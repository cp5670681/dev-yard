from __future__ import annotations

from pathlib import Path

from dev_yard import gitops, paths, status as st
from dev_yard.config import Repo, load_repos, save_repos
from dev_yard.atlassian import collect_requirement
from dev_yard.claude_fetch import claude_binary, run_claude_fetch
from dev_yard.env import load_env
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

Headings must be T + number (T1, T2, …) with a `- repo:` bullet.
CLI ignores any other heading.
"""


def init_yard(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    yml = paths.repos_yaml(root)
    if not yml.exists():
        yml.write_text("repos: {}\n")
    gi = root / ".gitignore"
    extra = [".repos/", ".yard-worktrees/", "reqs/", ".env"]
    existing = gi.read_text() if gi.exists() else ""
    lines = existing.splitlines()
    for line in extra:
        if line not in lines:
            lines.append(line)
    gi.write_text("\n".join(lines).rstrip() + "\n")
    example = root / ".env.example"
    if not example.exists():
        example.write_text(
            "JIRA_BASE_URL=\nJIRA_USERNAME=\nJIRA_PASSWORD=\nCONFLUENCE_BASE_URL=\n"
        )
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
    source = repo.source_path(root)
    if repo.path and not (source / ".git").exists():
        raise ValueError(f"{source} is not a git repo")
    repos[alias] = repo
    save_repos(root, repos)
    gitops.ensure_clone(repo.url, source)
    return repo


def req_open(
    root: Path,
    jira: str,
    *,
    source: str = "claude",
    dry_run: bool = False,
    force: bool = False,
) -> tuple[Path, str]:
    load_env(root)
    d = paths.req_dir(root, jira)
    if dry_run:
        if source == "claude":
            from dev_yard.claude_fetch import claude_argv

            return d, " ".join(claude_argv(d)) + "\n(stdin prompt)"
        if source == "http":
            return d, f"http fetch {jira} (no request)"
        return d, "skipped remote fetch"
    if d.exists() and (d / "STATUS.yaml").exists() and not force:
        phase = st.load(root, jira).get("phase") or "open"
        if phase != "open":
            raise ValueError(f"{jira} is already phase={phase}; pass --force to re-open")
    d.mkdir(parents=True, exist_ok=True)
    warning = ""
    if source == "claude":
        import shutil

        binary = claude_binary()
        if not shutil.which(binary) and not Path(binary).exists():
            raise FileNotFoundError(
                f"claude not found (`{binary}`). Install Claude Code or set YARD_CLAUDE."
            )
        assets = d / "assets"
        if assets.exists():
            shutil.rmtree(assets)
        result = run_claude_fetch(jira, d, dry_run=False)
        if not result.ok:
            raise RuntimeError(result.summary)
        if not (d / "REQUIREMENT.md").exists():
            (d / "REQUIREMENT.md").write_text(
                REQ_SKELETON.format(key=jira, title=jira, body="")
            )
            warning = "Claude did not write REQUIREMENT.md; wrote skeleton"
    elif source == "http":
        result = collect_requirement(d, jira, root)
        warning = "; ".join(result.warnings)
        (d / "REQUIREMENT.md").write_text(
            result.markdown or REQ_SKELETON.format(key=jira, title=jira, body="")
        )
    else:
        (d / "REQUIREMENT.md").write_text(REQ_SKELETON.format(key=jira, title=jira, body=""))
        warning = "skipped remote fetch"
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
        gitops.worktree_add(source, wt, f"req/{jira}", start, reset_existing=False)
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
    repo = repos.get(t.repo)
    if not repo:
        raise ValueError(f"unknown repo alias {t.repo}")
    source = repo.source_path(root)
    parent = paths.req_worktree(root, jira, t.repo)
    if not parent.exists():
        raise ValueError(f"missing requirement worktree {parent}; freeze first")
    child = paths.child_worktree(root, jira, t.repo, ticket_id)
    gitops.worktree_add(
        source, child, f"req/{jira}/{ticket_id}", f"req/{jira}", reset_existing=True
    )
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
    t = tix.get(ticket_id)
    if not t:
        raise ValueError(f"unknown ticket {ticket_id}")
    repos = load_repos(root)
    repo = repos.get(t.repo)
    if not repo:
        raise ValueError(f"unknown repo alias {t.repo}")
    source = repo.source_path(root)
    if child and parent:
        gitops.merge_into(Path(parent), f"req/{jira}/{ticket_id}")
        gitops.worktree_remove(source, Path(child))
        gitops.branch_delete(source, f"req/{jira}/{ticket_id}")
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


def ensure_on_default_base(root: Path) -> dict[str, Path]:
    """Managed `.repos/` clones are checked out; path-mapped working copies are not moved."""
    out: dict[str, Path] = {}
    for alias, repo in load_repos(root).items():
        source = repo.source_path(root)
        if repo.path:
            if not (source / ".git").exists():
                raise gitops.GitError(f"{alias}: {source} is not a git repo")
            branch = gitops.current_branch(source)
            if branch != repo.default_base:
                raise gitops.GitError(
                    f"{alias} at {source} is on {branch}, need {repo.default_base}; "
                    "checkout it yourself (dev-yard will not move a path-mapped clone)"
                )
            out[alias] = source
            continue
        gitops.ensure_clone(repo.url, source)
        gitops.checkout_default_base(source, repo.default_base)
        out[alias] = source
    return out


STAGE_PROTECT = {
    "grill": ("SPEC.md", "TICKETS.md"),
    "spec": ("TICKETS.md",),
    "tickets": ("REQUIREMENT.md", "GRILL.md", "SPEC.md", "STATUS.yaml"),
}


def _snapshot(req: Path, names: tuple[str, ...]) -> dict[str, bytes | None]:
    return {n: (req / n).read_bytes() if (req / n).exists() else None for n in names}


def _restore(req: Path, snap: dict[str, bytes | None]) -> list[str]:
    restored: list[str] = []
    for name, before in snap.items():
        p = req / name
        after = p.read_bytes() if p.exists() else None
        if after == before:
            continue
        if before is None:
            p.unlink()
        else:
            p.write_bytes(before)
        restored.append(name)
    return restored


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
        raise FileNotFoundError(f"missing {req}; run: dev-yard req open {jira}")
    extra = [req / "REQUIREMENT.md", req / "GRILL.md", req / "SPEC.md", req / "TICKETS.md"]
    bases = ""
    if name in {"grill", "spec", "tickets"} and not dry_run:
        mapping = ensure_on_default_base(root)
        repos = load_repos(root)
        lines = "\n".join(
            f"- {a}: {p}  (on {repos[a].default_base}"
            + ("; path-mapped, not moved" if repos[a].path else "")
            + ")"
            for a, p in mapping.items()
        )
        bases = (
            "Read application code from these source clones (on default_base). "
            "Do not switch their branches. Requirement worktrees are created later by freeze.\n"
            f"{lines}\n"
        )
    prompt = session_prompt(root, name, jira, extra=bases)
    r = runner or get_runner(root, name, dry_run=dry_run, print_mode=print_mode)
    snap = _snapshot(req, STAGE_PROTECT[name]) if name in STAGE_PROTECT and not dry_run else {}
    result = r.start(prompt, root, extra)
    restored = _restore(req, snap) if snap else []
    if restored:
        note = "restored (not this stage's job): " + ", ".join(restored)
        result = RunResult(ok=result.ok, summary=(result.summary + "\n" + note).strip(), exit_code=result.exit_code)
    return result


def _diff_vs_base(worktree: Path, default_base: str) -> str:
    try:
        base = gitops.start_point(worktree, default_base)
        return gitops.diff_against(worktree, base)
    except gitops.GitError as e:
        return f"(could not diff vs {default_base}: {e})"


def _review_blocked(result: RunResult) -> bool:
    if not result.ok:
        return True
    return "REVIEW_FAILED" in (result.summary or "")


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
    if not dry_run:
        st.save(root, jira, data)
    runner = runner or get_runner(root, "implement", dry_run=dry_run, print_mode=print_mode)
    targets = ids or st.ready_ids(data)
    ran: list[str] = []
    extra = [req / "SPEC.md", req / "TICKETS.md"]
    for tid in targets:
        t = tickets.get(tid)
        if not t:
            continue
        slot = data["tickets"].get(tid)
        if not slot:
            continue
        if slot.get("state") not in {"ready", "blocked", "implementing"} and ids is None:
            continue
        if dry_run:
            ran.append(tid)
            continue
        if _needs_child(data, t) and not slot.get("child_worktree"):
            ticket_start(root, jira, tid)
            data = st.load(root, jira)
            slot = data["tickets"][tid]
        cwd = _cwd_for_ticket(root, jira, t, slot)
        if not (cwd / ".git").exists():
            raise ValueError(f"missing worktree {cwd}; freeze first")
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
    data = st.sync_tickets(st.load(root, jira), list(tickets.values()))
    if not dry_run:
        st.save(root, jira, data)
    runner = runner or get_runner(root, "review", dry_run=dry_run, print_mode=print_mode)
    extra = [req / "SPEC.md", req / "TICKETS.md"]
    ran: list[str] = []
    if contract:
        aliases = list(data.get("repos") or [])
        if not aliases:
            raise ValueError("no repos in STATUS.yaml; freeze first")
        repos = load_repos(root)
        wt_lines: list[str] = []
        wt_paths: list[Path] = []
        diffs: list[str] = []
        for alias in aliases:
            wt = paths.req_worktree(root, jira, alias)
            if not wt.exists():
                raise ValueError(f"missing worktree {wt}; freeze first")
            wt_paths.append(wt)
            repo = repos.get(alias)
            base = repo.default_base if repo else "main"
            wt_lines.append(f"- {alias}: {wt}  (diff vs {base}, already inlined below)")
            diffs.append(f"### {alias}\n{_diff_vs_base(wt, base)}")
        listed = "\n".join(wt_lines)
        prompt = session_prompt(
            root,
            "review",
            jira,
            extra=(
                "Mode: --contract. Review every requirement worktree against SPEC.md contracts.\n"
                "Do not spawn sub-agents; pi has none. Do not git-diff the yard repo.\n"
                f"Worktrees:\n{listed}\n\n"
                + "\n\n".join(diffs)
            ),
        )
        result = runner.start(prompt, root, extra + wt_paths)
        if dry_run:
            return ["__contract__"]
        blocked = _review_blocked(result)
        data["contract_review"] = "failed" if blocked else "passed"
        data["contract_summary"] = result.summary
        if not blocked and st.all_done(data):
            data["phase"] = "done"
        st.save(root, jira, data)
        return ["__contract__"]

    targets = ids or [
        tid
        for tid, s in (data.get("tickets") or {}).items()
        if s.get("state") in {"implemented", "reviewing"}
    ]
    repos = load_repos(root)
    for tid in targets:
        t = tickets.get(tid)
        if not t:
            continue
        slot = data["tickets"].get(tid)
        if not slot:
            continue
        cwd = _cwd_for_ticket(root, jira, t, slot)
        if dry_run:
            ran.append(tid)
            continue
        if not (cwd / ".git").exists():
            raise ValueError(f"missing worktree {cwd}; freeze first")
        slot["state"] = "reviewing"
        st.save(root, jira, data)
        base = repos[t.repo].default_base if t.repo in repos else "main"
        prompt = session_prompt(
            root,
            "review",
            jira,
            extra=(
                f"Ticket: {tid} — {t.title}\nRepo alias: {t.repo}\n"
                f"Diff vs {base}:\n{_diff_vs_base(cwd, base)}"
            ),
        )
        result = runner.start(prompt, cwd, extra)
        data = st.load(root, jira)
        slot = data["tickets"][tid]
        if not _review_blocked(result):
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
