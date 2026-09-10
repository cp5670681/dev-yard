from __future__ import annotations

import re
import shutil
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from dev_yard import gitops, paths, status as st
from dev_yard.config import Repo, git_project_name, load_repos, require_pair, save_repos
from dev_yard.atlassian import collect_requirement
from dev_yard.env import load_env
from dev_yard.runners import RunResult, Runner, agent_binary, get_runner, pi_argv
from dev_yard.skillbind import session_prompt
from dev_yard.tickets import Ticket, load_tickets

REQ_SKELETON = """# {key}

{title}

{body}
"""

GRILL_SKELETON = """# Grill — {key}

Work in this directory. Read REQUIREMENT.md and source clones on `default_base`.
Record Q/A and decisions here. Settled terms go in `reqs/CONTEXT.md`; ADRs in `reqs/docs/adr/`.
Do not copy those into business repos. Do not implement in requirement worktrees until freeze.
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
    extra = [".repos/", ".yard-worktrees/", "reqs/", ".env", "repos.yaml"]
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
            "YARD_TEST_REPORT_TOKEN=\n"
        )
    paths.repos_dir(root).mkdir(exist_ok=True)
    paths.reqs_dir(root).mkdir(exist_ok=True)


def repo_add(
    root: Path,
    alias: str,
    url: str,
    default_base: str,
    role: str,
    path: str | None,
    on_progress: gitops.Progress | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> Repo:
    repos = load_repos(root)
    alias = (alias or "").strip() or git_project_name(url)
    pair = require_pair(provider, model)
    repo = Repo(
        alias=alias,
        url=url,
        default_base=default_base,
        role=role,
        path=Path(path) if path else None,
        provider=pair[0] if pair else None,
        model=pair[1] if pair else None,
    )
    source = repo.source_path(root)
    if repo.path and not (source / ".git").exists():
        raise ValueError(f"{source} is not a git repo")
    repos[alias] = repo
    save_repos(root, repos)
    gitops.ensure_clone(repo.url, source, on_progress=on_progress)
    return repo


def repo_set_pi(
    root: Path,
    alias: str,
    provider: str | None,
    model: str | None,
) -> Repo:
    repos = load_repos(root)
    repo = repos.get(alias)
    if repo is None:
        raise ValueError(f"unknown repo {alias}")
    pair = require_pair(provider, model)
    repo.provider = pair[0] if pair else None
    repo.model = pair[1] if pair else None
    save_repos(root, repos)
    return repo


def extract_req_key(target: str) -> str:
    """Extract or sanitize a requirement key from a URL, issue ID, or description."""
    s = (target or "").strip()
    if not s:
        return ""
    # 1. Plain identifier (alphanumeric with underscores, hyphens, dots)
    if not (s.startswith("http://") or s.startswith("https://") or "/" in s or "\\" in s):
        return s

    # 2. Jira issue URL patterns (/browse/KEY-123, /issues/KEY-123, ?selectedIssue=KEY-123)
    m = re.search(r"/(?:browse|issues|projects/[^/]+/issues)/([A-Za-z][A-Za-z0-9]+-\d+)", s)
    if m:
        return m.group(1).upper()
    m = re.search(r"[?&]selectedIssue=([A-Za-z][A-Za-z0-9]+-\d+)", s)
    if m:
        return m.group(1).upper()

    # 3. GitHub issues / pull requests: github.com/owner/repo/(issues|pull)/123
    m = re.search(r"github\.com/[^/]+/([^/]+)/(?:issues|pull)/(\d+)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # 4. GitLab issues / MRs: gitlab.com/.../repo/-/(issues|merge_requests)/123
    m = re.search(r"/([^/]+)/-/(?:issues|merge_requests)/(\d+)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # 5. Confluence pageId: pageId=123456
    m = re.search(r"[?&]pageId=(\d+)", s)
    if m:
        return f"CONF-{m.group(1)}"

    # 6. Feishu / Lark doc: feishu.cn/docx/xyz or feishu.cn/wiki/xyz
    m = re.search(r"(?:feishu|larksuite)\.cn/(?:docx|wiki|docs)/([A-Za-z0-9]+)", s)
    if m:
        return f"FEISHU-{m.group(1)[:12]}"

    # 7. General URL: extract sanitized last path segment
    try:
        parsed = urllib.parse.urlparse(s)
        if parsed.scheme in ("http", "https"):
            path = parsed.path.rstrip("/")
            if path:
                last_segment = path.split("/")[-1]
                cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", last_segment).strip("-.")
                if cleaned:
                    return cleaned
    except Exception:
        pass

    # 8. Fallback sanitize
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-.")
    return cleaned or "REQ"


def req_open(
    root: Path,
    jira: str,
    source: str = "pi",
    target: str | None = None,
    payload: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    on_progress: Callable[[str], None] | None = None,
    runner: Runner | None = None,
) -> tuple[Path, str]:
    load_env(root)
    actual_target = (target or jira).strip()
    req_key = (
        jira
        if not (jira.startswith("http://") or jira.startswith("https://") or "/" in jira or "\\" in jira)
        else ""
    ) or extract_req_key(actual_target)
    if not req_key:
        req_key = "REQ"

    d = paths.req_dir(root, req_key)
    if dry_run:
        if source == "pi":
            return d, " ".join(
                pi_argv(root=root, bundle="open", prompt="(dry-run)", print_mode=True)
            )
        if source == "http":
            return d, f"http fetch {req_key} (no request)"
        if source == "text":
            return d, f"write text requirement for {req_key} (dry-run)"
        if source == "file":
            return d, f"read file {payload or actual_target} for {req_key} (dry-run)"
        return d, "skipped remote fetch"
    if d.exists() and (d / "STATUS.yaml").exists() and not force:
        phase = st.load(root, req_key).get("phase") or "open"
        if phase != "open":
            raise ValueError(f"{req_key} is already phase={phase}; pass --force to re-open")
    d.mkdir(parents=True, exist_ok=True)
    warning = ""
    if source == "pi":
        import shutil

        if runner is None:
            binary = agent_binary()
            if not shutil.which(binary) and not Path(binary).exists():
                raise FileNotFoundError(
                    f"pi not found (`{binary}`). Install pi or set YARD_PI to its path."
                )
        assets = d / "assets"
        saved_assets = _stash_tree(assets)
        if assets.exists():
            shutil.rmtree(assets)
        snap = _snapshot(d, STAGE_PROTECT["open"])
        r = runner or get_runner(root, "open", print_mode=True)
        prompt = session_prompt(root, "open", req_key, target=actual_target)
        result = r.start(prompt, root, [d])
        restored = _restore(d, snap)
        if on_progress and result.summary:
            on_progress(result.summary)
        if restored:
            warning = "restored (not this stage's job): " + ", ".join(restored)
        if not result.ok:
            if saved_assets is not None:
                _unstash_tree(assets, saved_assets)
            raise RuntimeError(result.summary)
        req_md = d / "REQUIREMENT.md"
        skeleton = REQ_SKELETON.format(key=req_key, title=req_key, body="")
        if not req_md.exists() or req_md.read_text() == skeleton:
            req_md.write_text(skeleton)
            warning = "pi did not write REQUIREMENT.md; wrote skeleton"
    elif source == "text":
        content = (payload if payload is not None else actual_target) or ""
        if not content.strip():
            content = REQ_SKELETON.format(key=req_key, title=req_key, body="")
        (d / "REQUIREMENT.md").write_text(content)
    elif source == "file":
        src_file = Path(payload or actual_target).expanduser().resolve()
        if not src_file.exists():
            raise FileNotFoundError(f"Source file not found: {src_file}")
        content = src_file.read_text(encoding="utf-8")
        (d / "REQUIREMENT.md").write_text(content)
    elif source == "http":
        result = collect_requirement(d, req_key, root)
        warning = "; ".join(result.warnings)
        (d / "REQUIREMENT.md").write_text(
            result.markdown or REQ_SKELETON.format(key=req_key, title=req_key, body="")
        )
    else:
        (d / "REQUIREMENT.md").write_text(REQ_SKELETON.format(key=req_key, title=req_key, body=""))
        warning = "skipped remote fetch"
    if not (d / "GRILL.md").exists():
        (d / "GRILL.md").write_text(GRILL_SKELETON.format(key=req_key))
    if not (d / "SPEC.md").exists():
        (d / "SPEC.md").write_text(SPEC_SKELETON.format(key=req_key))
    if not (d / "TICKETS.md").exists():
        (d / "TICKETS.md").write_text(TICKETS_SKELETON.format(key=req_key))
    data = st.load(root, req_key)
    data["phase"] = "open"
    st.save(root, req_key, data)
    if warning == "pi did not write REQUIREMENT.md; wrote skeleton":
        raise RuntimeError(warning)
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


def req_delete(root: Path, jira: str) -> None:
    """Remove this Jira's docs, worktrees, and local branches. Shared glossary/ADR stay."""
    d = paths.req_dir(root, jira)
    if not d.exists() or not paths.is_req_dir(d):
        raise FileNotFoundError(f"no requirement {jira}")
    with st.jira_lock(jira):
        _req_delete_locked(root, jira, d)


def _req_delete_locked(root: Path, jira: str, d: Path) -> None:
    repos = load_repos(root)
    data = st.load(root, jira) if (d / "STATUS.yaml").is_file() else {"tickets": {}}
    tickets = st.tickets_map(data.get("tickets"))

    for tid, slot in tickets.items():
        child = slot.get("child_worktree")
        alias = slot.get("repo")
        repo = repos.get(alias) if alias else None
        if child and repo:
            gitops.worktree_remove(repo.source_path(root), Path(child))
            gitops.branch_delete(repo.source_path(root), _child_branch(jira, tid))

    child_root = root / ".yard-worktrees" / jira
    if child_root.is_dir():
        for alias_dir in child_root.iterdir():
            if not alias_dir.is_dir():
                continue
            repo = repos.get(alias_dir.name)
            for ticket_dir in alias_dir.iterdir():
                if not ticket_dir.is_dir() or not repo:
                    continue
                gitops.worktree_remove(repo.source_path(root), ticket_dir)
                gitops.branch_delete(
                    repo.source_path(root), _child_branch(jira, ticket_dir.name)
                )
        shutil.rmtree(child_root, ignore_errors=True)

    aliases: set[str] = set()
    wt_root = d / "worktrees"
    if wt_root.is_dir():
        aliases.update(p.name for p in wt_root.iterdir() if p.is_dir())
    for slot in tickets.values():
        if slot.get("repo"):
            aliases.add(str(slot["repo"]))
    for alias in aliases:
        repo = repos.get(alias)
        if not repo:
            continue
        source = repo.source_path(root)
        gitops.worktree_remove(source, paths.req_worktree(root, jira, alias))
        gitops.branch_delete(source, f"req/{jira}")

    shutil.rmtree(d)


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
        source, child, _child_branch(jira, ticket_id), f"req/{jira}", reset_existing=True
    )
    with st.jira_lock(jira):
        data = st.load(root, jira)
        slot = data.setdefault("tickets", {}).setdefault(ticket_id, {})
        slot["child_worktree"] = str(child)
        slot["worktree"] = str(parent)
        st.save(root, jira, data)
    return child


def ticket_done(root: Path, jira: str, ticket_id: str) -> None:
    with st.jira_lock(jira):
        data = st.load(root, jira)
        parent = ((data.get("tickets") or {}).get(ticket_id) or {}).get("worktree")
        try:
            _ticket_done_locked(root, jira, ticket_id)
        except gitops.GitError:
            if parent:
                gitops.merge_abort(Path(parent))
            raise


def _ticket_done_locked(
    root: Path, jira: str, ticket_id: str, summary: str | None = None
) -> None:
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
        gitops.merge_into(Path(parent), _child_branch(jira, ticket_id))
        gitops.worktree_remove(source, Path(child))
        gitops.branch_delete(source, _child_branch(jira, ticket_id))
        slot["child_worktree"] = None
    if summary is not None:
        slot["last_summary"] = summary
    slot["state"] = "done"
    st.refresh_ready(data)
    st.save(root, jira, data)


def _child_branch(jira: str, ticket_id: str) -> str:
    # Cannot be req/<jira>/<ticket>: git refuses a nested ref when req/<jira> exists.
    return f"req/{jira}-{ticket_id}"


def _cwd_for_ticket(root: Path, jira: str, ticket: Ticket, slot: dict) -> Path:
    child = slot.get("child_worktree")
    if child:
        return Path(child)
    parent = slot.get("worktree") or str(paths.req_worktree(root, jira, ticket.repo))
    return Path(parent)


def _needs_child(data: dict, ticket: Ticket) -> bool:
    sibling_inflight = False
    sibling_mid = False
    for tid, slot in (data.get("tickets") or {}).items():
        if tid == ticket.id:
            continue
        if slot.get("repo") != ticket.repo:
            continue
        if slot.get("state") == "implementing" or slot.get("child_worktree"):
            sibling_inflight = True
        if slot.get("state") in {"implementing", "implemented", "reviewing"}:
            sibling_mid = True
    if sibling_inflight:
        return True
    return bool(ticket.parallel and sibling_mid)


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
    "open": ("GRILL.md", "SPEC.md", "TICKETS.md", "STATUS.yaml"),
    "grill": ("SPEC.md", "TICKETS.md"),
    "spec": ("TICKETS.md",),
    "tickets": ("REQUIREMENT.md", "GRILL.md", "SPEC.md", "STATUS.yaml"),
}


def _snapshot(req: Path, names: tuple[str, ...]) -> dict[str, bytes | None]:
    return {n: (req / n).read_bytes() if (req / n).exists() else None for n in names}


def _stash_tree(path: Path) -> dict[str, bytes] | None:
    if not path.is_dir():
        return None
    return {
        str(p.relative_to(path)): p.read_bytes()
        for p in path.rglob("*")
        if p.is_file()
    }


def _unstash_tree(path: Path, files: dict[str, bytes]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        dest = path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


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
    prompt_extra: str = "",
) -> RunResult:
    req = paths.req_dir(root, jira)
    if not req.exists():
        raise FileNotFoundError(f"missing {req}; run: dev-yard req open {jira}")
    extra = [
        req / "REQUIREMENT.md",
        req / "GRILL.md",
        req / "SPEC.md",
        req / "TICKETS.md",
        paths.context_md(root),
        paths.adr_dir(root),
    ]
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
    if prompt_extra:
        bases = (bases + "\n" + prompt_extra).strip() if bases else prompt_extra
    prompt = session_prompt(root, name, jira, extra=bases)
    r = runner or get_runner(root, name, dry_run=dry_run, print_mode=print_mode)
    snap = _snapshot(req, STAGE_PROTECT[name]) if name in STAGE_PROTECT and not dry_run else {}
    result = r.start(prompt, root, extra)
    restored = _restore(req, snap) if snap else []
    if restored:
        note = "restored (not this stage's job): " + ", ".join(restored)
        result = RunResult(ok=result.ok, summary=(result.summary + "\n" + note).strip(), exit_code=result.exit_code)
    return result


def _diff_vs_base(worktree: Path, default_base: str, since: str | None = None) -> str:
    try:
        base = since or gitops.start_point(worktree, default_base)
        return gitops.diff_against(worktree, base)
    except gitops.GitError as e:
        return f"(could not diff vs {since or default_base}: {e})"


def _previous_head_sha(data: dict, parsed: list, tid: str, repo: str) -> str | None:
    prev: str | None = None
    for t in parsed:
        if t.id == tid:
            break
        if getattr(t, "repo", None) != repo:
            continue
        sha = ((data.get("tickets") or {}).get(t.id) or {}).get("head_sha")
        if sha:
            prev = str(sha)
    return prev


def _record_head_sha(slot: dict, cwd: Path) -> None:
    try:
        slot["head_sha"] = gitops.run(["git", "rev-parse", "HEAD"], cwd=cwd)
    except gitops.GitError:
        return


def _review_blocked(result: RunResult) -> bool:
    if not result.ok:
        return True
    return "REVIEW_FAILED" in (result.summary or "")


_CLAIM = {
    "implement": ("implementing", {"ready", "blocked", "implementing"}),
    "review": ("reviewing", {"implemented", "reviewing", "blocked"}),
    "fix-contract": (
        "implementing",
        {"ready", "blocked", "implementing", "implemented", "reviewing", "done"},
    ),
    "fix-test": (
        "implementing",
        {"ready", "blocked", "implementing", "implemented", "reviewing", "done"},
    ),
}

_FROM_CONTRACT_STATES = {
    "ready",
    "blocked",
    "implementing",
    "implemented",
    "reviewing",
    "done",
}


def _implement_prompt_extra(
    tid: str,
    title: str,
    repo: str,
    last_summary: str | None,
    contract_summary: str | None = None,
    test_report: str | None = None,
) -> str:
    extra = f"Ticket: {tid} — {title}\nRepo alias: {repo}\nStay in this worktree."
    if last_summary and ("REVIEW_FAILED" in last_summary or "[Human Review" in last_summary):
        extra += (
            "\n\nPrevious review failed / feedback provided. Fix hard violations, Spec gaps, and review feedback "
            "in this report; do not expand scope; optional smells may stay.\n"
            f"{last_summary}"
        )
    if contract_summary:
        extra += (
            "\n\nPrevious contract review. Fix only Spec contract gaps and hard "
            "violations in this report that belong to this ticket's repo; do not "
            "expand scope; optional smells may stay.\n"
            f"{contract_summary}"
        )
    if test_report:
        extra += (
            "\n\nPrevious test report. Fix only failed findings in this report "
            "that belong to this ticket's repo; do not expand scope.\n"
            f"{test_report}"
        )
    return extra


def from_contract_ids(
    tickets: dict[str, object],
    ids: list[str] | None,
) -> list[str]:
    """Default: last ticket per repo (document order). Explicit ids keep order."""
    if ids:
        return [tid for tid in ids if tid in tickets]
    by_repo: dict[str, str] = {}
    for tid, t in tickets.items():
        repo = getattr(t, "repo", None)
        if repo:
            by_repo[repo] = tid
    return list(by_repo.values())


def claim_run(root: Path, jira: str, action: str, ids: list[str]) -> list[str]:
    spec = _CLAIM.get(action)
    if spec is None or not ids:
        return []
    state, allowed = spec
    parsed = load_tickets(paths.req_dir(root, jira))
    claimed: list[str] = []
    with st.jira_lock(jira):
        data = st.sync_tickets(st.load(root, jira), parsed)
        st.refresh_ready(data)
        tickets = data["tickets"]
        for tid in ids:
            slot = tickets.get(tid)
            if not slot:
                continue
            if slot.get("state") not in allowed:
                continue
            slot["state"] = state
            claimed.append(tid)
        st.save(root, jira, data)
    return claimed


def implement(
    root: Path,
    jira: str,
    ids: list[str] | None,
    dry_run: bool = False,
    print_mode: bool = False,
    runner: Runner | None = None,
    from_contract: bool = False,
    from_test: bool = False,
) -> list[str]:
    if from_contract and from_test:
        raise ValueError("from_contract and from_test are mutually exclusive")
    from dev_yard.test_report import from_test_ids, latest_report_path

    req = paths.req_dir(root, jira)
    tickets = {t.id: t for t in load_tickets(req)}
    test_body = ""
    with st.jira_lock(jira):
        data = st.sync_tickets(st.load(root, jira), list(tickets.values()))
        st.refresh_ready(data)
        if not dry_run:
            st.save(root, jira, data)
        contract_summary = (data.get("contract_summary") or "").strip()
        test_slot = data.get("test") if isinstance(data.get("test"), dict) else {}
        if from_contract:
            if not contract_summary:
                raise ValueError("no contract_summary; run review --contract first")
            targets = from_contract_ids(tickets, ids)
        elif from_test:
            if (test_slot or {}).get("latest_verdict") != "failed":
                raise ValueError(
                    "no failed test report; submit-test and accept a failed report first"
                )
            report_path = latest_report_path(root, jira)
            if not report_path.is_file():
                raise ValueError(f"missing {report_path}")
            test_body = report_path.read_text()
            targets = from_test_ids(tickets, ids, test_slot.get("findings") or [])
        else:
            targets = ids or st.ready_ids(data)
    runner = runner or get_runner(root, "implement", dry_run=dry_run, print_mode=print_mode)
    ran: list[str] = []
    extra = [req / "SPEC.md", req / "TICKETS.md"]
    allowed = (
        _FROM_CONTRACT_STATES
        if from_contract or from_test
        else {"ready", "blocked", "implementing"}
    )
    skip_state_check = ids is not None and not from_contract and not from_test
    for tid in targets:
        t = tickets.get(tid)
        if not t:
            continue
        if dry_run:
            slot = data["tickets"].get(tid)
            if not slot:
                continue
            if not skip_state_check and slot.get("state") not in allowed:
                continue
            ran.append(tid)
            continue
        with st.jira_lock(jira):
            data = st.sync_tickets(st.load(root, jira), list(tickets.values()))
            st.refresh_ready(data)
            slot = data["tickets"].get(tid)
            if not slot:
                continue
            if not skip_state_check and slot.get("state") not in allowed:
                continue
            last_summary = slot.get("last_summary")
            slot["state"] = "implementing"
            if from_contract and data.get("phase") == "done":
                data["phase"] = "frozen"
            if from_test and data.get("phase") == "testing":
                data["phase"] = "frozen"
                ts = data.get("test") if isinstance(data.get("test"), dict) else {}
                ts["status"] = "fixing"
                data["test"] = ts
            st.save(root, jira, data)
            if _needs_child(data, t) and not slot.get("child_worktree"):
                ticket_start(root, jira, tid)
                data = st.load(root, jira)
                slot = data["tickets"][tid]
            cwd = _cwd_for_ticket(root, jira, t, slot)
            if not (cwd / ".git").exists():
                raise ValueError(f"missing worktree {cwd}; freeze first")
        prompt = session_prompt(
            root,
            "implement",
            jira,
            extra=_implement_prompt_extra(
                tid,
                t.title,
                t.repo,
                last_summary,
                contract_summary if from_contract else None,
                test_body if from_test else None,
            ),
        )
        result = runner.start(prompt, cwd, extra, repo=t.repo)
        with st.jira_lock(jira):
            data = st.load(root, jira)
            slot = data["tickets"][tid]
            if result.ok:
                slot["state"] = "implemented"
                slot["last_summary"] = result.summary
                _record_head_sha(slot, cwd)
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
        st.save(root, jira, data)
        return ["__contract__"]

    _REVIEWABLE = {"implemented", "reviewing", "blocked"}
    parsed = list(tickets.values())
    targets = ids or [
        tid
        for tid, s in (data.get("tickets") or {}).items()
        if s.get("state") in _REVIEWABLE
    ]
    repos = load_repos(root)
    for tid in targets:
        t = tickets.get(tid)
        if not t:
            continue
        if dry_run:
            slot = data["tickets"].get(tid)
            if not slot or slot.get("state") not in _REVIEWABLE:
                continue
            ran.append(tid)
            continue
        with st.jira_lock(jira):
            data = st.load(root, jira)
            slot = data["tickets"].get(tid)
            if not slot or slot.get("state") not in _REVIEWABLE:
                continue
            cwd = _cwd_for_ticket(root, jira, t, slot)
            if not (cwd / ".git").exists():
                raise ValueError(f"missing worktree {cwd}; freeze first")
            since = _previous_head_sha(data, parsed, tid, t.repo)
            slot["state"] = "reviewing"
            st.save(root, jira, data)
        base = repos[t.repo].default_base if t.repo in repos else "main"
        label = since or base
        prompt = session_prompt(
            root,
            "review",
            jira,
            extra=(
                f"Ticket: {tid} — {t.title}\nRepo alias: {t.repo}\n"
                f"Diff vs {label} (this ticket only; working tree included):\n"
                f"{_diff_vs_base(cwd, base, since)}"
            ),
        )
        result = runner.start(prompt, cwd, extra)
        with st.jira_lock(jira):
            data = st.load(root, jira)
            slot = data["tickets"][tid]
            if not _review_blocked(result):
                child = slot.get("child_worktree")
                slot["last_summary"] = result.summary
                if child:
                    # Merge the child branch into the parent before persisting
                    # `done`; a conflicted merge must not strand a half-done state.
                    parent = slot.get("worktree")
                    try:
                        _ticket_done_locked(root, jira, tid, summary=result.summary)
                    except gitops.GitError as e:
                        if parent:
                            gitops.merge_abort(Path(parent))
                        slot = data["tickets"][tid]
                        slot["state"] = "reviewing"
                        slot["last_summary"] = (
                            (result.summary or "").rstrip()
                            + f"\n\nmerge conflict into {parent}: {e}\n"
                            "Resolve the conflict in the parent worktree "
                            "(`git merge --abort` to start over), then re-review."
                        )
                        st.save(root, jira, data)
                        ran.append(tid)
                        continue
                else:
                    slot["state"] = "done"
                    st.save(root, jira, data)
                data = st.load(root, jira)
                st.refresh_ready(data)
                st.save(root, jira, data)
            else:
                slot["state"] = "blocked"
                slot["last_summary"] = result.summary
                st.save(root, jira, data)
        ran.append(tid)
    return ran


def ticket_review_override(
    root: Path,
    jira: str,
    ticket_id: str,
    verdict: str,
    summary: str | None = None,
) -> dict[str, Any]:
    norm_verdict = (verdict or "").strip().lower()
    if norm_verdict in {"pass", "passed", "ok", "done"}:
        norm_verdict = "passed"
    elif norm_verdict in {"fail", "failed", "blocked"}:
        norm_verdict = "failed"
    else:
        raise ValueError(f"invalid verdict {verdict!r}; must be 'passed' or 'failed'")

    req = paths.req_dir(root, jira)
    tickets = {t.id: t for t in load_tickets(req)}
    if ticket_id not in tickets:
        raise ValueError(f"unknown ticket {ticket_id}")

    with st.jira_lock(jira):
        data = st.load(root, jira)
        t_slots = data.get("tickets") or {}
        if ticket_id not in t_slots:
            raise ValueError(f"ticket {ticket_id} not found in STATUS.yaml")
        slot = t_slots[ticket_id]

        if norm_verdict == "passed":
            parent = slot.get("worktree")
            try:
                _ticket_done_locked(root, jira, ticket_id, summary=summary)
            except gitops.GitError as e:
                if parent:
                    gitops.merge_abort(Path(parent))
                data = st.load(root, jira)
                slot = data["tickets"][ticket_id]
                slot["state"] = "reviewing"
                slot["last_summary"] = (
                    ((summary if summary is not None else slot.get("last_summary")) or "").rstrip()
                    + f"\n\nmerge conflict into {parent}: {e}\n"
                    "Resolve the conflict in the parent worktree "
                    "(`git merge --abort` to start over), then re-review."
                )
                st.save(root, jira, data)
                raise ValueError(f"merge conflict into {parent}: {e}") from e
        else:
            text = (summary or "").strip()
            if text:
                if "REVIEW_FAILED" not in text:
                    formatted = f"REVIEW_FAILED\n\n{text}"
                else:
                    formatted = text
            else:
                formatted = "REVIEW_FAILED\n\nRejected by reviewer."
            slot["last_summary"] = formatted
            slot["state"] = "blocked"
            st.refresh_ready(data)
            st.save(root, jira, data)

        data = st.load(root, jira)
        return dict(data["tickets"][ticket_id])


def status_text(root: Path, jira: str | None) -> str:
    if jira:
        keys = [jira]
    else:
        keys = [p.name for p in paths.iter_req_dirs(root)]
    lines: list[str] = []
    for key in sorted(keys):
        data = st.load(root, key)
        lines.append(f"{key}  phase={data.get('phase')}")
        for tid, slot in (data.get("tickets") or {}).items():
            child = slot.get("child_worktree") or "-"
            lines.append(f"  {tid}  {slot.get('state')}  repo={slot.get('repo')}  child={child}")
        if data.get("contract_review"):
            lines.append(f"  contract={data.get('contract_review')}")
        test = data.get("test") if isinstance(data.get("test"), dict) else None
        if test:
            lines.append(
                f"  test={test.get('status')} verdict={test.get('latest_verdict') or '-'}"
            )
    return "\n".join(lines) if lines else "(no requirements)"


def ticket_diff(root: Path, jira: str, ticket_id: str) -> dict[str, Any]:
    req = paths.req_dir(root, jira)
    if not req.exists() or not paths.is_req_dir(req):
        raise FileNotFoundError(f"no requirement {jira}")
    tickets = {t.id: t for t in load_tickets(req)}
    t = tickets.get(ticket_id)
    if not t:
        raise ValueError(f"unknown ticket {ticket_id}")
    data = st.load(root, jira)
    slot = (data.get("tickets") or {}).get(ticket_id) or {}
    state = slot.get("state") or "pending"
    title = t.title or t.id
    repo_alias = t.repo

    if state in {"pending", "ready"}:
        return {
            "jira": jira,
            "ticket_id": ticket_id,
            "title": title,
            "repo": repo_alias,
            "state": state,
            "base": "",
            "head": "",
            "log": "",
            "stat": "",
            "diff": "",
            "files": [],
            "message": f"任务尚未开始实现 (状态: {state})",
        }

    repos = load_repos(root)
    repo_obj = repos.get(repo_alias)
    default_base = repo_obj.default_base if repo_obj else "main"
    parsed = list(tickets.values())
    since = _previous_head_sha(data, parsed, ticket_id, repo_alias)

    cwd = _cwd_for_ticket(root, jira, t, slot)
    if not cwd.exists() or not (cwd / ".git").exists():
        parent = paths.req_worktree(root, jira, repo_alias)
        if parent.exists() and (parent / ".git").exists():
            cwd = parent
        else:
            return {
                "jira": jira,
                "ticket_id": ticket_id,
                "title": title,
                "repo": repo_alias,
                "state": state,
                "base": "",
                "head": "",
                "log": "",
                "stat": "",
                "diff": "",
                "files": [],
                "message": f"工作区不存在 ({cwd})，可能尚未冻结或已清理",
            }

    base = since or gitops.start_point(cwd, default_base)
    head_sha = slot.get("head_sha")

    diff_target = f"{base}..{head_sha}" if (state == "done" and head_sha) else base
    log_target = f"{base}..{head_sha}" if (state == "done" and head_sha) else f"{base}..HEAD"

    try:
        log = gitops.run(["git", "log", "--oneline", log_target], cwd=cwd)
    except gitops.GitError:
        log = ""

    try:
        stat = gitops.run(["git", "diff", "--stat", diff_target], cwd=cwd)
    except gitops.GitError:
        stat = ""

    try:
        raw_diff = gitops.run(["git", "diff", diff_target], cwd=cwd)
    except gitops.GitError as e:
        raw_diff = f"(git diff 出错: {e})"

    try:
        name_status = gitops.run(["git", "diff", "--name-status", diff_target], cwd=cwd)
    except gitops.GitError:
        name_status = ""

    files: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for line in name_status.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            st_code, file_path = parts[0], parts[1]
            files.append({"status": st_code, "path": file_path})
            seen_paths.add(file_path)

    untracked_diff_text = ""
    if state != "done":
        try:
            untracked_names = gitops.run(
                ["git", "ls-files", "--others", "--exclude-standard"], cwd=cwd
            )
            for rel in untracked_names.splitlines():
                rel = rel.strip()
                if not rel or rel in seen_paths:
                    continue
                files.append({"status": "A", "path": rel})
            untracked_diff_text = gitops._untracked_diff(cwd)
        except gitops.GitError:
            pass

    full_diff = raw_diff
    if untracked_diff_text:
        if full_diff.strip():
            full_diff = full_diff + "\n\n" + untracked_diff_text
        else:
            full_diff = untracked_diff_text

    if not full_diff.strip() and not log.strip():
        full_diff = f"(与 {base} 相比无代码改动)"

    return {
        "jira": jira,
        "ticket_id": ticket_id,
        "title": title,
        "repo": repo_alias,
        "state": state,
        "base": base,
        "head": head_sha if (state == "done" and head_sha) else "HEAD",
        "log": log,
        "stat": stat,
        "diff": full_diff,
        "files": files,
    }


def requirement_diff(root: Path, jira: str) -> dict[str, Any]:
    req = paths.req_dir(root, jira)
    if not req.exists() or not paths.is_req_dir(req):
        raise FileNotFoundError(f"no requirement {jira}")
    data = st.load(root, jira)
    aliases = list(data.get("repos") or [])
    repos = load_repos(root)
    out_repos: list[dict[str, Any]] = []
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        repo_obj = repos.get(alias)
        default_base = repo_obj.default_base if repo_obj else "main"
        if not wt.exists() or not (wt / ".git").exists():
            continue
        base = gitops.start_point(wt, default_base)
        try:
            log = gitops.run(["git", "log", "--oneline", f"{base}..HEAD"], cwd=wt)
        except gitops.GitError:
            log = ""
        try:
            stat = gitops.run(["git", "diff", "--stat", base], cwd=wt)
        except gitops.GitError:
            stat = ""
        try:
            raw_diff = gitops.run(["git", "diff", base], cwd=wt)
        except gitops.GitError:
            raw_diff = ""
        try:
            name_status = gitops.run(["git", "diff", "--name-status", base], cwd=wt)
        except gitops.GitError:
            name_status = ""
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        for line in name_status.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                files.append({"status": parts[0], "path": parts[1]})
                seen.add(parts[1])
        try:
            untracked = gitops._untracked_diff(wt)
            for rel in gitops.run(
                ["git", "ls-files", "--others", "--exclude-standard"], cwd=wt
            ).splitlines():
                rel = rel.strip()
                if rel and rel not in seen:
                    files.append({"status": "A", "path": rel})
        except gitops.GitError:
            untracked = ""
        full_diff = raw_diff
        if untracked:
            full_diff = (full_diff + "\n\n" + untracked).strip()
        out_repos.append(
            {
                "repo": alias,
                "default_base": default_base,
                "base": base,
                "log": log,
                "stat": stat,
                "diff": full_diff or f"(与 {base} 相比无代码改动)",
                "files": files,
            }
        )
    return {
        "jira": jira,
        "phase": data.get("phase", "open"),
        "repos": out_repos,
    }

