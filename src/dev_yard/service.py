from __future__ import annotations

from pathlib import Path

from dev_yard import gitops, paths, status as st
from dev_yard.config import Repo, load_repos, save_repos
from dev_yard.jira import fetch_issue
from dev_yard.runners import RunResult, Runner, get_runner
from dev_yard.skillbind import session_prompt
from dev_yard.tickets import Ticket, load_tickets

REQ_SKELETON = """# {key}

{title}

{body}
"""

GRILL_SKELETON = """# Grill — {key}

Work in this directory. Read REQUIREMENT.md and source clones on `default_base`.
Record Q/A and decisions here. Do not implement in requirement worktrees until freeze.
"""

SPEC_SKELETON = """# Spec — {key}

## Goals

## Non-goals

## Per-repo

## Contracts (APIs / events / fields)
"""

TICKETS_SKELETON = """# Tickets — {key}

Each ticket binds to one `repo` alias from repos.yaml.

## T1: example
- repo: backend
- depends_on:
- parallel: false
"""


def init_yard(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    yml = paths.repos_yaml(root)
    if not yml.exists():
        yml.write_text("repos: {}\n")
    gi = root / ".gitignore"
    extra = "\n".join([".repos/", ".yard-worktrees/", "reqs/*/worktrees/", ""])
    existing = gi.read_text() if gi.exists() else ""
    if ".repos/" not in existing:
        gi.write_text(existing + extra)
    paths.repos_dir(root).mkdir(exist_ok=True)
    paths.reqs_dir(root).mkdir(exist_ok=True)


def repo_add(root: Path, alias: str, url: str, default_base: str, role: str, path: str | None) -> Repo:
    repos = load_repos(root)
    repo = Repo(
        alias=alias,
        url=url,
        default_base=default_base,
        role=role,
        path=Path(path) if path else None,
    )
    repos[alias] = repo
    save_repos(root, repos)
    gitops.ensure_clone(repo.url, repo.source_path(root))
    return repo


def req_open(root: Path, jira: str) -> tuple[Path, str]:
    d = paths.req_dir(root, jira)
    d.mkdir(parents=True, exist_ok=True)
    warning = ""
    fetched = fetch_issue(jira)
    if fetched:
        title, body = fetched
        note = f"{title}\n\n{body}"
    else:
        title, note = jira, ""
        warning = "Jira fetch skipped or failed; wrote skeleton REQUIREMENT.md"
    (d / "REQUIREMENT.md").write_text(REQ_SKELETON.format(key=jira, title=title, body=note))
    if not (d / "GRILL.md").exists():
        (d / "GRILL.md").write_text(GRILL_SKELETON.format(key=jira))
    if not (d / "SPEC.md").exists():
        (d / "SPEC.md").write_text(SPEC_SKELETON.format(key=jira))
    if not (d / "TICKETS.md").exists():
        (d / "TICKETS.md").write_text(TICKETS_SKELETON.format(key=jira))
    data = st.load(root, jira)
    data["phase"] = "open"
    st.save(root, jira, data)
    return d, warning


def req_freeze(root: Path, jira: str) -> list[Path]:
    req = paths.req_dir(root, jira)
    tickets = load_tickets(req)
    if not tickets or not any(t.repo for t in tickets):
        raise ValueError("TICKETS.md has no tickets with a repo; finish to-tickets first")
    repos = load_repos(root)
    data = st.sync_tickets(st.load(root, jira), tickets)
    created: list[Path] = []
    aliases = sorted({t.repo for t in tickets if t.repo})
    for alias in aliases:
        repo = repos.get(alias)
        if not repo:
            raise ValueError(f"unknown repo alias {alias}")
        source = repo.source_path(root)
        gitops.ensure_clone(repo.url, source)
        gitops.fetch(source)
        wt = paths.req_worktree(root, jira, alias)
        start = gitops.start_point(source, repo.default_base)
        gitops.worktree_add(source, wt, f"req/{jira}", start)
        created.append(wt)
        for tid, slot in data["tickets"].items():
            if slot.get("repo") == alias:
                slot["worktree"] = str(wt)
    data["phase"] = "frozen"
    st.refresh_ready(data)
    st.save(root, jira, data)
    return created


def ticket_start(root: Path, jira: str, ticket_id: str) -> Path:
    req = paths.req_dir(root, jira)
    tickets = {t.id: t for t in load_tickets(req)}
    t = tickets.get(ticket_id)
    if not t:
        raise ValueError(f"unknown ticket {ticket_id}")
    repos = load_repos(root)
    repo = repos[t.repo]
    source = repo.source_path(root)
    parent = paths.req_worktree(root, jira, t.repo)
    child = paths.child_worktree(root, jira, t.repo, ticket_id)
    gitops.worktree_add(source, child, f"req/{jira}/{ticket_id}", f"req/{jira}")
    data = st.load(root, jira)
    slot = data.setdefault("tickets", {}).setdefault(ticket_id, {})
    slot["child_worktree"] = str(child)
    slot["worktree"] = str(parent)
    st.save(root, jira, data)
    return child


def ticket_done(root: Path, jira: str, ticket_id: str) -> None:
    data = st.load(root, jira)
    slot = (data.get("tickets") or {}).get(ticket_id) or {}
    child = slot.get("child_worktree")
    parent = slot.get("worktree")
    tix = {t.id: t for t in load_tickets(paths.req_dir(root, jira))}
    t = tix[ticket_id]
    repos = load_repos(root)
    source = repos[t.repo].source_path(root)
    if child and parent:
        gitops.merge_into(Path(parent), f"req/{jira}/{ticket_id}")
        gitops.worktree_remove(source, Path(child))
        slot["child_worktree"] = None
    slot["state"] = "done"
    st.refresh_ready(data)
    st.save(root, jira, data)


def _cwd_for_ticket(root: Path, jira: str, ticket: Ticket, slot: dict) -> Path:
    child = slot.get("child_worktree")
    if child:
        return Path(child)
    parent = slot.get("worktree") or str(paths.req_worktree(root, jira, ticket.repo))
    return Path(parent)


def _needs_child(data: dict, ticket: Ticket) -> bool:
    if not ticket.parallel:
        return False
    for tid, slot in (data.get("tickets") or {}).items():
        if tid == ticket.id:
            continue
        if slot.get("repo") != ticket.repo:
            continue
        if slot.get("state") in {"implementing", "implemented", "reviewing"}:
            return True
        if slot.get("child_worktree"):
            return True
    return False


def launch_skill(
    root: Path,
    name: str,
    jira: str,
    dry_run: bool = False,
    print_mode: bool = False,
    runner: Runner | None = None,
) -> RunResult:
    req = paths.req_dir(root, jira)
    if not req.exists():
        raise FileNotFoundError(f"missing {req}; run: yard req open {jira}")
    extra = [req / "REQUIREMENT.md", req / "GRILL.md", req / "SPEC.md", req / "TICKETS.md"]
    prompt = session_prompt(root, name, jira)
    r = runner or get_runner(root, name, dry_run=dry_run, print_mode=print_mode)
    return r.start(prompt, root, extra)


def implement(
    root: Path,
    jira: str,
    ids: list[str] | None,
    dry_run: bool = False,
    print_mode: bool = False,
    runner: Runner | None = None,
) -> list[str]:
    req = paths.req_dir(root, jira)
    tickets = {t.id: t for t in load_tickets(req)}
    data = st.sync_tickets(st.load(root, jira), list(tickets.values()))
    st.refresh_ready(data)
    runner = runner or get_runner(root, "implement", dry_run=dry_run, print_mode=print_mode)
    targets = ids or st.ready_ids(data)
    ran: list[str] = []
    extra = [req / "SPEC.md", req / "TICKETS.md"]
    for tid in targets:
        t = tickets[tid]
        slot = data["tickets"][tid]
        if slot.get("state") not in {"ready", "blocked"} and ids is None:
            continue
        if _needs_child(data, t) and not slot.get("child_worktree"):
            ticket_start(root, jira, tid)
            data = st.load(root, jira)
            slot = data["tickets"][tid]
        cwd = _cwd_for_ticket(root, jira, t, slot)
        slot["state"] = "implementing"
        st.save(root, jira, data)
        prompt = session_prompt(
            root,
            "implement",
            jira,
            extra=f"Ticket: {tid} — {t.title}\nRepo alias: {t.repo}\nStay in this worktree.",
        )
        result = runner.start(prompt, cwd, extra)
        slot = st.load(root, jira)["tickets"][tid]
        data = st.load(root, jira)
        slot = data["tickets"][tid]
        if result.ok:
            slot["state"] = "implemented"
            slot["last_summary"] = result.summary
        else:
            slot["state"] = "blocked"
            slot["last_summary"] = result.summary
        st.save(root, jira, data)
        ran.append(tid)
    return ran


def review(
    root: Path,
    jira: str,
    ids: list[str] | None,
    contract: bool = False,
    dry_run: bool = False,
    print_mode: bool = False,
    runner: Runner | None = None,
) -> list[str]:
    req = paths.req_dir(root, jira)
    tickets = {t.id: t for t in load_tickets(req)}
    data = st.load(root, jira)
    runner = runner or get_runner(root, "review", dry_run=dry_run, print_mode=print_mode)
    extra = [req / "SPEC.md", req / "TICKETS.md"]
    ran: list[str] = []
    if contract:
        cwd = req
        prompt = session_prompt(
            root,
            "review",
            jira,
            extra="Mode: --contract. Review every requirement worktree against SPEC.md contracts.",
        )
        result = runner.start(prompt, cwd, extra + [paths.req_worktree(root, jira, a) for a in data.get("repos") or []])
        data["contract_review"] = "passed" if result.ok else "failed"
        data["contract_summary"] = result.summary
        if result.ok and st.all_done(data):
            data["phase"] = "done"
        st.save(root, jira, data)
        return ["__contract__"]

    targets = ids or [
        tid for tid, s in (data.get("tickets") or {}).items() if s.get("state") == "implemented"
    ]
    for tid in targets:
        t = tickets[tid]
        slot = data["tickets"][tid]
        cwd = _cwd_for_ticket(root, jira, t, slot)
        slot["state"] = "reviewing"
        st.save(root, jira, data)
        prompt = session_prompt(
            root,
            "review",
            jira,
            extra=f"Ticket: {tid} — {t.title}\nRepo alias: {t.repo}",
        )
        result = runner.start(prompt, cwd, extra)
        data = st.load(root, jira)
        slot = data["tickets"][tid]
        if result.ok:
            slot["state"] = "done"
            child = slot.get("child_worktree")
            st.save(root, jira, data)
            if child:
                ticket_done(root, jira, tid)
            data = st.load(root, jira)
            slot = data["tickets"][tid]
            st.refresh_ready(data)
        else:
            slot["state"] = "blocked"
        slot["last_summary"] = result.summary
        st.save(root, jira, data)
        ran.append(tid)
    return ran


def status_text(root: Path, jira: str | None) -> str:
    if jira:
        keys = [jira]
    else:
        rd = paths.reqs_dir(root)
        keys = [p.name for p in rd.iterdir() if p.is_dir()] if rd.exists() else []
    lines: list[str] = []
    for key in sorted(keys):
        data = st.load(root, key)
        lines.append(f"{key}  phase={data.get('phase')}")
        for tid, slot in (data.get("tickets") or {}).items():
            child = slot.get("child_worktree") or "-"
            lines.append(f"  {tid}  {slot.get('state')}  repo={slot.get('repo')}  child={child}")
        if data.get("contract_review"):
            lines.append(f"  contract={data.get('contract_review')}")
    return "\n".join(lines) if lines else "(no requirements)"
