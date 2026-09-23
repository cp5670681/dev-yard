from __future__ import annotations

import hashlib
import re
import shutil
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dev_yard import attachments, gitops, grill_round, paths
from dev_yard import status as st
from dev_yard.atlassian import collect_requirement
from dev_yard.bug_tickets import (
    fix_ticket_ids,
    normalize_findings,
    parse_findings_from_summary,
    spawn_fix_tickets,
    spawn_fix_tickets_result,
)
from dev_yard.config import (
    Repo,
    git_project_name,
    load_git_settings,
    load_repos,
    render_freeze_branch,
    require_pair,
    resolve_freeze_branch,
    save_repos,
    ticket_branch_name,
)
from dev_yard.env import load_env
from dev_yard.fsutil import atomic_write_bytes, atomic_write_text
from dev_yard.runners import (
    Runner,
    RunResult,
    agent_binary,
    get_runner,
    pi_argv,
)
from dev_yard.skillbind import session_prompt, session_prompt_for
from dev_yard.stages import StageSpec, load_registry
from dev_yard.tickets import HEADING as TICKET_HEADING
from dev_yard.tickets import (
    Ticket,
    append_light_ticket,
    load_tickets,
    next_ticket_id,
    parse_tickets,
)

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

Headings must be T + number (T1, T2, …) or bug tickets B + number (B1, B2, …) with a `- repo:` bullet.
CLI ignores any other heading. Bug tickets may set `source`, `finding`, and `depends_on`.
"""


def init_yard(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    yml = paths.repos_yaml(root)
    if not yml.exists():
        yml.write_text("repos: {}\n")
    gi = root / ".gitignore"
    extra = [
        ".repos/",
        ".yard-worktrees/",
        "reqs/",
        ".env",
        "repos.yaml",
        "qa.yaml",
        ".yard-qa/",
        ".yard-assistant/",
    ]
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
    test_branch: str | None = None,
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
        test_branch=(test_branch or "").strip() or None,
    )
    source = repo.source_path(root)
    if repo.path and not (source / ".git").exists():
        raise ValueError(f"{source} is not a git repo")
    if not repo.path and (source / ".git").exists():
        try:
            current = gitops.run(["git", "remote", "get-url", "origin"], cwd=source)
        except gitops.GitError:
            current = ""
        if current and _norm_git_url(current) != _norm_git_url(url):
            raise ValueError(
                f"{alias}: clone at {source} has origin {current}, not {url}; "
                "remove the clone or keep the existing URL"
            )
    repos[alias] = repo
    save_repos(root, repos)
    gitops.ensure_clone(repo.url, source, on_progress=on_progress)
    return repo


def repo_set_pi(
    root: Path,
    alias: str,
    provider: str | None,
    model: str | None,
    test_branch: str | None = None,
) -> Repo:
    repos = load_repos(root)
    repo = repos.get(alias)
    if repo is None:
        raise ValueError(f"unknown repo {alias}")
    pair = require_pair(provider, model)
    repo.provider = pair[0] if pair else None
    repo.model = pair[1] if pair else None
    if test_branch is not None:
        # None = not provided (leave as-is); "" = clear.
        repo.test_branch = test_branch.strip() or None
    save_repos(root, repos)
    return repo


def extract_req_key(target: str) -> str:
    """Extract or sanitize a requirement key from a URL, issue ID, or description."""
    s = (target or "").strip()
    if not s:
        return ""
    # 1. Plain identifier (alphanumeric with underscores, hyphens, dots)
    if not (s.startswith(("http://", "https://")) or "/" in s or "\\" in s):
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
        if not (jira.startswith(("http://", "https://")) or "/" in jira or "\\" in jira)
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
            raise ValueError(
                f"{req_key} is already phase={phase}; run 'dev-yard req reset-phase {req_key}' or pass --force to re-open"
            )
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
        # uploads/ is human-owned: the agent must not be able to drop it, so
        # snapshot it and put it back after the run (success or failure).
        uploads = d / attachments.UPLOADS_DIRNAME
        saved_uploads = _stash_tree(uploads)
        snap = _snapshot(d, load_registry(root)["open"].protects)
        r = runner or get_runner(root, "open", print_mode=True)
        prompt = session_prompt(root, "open", req_key, target=actual_target)
        result = r.start(prompt, root, [d])
        if saved_uploads:
            _unstash_tree(uploads, saved_uploads)
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
        if not req_md.exists() or req_md.read_text(encoding="utf-8") == skeleton:
            req_md.write_text(skeleton, encoding="utf-8")
            warning = "pi did not write REQUIREMENT.md; wrote skeleton"
    elif source == "text":
        content = (payload if payload is not None else actual_target) or ""
        if not content.strip():
            content = REQ_SKELETON.format(key=req_key, title=req_key, body="")
        (d / "REQUIREMENT.md").write_text(content, encoding="utf-8")
    elif source == "file":
        src_file = Path(payload or actual_target).expanduser().resolve()
        if not src_file.exists():
            raise FileNotFoundError(f"Source file not found: {src_file}")
        content = src_file.read_text(encoding="utf-8")
        (d / "REQUIREMENT.md").write_text(content, encoding="utf-8")
    elif source == "http":
        result = collect_requirement(d, req_key, root)
        warning = "; ".join(result.warnings)
        (d / "REQUIREMENT.md").write_text(
            result.markdown or REQ_SKELETON.format(key=req_key, title=req_key, body=""),
            encoding="utf-8",
        )
    else:
        (d / "REQUIREMENT.md").write_text(
            REQ_SKELETON.format(key=req_key, title=req_key, body=""),
            encoding="utf-8",
        )
        warning = "skipped remote fetch"
    if not (d / "GRILL.md").exists():
        (d / "GRILL.md").write_text(GRILL_SKELETON.format(key=req_key), encoding="utf-8")
    if not (d / "SPEC.md").exists():
        (d / "SPEC.md").write_text(SPEC_SKELETON.format(key=req_key), encoding="utf-8")
    if not (d / "TICKETS.md").exists():
        (d / "TICKETS.md").write_text(TICKETS_SKELETON.format(key=req_key), encoding="utf-8")
    # uploads/ is never wiped above; re-point REQUIREMENT.md at whatever survives,
    # so a re-extract does not silently drop the human-added attachments.
    kept_uploads = attachments.list_names(root, req_key)
    if kept_uploads:
        attachments.sync_uploads_section(d, kept_uploads)
    data = st.load(root, req_key)
    data["phase"] = "open"
    st.save(root, req_key, data)
    if warning == "pi did not write REQUIREMENT.md; wrote skeleton":
        raise RuntimeError(warning)
    return d, warning


# ---- human attachments (reqs/<JIRA>/uploads/) ---------------------------


def req_attach(
    root: Path, jira: str, sources: list[Path], names: list[str] | None = None
) -> list[str]:
    """Copy local files into reqs/<jira>/uploads/ and refresh REQUIREMENT.md."""
    req = paths.req_dir(root, jira)
    if not req.is_dir():
        raise FileNotFoundError(f"missing {req}; run 'dev-yard req open' first")
    if not sources:
        raise ValueError("no files to attach")
    if names and len(names) != len(sources):
        raise ValueError("--name count must match the number of files")
    added = [
        attachments.add_file(root, jira, src, name)
        for src, name in zip(sources, names or [None] * len(sources), strict=True)
    ]
    attachments.sync_doc(root, jira)
    return added


def req_attach_bytes(root: Path, jira: str, items: list[tuple[str, bytes]]) -> list[str]:
    """Store already-read uploads (web form path); validates before any write."""
    req = paths.req_dir(root, jira)
    if not req.is_dir():
        raise FileNotFoundError(f"missing {req}; run 'dev-yard req open' first")
    if not items:
        raise ValueError("no files uploaded")
    limit = attachments.MAX_BYTES // (1024 * 1024)
    for name, data in items:
        attachments.safe_name(name)
        if not data:
            raise ValueError(f"{name} is empty")
        if len(data) > attachments.MAX_BYTES:
            raise ValueError(f"{name} too large (>{limit}MB)")
    added = [attachments.add_bytes(root, jira, n, d) for n, d in items]
    attachments.sync_doc(root, jira)
    return added


def req_detach(root: Path, jira: str, names: list[str]) -> list[str]:
    """Remove attachments and refresh the REQUIREMENT.md uploads list."""
    req = paths.req_dir(root, jira)
    if not req.is_dir():
        raise FileNotFoundError(f"missing {req}")
    if not names:
        raise ValueError("no attachments to remove")
    for name in names:
        attachments.remove(root, jira, name)
    attachments.sync_doc(root, jira)
    return attachments.list_names(root, jira)


def _norm_git_url(url: str) -> str:
    s = (url or "").strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    return s


def req_freeze(root: Path, jira: str, force: bool = False) -> list[Path]:
    req = paths.req_dir(root, jira)
    tickets = load_tickets(req)
    if not tickets or not any(t.repo for t in tickets):
        raise ValueError("TICKETS.md has no tickets with a repo; finish to-tickets first")
    with st.jira_lock(jira):
        data = st.load(root, jira)
        phase = data.get("phase") or "open"
        if phase in {"testing", "done"} and not force:
            raise ValueError(
                f"phase is {phase}; re-freeze would unwind it. pass --force to continue"
            )
        repos = load_repos(root)
        data = st.sync_tickets(data, tickets)
        created: list[Path] = []
        aliases = sorted({t.repo for t in tickets if t.repo})
        branch = _existing_freeze_branch(root, jira, data, aliases, repos)
        if not branch:
            branch = render_freeze_branch(load_git_settings(root).freeze_branch, jira)
        for alias in aliases:
            repo = repos.get(alias)
            if not repo:
                raise ValueError(f"unknown repo alias {alias}")
            source = repo.source_path(root)
            gitops.ensure_clone(repo.url, source)
            gitops.fetch(source)
            wt = paths.req_worktree(root, jira, alias)
            start = gitops.start_point(source, repo.default_base)
            gitops.worktree_add(
                source,
                wt,
                branch,
                start,
                reset_existing=bool(force and phase in {"testing", "done"}),
            )
            created.append(wt)
            sha = gitops.rev_parse(source, start)
            if sha:
                bases = data.get("base_shas")
                if not isinstance(bases, dict):
                    bases = {}
                bases[alias] = sha
                data["base_shas"] = bases
            for slot in data["tickets"].values():
                if slot.get("repo") == alias:
                    slot["worktree"] = str(wt)
        data["branch"] = branch
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


def _teardown_worktrees(root: Path, jira: str, d: Path, data: dict[str, Any]) -> None:
    """Remove a requirement's ticket/freeze worktrees and their local branches."""
    repos = load_repos(root)
    tickets = st.tickets_map(data.get("tickets"))

    aliases: set[str] = set()
    wt_root = d / "worktrees"
    if wt_root.is_dir():
        aliases.update(p.name for p in wt_root.iterdir() if p.is_dir())
    for slot in tickets.values():
        if slot.get("repo"):
            aliases.add(str(slot["repo"]))
    freeze = _existing_freeze_branch(root, jira, data, sorted(aliases), repos)
    if not freeze:
        freeze = resolve_freeze_branch(root, jira, data)

    for tid, slot in tickets.items():
        child = slot.get("child_worktree")
        alias = slot.get("repo")
        repo = repos.get(alias) if alias else None
        if child and repo:
            source = repo.source_path(root)
            child_path = Path(child)
            name = _checked_out_branch(child_path) or ticket_branch_name(freeze, tid)
            gitops.worktree_remove(source, child_path)
            gitops.branch_delete(source, name)

    child_root = root / ".yard-worktrees" / jira
    if child_root.is_dir():
        for alias_dir in child_root.iterdir():
            if not alias_dir.is_dir():
                continue
            repo = repos.get(alias_dir.name)
            for ticket_dir in alias_dir.iterdir():
                if not ticket_dir.is_dir() or not repo:
                    continue
                if ticket_dir.name.startswith("_"):
                    # Scratch dirs (e.g. _test-merge) are not ticket worktrees.
                    continue
                source = repo.source_path(root)
                name = _checked_out_branch(ticket_dir) or ticket_branch_name(
                    freeze, ticket_dir.name
                )
                gitops.worktree_remove(source, ticket_dir)
                gitops.branch_delete(source, name)
        for alias_dir in child_root.iterdir():
            if not alias_dir.is_dir():
                continue
            repo = repos.get(alias_dir.name)
            if repo:
                gitops.worktree_remove(
                    repo.source_path(root),
                    paths.test_merge_worktree(root, jira, alias_dir.name),
                )
        shutil.rmtree(child_root, ignore_errors=True)

    for alias in aliases:
        repo = repos.get(alias)
        if not repo:
            continue
        source = repo.source_path(root)
        wt = paths.req_worktree(root, jira, alias)
        name = _checked_out_branch(wt) or freeze
        gitops.worktree_remove(source, wt)
        gitops.branch_delete(source, name)


def _req_delete_locked(root: Path, jira: str, d: Path) -> None:
    data = st.load(root, jira) if (d / "STATUS.yaml").is_file() else {"tickets": {}}
    _teardown_worktrees(root, jira, d, data)
    shutil.rmtree(d)


def req_reset_phase(root: Path, jira: str) -> dict[str, Any]:
    """Rewind a requirement to phase=open and drop what the pipeline built.

    Tears down freeze/ticket worktrees and their local branches, resets ticket
    states, and clears contract/test/stage bookkeeping so the requirement can
    walk the pipeline again. Docs and assets under `reqs/<jira>/` are kept.
    """
    d = paths.req_dir(root, jira)
    if not d.exists() or not paths.is_req_dir(d):
        raise FileNotFoundError(f"no requirement {jira}")
    with st.jira_lock(jira):
        data = st.load(root, jira)
        phase = data.get("phase") or "open"
        if phase == "open":
            raise ValueError(f"{jira} is already phase=open; nothing to reset")
        _teardown_worktrees(root, jira, d, data)
        tickets = st.tickets_map(data.get("tickets"))
        for slot in tickets.values():
            slot.pop("worktree", None)
            slot.pop("child_worktree", None)
            slot.pop("head_sha", None)
            slot.pop("last_summary", None)
            slot.pop("last_verdict", None)
            slot["state"] = "pending"
        data["tickets"] = tickets
        for key in (
            "branch",
            "base_shas",
            "contract_review",
            "contract_summary",
            "contract_findings",
            "test",
            "stage_runs",
        ):
            data.pop(key, None)
        data["phase"] = "open"
        st.refresh_ready(data)
        st.save(root, jira, data)
    return data


def req_reset_grill(root: Path, jira: str) -> dict[str, Any]:
    """Drop the pending alignment round and rewind the grill stage.

    Removes the web round file (`.grill-round.json`), resets GRILL.md to its
    skeleton, and clears the `grill` stage bookkeeping so the next 对齐
    regenerates the frontier from scratch instead of replaying a stale round.
    Phase, tickets, contract and test state are untouched.
    """
    d = paths.req_dir(root, jira)
    if not d.exists() or not paths.is_req_dir(d):
        raise FileNotFoundError(f"no requirement {jira}")
    with st.jira_lock(jira):
        data = st.load(root, jira)
        grill_round.clear_round(d)
        (d / "GRILL.md").write_text(GRILL_SKELETON.format(key=jira), encoding="utf-8")
        runs = data.get("stage_runs")
        if isinstance(runs, dict):
            runs.pop("grill", None)
            if not runs:
                data.pop("stage_runs", None)
        st.save(root, jira, data)
    return data


# ---- lightweight requirement-doc change ---------------------------------

_CHANGE_DOCS = ("REQUIREMENT.md", "GRILL.md", "SPEC.md", "TICKETS.md")
_CHANGE_LOG_HEADING = "## 变更记录"
_NOTE_MAX = 500
# to-spec's template uses the first; the bundled SPEC skeleton uses "## Contracts (APIs / events / fields)".
_CONTRACT_HEADINGS = ("## Implementation Decisions", "## Contracts")
_CHANGE_ENTRY_RE = re.compile(r"^### (c[0-9]+) ·")


def doc_fingerprint(req: Path) -> dict[str, str | None]:
    """sha256 of the four requirement docs; None when a doc is missing."""
    out: dict[str, str | None] = {}
    for name in _CHANGE_DOCS:
        path = req / name
        out[name.split(".")[0].lower()] = (
            "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else None
        )
    return out


def _sanitize_note(note: str) -> str:
    """One-line, defused note: never lets a note forge a ticket heading or bullet."""
    text = re.sub(r"\s+", " ", note or "").strip()
    text = text.lstrip("#").strip()
    while text[:2] in {"- ", "* ", "+ "}:
        text = text[2:].strip()
    if len(text) > _NOTE_MAX:
        text = text[:_NOTE_MAX].rstrip() + "…"
    return text


def _format_change_note(entry: dict[str, Any]) -> str:
    lines = [
        f"### {entry.get('id')} · {entry.get('at')} ({entry.get('actor') or 'host'})",
        str(entry.get("note") or ""),
    ]
    repo = str(entry.get("repo") or "")
    ticket = str(entry.get("ticket") or "")
    if repo or ticket:
        lines.append("")
        if repo:
            lines.append(f"- 仓：{repo}")
        if ticket:
            lines.append(f"- 票：{ticket}")
    return "\n".join(lines)


def _parse_change_log(text: str) -> list[tuple[str, str]]:
    """`(change_id, note)` pairs from REQUIREMENT.md's `## 变更记录` section."""
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        m = _CHANGE_ENTRY_RE.match(line.strip())
        if not m:
            continue
        note = ""
        for nxt in lines[i + 1 :]:
            stripped = nxt.strip()
            if not stripped:
                continue
            if stripped.startswith(("#", "- ")):
                break
            note = stripped
            break
        out.append((m.group(1), note))
    return out


def _append_change_note(
    req: Path, *, note: str, actor: str, repo: str, at: str
) -> str:
    """Append a change-log entry (idempotent by note); returns its id.

    Caller must hold `st.jira_lock(jira)`. The log in REQUIREMENT.md is the
    authority for change ids, so concurrent changes cannot reuse one.
    """
    path = req / "REQUIREMENT.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    entries = _parse_change_log(existing)
    for cid, text in entries:
        if text == note:
            return cid
    nums = [int(cid[1:]) for cid, _ in entries if cid[1:].isdigit()]
    change_id = f"c{max(nums, default=0) + 1}"
    block = _format_change_note(
        {"id": change_id, "at": at, "actor": actor, "note": note, "repo": repo}
    )
    if _CHANGE_LOG_HEADING in existing:
        text = existing.rstrip() + "\n\n" + block + "\n"
    else:
        text = existing.rstrip() + "\n\n" + _CHANGE_LOG_HEADING + "\n\n" + block + "\n"
    atomic_write_text(path, text)
    return change_id


def _is_contract_heading(line: str) -> bool:
    stripped = line.strip()
    return any(
        stripped == h or stripped.startswith((h + " ", h + "("))
        for h in _CONTRACT_HEADINGS
    )


def _contract_section(text: str) -> str:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _is_contract_heading(line):
            start = i + 1
            break
    if start is None:
        return ""
    out: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out).strip()


def _light_title(note: str) -> str:
    one = " ".join(note.split())
    if len(one) > 60:
        one = one[:60].rstrip() + "…"
    return f"轻量变更：{one}"


def _spec_change_prompt(note: str, repo: str, jira: str) -> str:
    return (
        "This is a LIGHTWEIGHT requirement change, not a full re-spec. "
        f"Change note: {note}\n"
        f"Affected repo: {repo}.\n"
        "Update SPEC.md incrementally: edit only the sections this change touches; "
        "do not rewrite the whole document and do not touch unrelated sections. "
        "Do NOT modify REQUIREMENT.md, GRILL.md, or TICKETS.md. "
        "Do NOT add or change any cross-repo contract (interfaces / fields / timing); "
        "if the change needs a contract change, state that in your summary instead "
        "of editing the contract."
    )


def _req_change_begin(
    root: Path,
    jira: str,
    note: str,
    *,
    repo: str,
    actor: str,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    req = paths.req_dir(root, jira)
    if not req.is_dir() or not paths.is_req_dir(req):
        raise FileNotFoundError(f"no requirement {jira}")
    clean = _sanitize_note(note)
    if not clean:
        raise ValueError("变更说明不能为空")
    repo = (repo or "").strip()
    if not repo:
        raise ValueError("必须选择一个仓（--repo）")
    if repo not in load_repos(root):
        raise ValueError(f"unknown repo alias {repo}")
    at = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M")
    with st.jira_lock(jira):
        data = st.load(root, jira)
        phase = data.get("phase") or "open"
        if phase not in {"frozen", "testing"}:
            hint = (
                "（已完成的需求改动属于方案级，本次不支持）"
                if phase == "done"
                else "；请先 freeze，或在 open 阶段直接改文档后重跑阶段"
            )
            raise ValueError(f"{jira} phase={phase}; 轻量变更仅在 frozen/testing 可用{hint}")
        if not paths.req_worktree(root, jira, repo).is_dir():
            raise ValueError(f"{repo} 没有冻结 worktree（该仓未参与本需求 freeze）")
        change_id = _append_change_note(
            req, note=clean, actor=actor, repo=repo, at=at
        )
        docs_before = doc_fingerprint(req)
    spec_path = req / "SPEC.md"
    spec_before = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
    if on_progress:
        on_progress(f"change {change_id} recorded in REQUIREMENT.md")
    return {
        "jira": jira,
        "change_id": change_id,
        "at": at,
        "actor": actor,
        "note": clean,
        "repo": repo,
        "docs_before": docs_before,
        "spec_before": spec_before,
    }


def _req_change_apply(root: Path, jira: str, ctx: dict[str, Any], **fields: Any) -> str:
    """Append (or reuse) the light ticket and write STATUS, under one lock."""
    req = paths.req_dir(root, jira)
    with st.jira_lock(jira):
        data = st.load(root, jira)
        phase = data.get("phase") or "open"
        if phase not in {"frozen", "testing"}:
            raise ValueError(
                f"{jira} phase changed to {phase!r} during change; aborting"
            )
        text = (
            (req / "TICKETS.md").read_text(encoding="utf-8")
            if (req / "TICKETS.md").is_file()
            else ""
        )
        parsed_all = parse_tickets(text)
        existing = next(
            (
                t
                for t in parsed_all
                if t.source == "light" and t.change == ctx["change_id"]
            ),
            None,
        )
        if existing is not None:
            ticket_id = existing.id
        else:
            ticket_id = next_ticket_id(parsed_all)
            append_light_ticket(
                req,
                ticket_id=ticket_id,
                title=_light_title(ctx["note"]),
                repo=ctx["repo"],
                note=ctx["note"],
                change_id=ctx["change_id"],
            )
            if not any(t.id == ticket_id for t in load_tickets(req)):
                raise RuntimeError("light ticket was appended but did not parse")
        data = st.sync_tickets(data, load_tickets(req))
        st.refresh_ready(data)
        st.upsert_change(
            data,
            {
                "id": ctx["change_id"],
                "at": ctx["at"],
                "actor": ctx["actor"],
                "note": ctx["note"],
                "repo": ctx["repo"],
                "ticket": ticket_id,
                **fields,
            },
        )
        st.save(root, jira, data)
    return ticket_id


def _mark_qa_stale(root: Path, jira: str, change_id: str) -> bool:
    from dev_yard.qa_review import mark_stale

    qa = paths.qa_dir(root, jira)
    cases = qa / "cases"
    if not cases.is_dir() or not any(cases.rglob("case-*.md")):
        return False
    mark_stale(qa, f"轻量变更 {change_id}")
    return True


def req_change(
    root: Path,
    jira: str,
    note: str,
    *,
    repo: str,
    grill: bool = False,
    run: bool = False,
    print_mode: bool = False,
    actor: str = "cli",
    on_progress: Callable[[str], None] | None = None,
    runner_factory: Callable[[str], Runner] | None = None,
    grill_runner: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Lightweight requirement-doc change: note -> (grill) -> spec -> one light ticket."""
    ctx = _req_change_begin(
        root, jira, note, repo=repo, actor=actor, on_progress=on_progress
    )
    req = paths.req_dir(root, jira)
    grilled = False
    if grill:
        if on_progress:
            on_progress(f"grill: {ctx['change_id']}")
        if grill_runner is not None:
            grill_runner()
        else:
            runner = runner_factory("grill") if runner_factory else None
            result = run_stage(
                root,
                "grill",
                jira,
                print_mode=print_mode,
                runner=runner,
                prompt_extra=ctx["note"],
            )
            if not result.ok:
                raise RuntimeError("grill failed; change note kept, no ticket created")
        grilled = True
    if on_progress:
        on_progress(f"spec: {ctx['change_id']}")
    spec_extra = _spec_change_prompt(ctx["note"], ctx["repo"], jira)
    runner = runner_factory("spec") if runner_factory else None
    result = run_stage(
        root, "spec", jira, print_mode=print_mode, runner=runner, prompt_extra=spec_extra
    )
    if not result.ok:
        raise RuntimeError("spec failed; change note kept, no ticket created")
    spec_path = req / "SPEC.md"
    spec_after = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
    contract_touched = _contract_section(ctx["spec_before"]) != _contract_section(spec_after)
    # Mark QA stale before the final write: if this fails, nothing has been
    # appended yet and the whole change can be retried (note is idempotent).
    qa_stale = _mark_qa_stale(root, jira, ctx["change_id"])
    ticket_id = _req_change_apply(
        root,
        jira,
        ctx,
        grilled=grilled,
        docs_before=ctx["docs_before"],
        contract_touched=contract_touched,
        stale={"qa": qa_stale},
    )
    outcome: dict[str, Any] = {
        "jira": jira,
        "change_id": ctx["change_id"],
        "ticket": ticket_id,
        "repo": ctx["repo"],
        "grilled": grilled,
        "contract_touched": contract_touched,
        "qa_stale": qa_stale,
        "ran": [],
    }
    if on_progress:
        on_progress(
            f"{jira} change {ctx['change_id']}: +{ticket_id} "
            f"({'contract touched; ' if contract_touched else ''}"
            f"qa_stale={qa_stale})"
        )
    if run:
        outcome["ran"] = implement(
            root,
            jira,
            [ticket_id],
            print_mode=print_mode,
            runner=runner_factory("implement") if runner_factory else None,
        )
    return outcome


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
    freeze = resolve_freeze_branch(root, jira, st.load(root, jira), parent)
    if (parent / ".git").exists() and gitops.has_changes(parent):
        gitops.commit_all(parent, f"chore: sync uncommitted changes before {ticket_id}")
    child = paths.child_worktree(root, jira, t.repo, ticket_id)
    gitops.worktree_add(
        source,
        child,
        ticket_branch_name(freeze, ticket_id),
        freeze,
        reset_existing=True,
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
    root: Path,
    jira: str,
    ticket_id: str,
    summary: str | None = None,
    verdict: str | None = None,
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
    freeze = resolve_freeze_branch(
        root, jira, data, Path(parent) if parent else None
    )
    if child and parent:
        gitops.commit_all(Path(child), f"feat({ticket_id}): {t.title or ticket_id}")
        gitops.merge_into(Path(parent), ticket_branch_name(freeze, ticket_id))
        sha = gitops.commit_all(Path(parent), f"merge: {ticket_id}")
        if sha:
            slot["head_sha"] = sha
        gitops.worktree_remove(source, Path(child))
        gitops.branch_delete(source, ticket_branch_name(freeze, ticket_id))
        slot["child_worktree"] = None
    elif parent:
        sha = gitops.commit_all(Path(parent), f"feat({ticket_id}): {t.title or ticket_id}")
        if sha:
            slot["head_sha"] = sha
    if summary is not None:
        slot["last_summary"] = summary
    if verdict is not None:
        slot["last_verdict"] = verdict
    slot["state"] = "done"
    st.refresh_ready(data)
    st.save(root, jira, data)


def _checked_out_branch(worktree: Path | None) -> str | None:
    if worktree is None:
        return None
    if not (worktree / ".git").exists():
        return None
    try:
        name = gitops.current_branch(worktree)
    except gitops.GitError:
        return None
    if name and name != "HEAD":
        return name
    return None


def _existing_freeze_branch(
    root: Path,
    jira: str,
    data: dict[str, Any],
    aliases: list[str],
    repos: dict[str, Repo],
) -> str | None:
    stored = data.get("branch")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    for alias in aliases:
        repo = repos.get(alias)
        if not repo:
            continue
        name = _checked_out_branch(paths.req_worktree(root, jira, alias))
        if name:
            return name
    return None


def _cwd_for_ticket(root: Path, jira: str, ticket: Ticket, slot: dict) -> Path:
    child = slot.get("child_worktree")
    if child:
        return Path(child)
    parent = slot.get("worktree") or str(paths.req_worktree(root, jira, ticket.repo))
    return Path(parent)


def _sync_child_with_parent(
    root: Path, jira: str, slot: dict, *, leave_conflict: bool
) -> str | None:
    """Bring a ticket's child worktree up to date with the parent branch.

    The parent branch advances whenever a sibling ticket is merged, so a child
    forked earlier must merge it back in before it is reviewed/merged again.
    Returns None when already up to date or merged cleanly, else a conflict
    report. With `leave_conflict=False` the conflicted merge is aborted so the
    child is left untouched and the caller can route the ticket to implement/fix;
    with `leave_conflict=True` the conflict stays in the worktree for the agent.
    """
    child = slot.get("child_worktree")
    parent = slot.get("worktree")
    if not child or not parent:
        return None
    child_path, parent_path = Path(child), Path(parent)
    if not ((child_path / ".git").exists() and (parent_path / ".git").exists()):
        return None
    data = st.load(root, jira)
    freeze = resolve_freeze_branch(root, jira, data, parent_path)

    def report(files: list[str], detail: str) -> str:
        listed = "\n".join(f"- {f}" for f in files) or "(git 未报告冲突文件)"
        return (
            f"SYNC_CONFLICT: 与父分支 `{freeze}` 合并存在冲突，需先解决再审查。\n"
            f"冲突文件：\n{listed}\n{detail}\n"
            "在子 worktree 内解决冲突（保留兄弟票已合并的改动），`git add` 提交后再走实现/审查。"
        )

    # A prior run may have died mid-merge; never commit those markers blindly.
    pending = gitops.unmerged_files(child_path)
    if pending:
        if not leave_conflict:
            gitops.merge_abort(child_path)
        return report(pending, "(上一次合并冲突未解决)")
    if gitops.has_changes(child_path):
        gitops.commit_all(child_path, "chore: wip before syncing parent")
    try:
        gitops.merge_into(child_path, freeze)
    except gitops.GitError as e:
        msg = report(gitops.unmerged_files(child_path), str(e))
        if not leave_conflict:
            gitops.merge_abort(child_path)
        return msg
    return None


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


def _snapshot(req: Path, names: tuple[str, ...]) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for n in names:
        if Path(n).name != n or n in {".", "..", ""}:
            continue
        p = req / n
        out[n] = p.read_bytes() if p.exists() else None
    return out


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
        if Path(name).name != name or name in {".", "..", ""}:
            continue
        p = req / name
        after = p.read_bytes() if p.exists() else None
        if after == before:
            continue
        if before is not None:
            atomic_write_bytes(p, before)
        elif p.exists() or p.is_symlink():
            p.unlink()
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
    """Backward-compatible entry: any registry stage via run_stage."""
    return run_stage(
        root, name, jira, dry_run=dry_run, print_mode=print_mode,
        runner=runner, prompt_extra=prompt_extra,
    )


def run_stage(
    root: Path,
    stage: str | StageSpec,
    jira: str,
    dry_run: bool = False,
    print_mode: bool = False,
    runner: Runner | None = None,
    prompt_extra: str = "",
) -> RunResult:
    """Unified stage execution: phase gate, snapshot/restore, stage_runs."""
    spec = stage if isinstance(stage, StageSpec) else load_registry(root)[stage]
    req = paths.req_dir(root, jira)
    if not req.exists():
        raise FileNotFoundError(f"missing {req}; run: dev-yard req open {jira}")
    bases = ""
    if spec.lists_sources and not dry_run:
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
    with st.jira_lock(jira):
        data = st.load(root, jira)
        phase = data.get("phase") or "open"
        if spec.requires_phase and phase != spec.requires_phase:
            raise ValueError(
                f"{spec.name} requires phase={spec.requires_phase}, current phase={phase}"
            )
    extra = attachments.with_images(
        root,
        jira,
        [
            req / "REQUIREMENT.md",
            req / "GRILL.md",
            req / "SPEC.md",
            req / "TICKETS.md",
            paths.context_md(root),
            paths.adr_dir(root),
        ],
    )
    prompt = session_prompt_for(spec, root, jira, extra=bases)
    r = runner or get_runner(
        root, spec.name, dry_run=dry_run, print_mode=print_mode, spec=spec
    )
    protects = spec.protects
    if "STATUS.yaml" not in protects:
        protects = protects + ("STATUS.yaml",)
    snap = _snapshot(req, protects) if protects and not dry_run else {}
    result = r.start(prompt, root, extra)
    restored = _restore(req, snap) if snap else []
    if restored:
        note = "restored (not this stage's job): " + ", ".join(restored)
        result = RunResult(ok=result.ok, summary=(result.summary + "\n" + note).strip(), exit_code=result.exit_code)
    if not dry_run:
        from datetime import datetime

        with st.jira_lock(jira):
            data = st.load(root, jira)
            runs = data.setdefault("stage_runs", {})
            runs[spec.name] = {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "ok": bool(result.ok),
                "summary": (result.summary or "")[:4000],
            }
            if spec.builtin and result.ok and spec.sets_phase:
                data["phase"] = spec.sets_phase
            st.save(root, jira, data)
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


def _ticket_base_sha(
    data: dict, parsed: list, tid: str, repo: str, slot: dict, cwd: Path
) -> str | None:
    """Diff base for a ticket's own work.

    A ticket that ran in an isolated child worktree branched from the freeze
    point, not from a sibling's head. Diffing it against the sibling head (the
    sequential shortcut in `_previous_head_sha`) makes the sibling's already
    merged work look deleted by this ticket. Use the child/parent merge-base
    instead, which is exactly where the child branched off. A finished ticket's
    branch is gone; its merge landed on top of the then-current parent, i.e. the
    first parent of `head_sha`.
    """
    child = slot.get("child_worktree")
    parent = slot.get("worktree")
    if child and parent:
        child_path, parent_path = Path(child), Path(parent)
        if (child_path / ".git").exists() and (parent_path / ".git").exists():
            try:
                base = gitops.merge_base(child_path, gitops.head_sha(parent_path))
            except gitops.GitError:
                base = None
            if base:
                return base
    head = slot.get("head_sha")
    if slot.get("state") == "done" and isinstance(head, str) and head:
        base = gitops.first_parent(cwd, head)
        if base:
            return base
    return _previous_head_sha(data, parsed, tid, repo)


def _legacy_review_failed(summary: str | None) -> bool:
    """Older reports encoded failure as its own line, or as human-review notes.

    A mention inside a sentence is not a verdict. New reviews use submit_review.
    """
    if not summary:
        return False
    if "[Human Review" in summary:
        return True
    return any(line.strip() == "REVIEW_FAILED" for line in summary.splitlines())


def _review_blocked(result: RunResult) -> bool:
    if result.verdict == "passed":
        return False
    if result.verdict == "failed":
        return True
    return not result.ok


_CLAIM = {
    "implement": ("implementing", {"ready", "blocked", "implementing"}),
    "review": ("reviewing", {"implemented", "reviewing", "blocked"}),
    "fix-contract": (
        "implementing",
        {"ready", "blocked", "implementing"},
    ),
    "fix-test": (
        "implementing",
        {"ready", "blocked", "implementing"},
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
    finding: str = "",
    sync_conflict: str | None = None,
    prior_verdict: str | None = None,
) -> str:
    extra = f"Ticket: {tid} — {title}\nRepo alias: {repo}\nStay in this worktree."
    if sync_conflict:
        extra += (
            "\n\n与父分支同步时发生合并冲突，当前 worktree 处于冲突状态。"
            "先解决所有冲突并保留兄弟票已合并的改动，再完成本票改动；"
            f"完成后一并提交。\n{sync_conflict}"
        )
    if contract_summary or test_report:
        scope = f"finding {finding}" if finding else f"ticket {tid}"
        extra += (
            f" Fix only {scope}; do not implement sibling bug tickets. "
            "The defect text is this ticket's section in TICKETS.md."
        )
    if last_summary and (
        prior_verdict == "failed" or _legacy_review_failed(last_summary)
    ):
        extra += (
            "\n\nPrevious review failed / feedback provided. Fix hard violations, Spec gaps, and review feedback "
            "in this report; do not expand scope; optional smells may stay.\n"
            f"{last_summary}"
        )
    if contract_summary:
        extra += (
            "\n\nPrevious contract review. Fix only Spec contract gaps and hard "
            "violations for this ticket"
            + (f" (finding {finding})" if finding else "")
            + "; do not expand scope; optional smells may stay.\n"
            f"{contract_summary}"
        )
    if test_report:
        extra += (
            "\n\nPrevious test report. Fix only failed findings for this ticket"
            + (f" (finding {finding})" if finding else "")
            + "; do not expand scope.\n"
            f"{test_report}"
        )
    return extra


def from_contract_ids(
    tickets: dict[str, object],
    ids: list[str] | None,
    data: dict | None = None,
) -> list[str]:
    """Bug tickets spawned from contract findings (ready/blocked). Explicit ids keep order."""
    typed = {str(tid): t for tid, t in tickets.items() if isinstance(t, Ticket)}
    if typed:
        return fix_ticket_ids(typed, ids, data or {"tickets": {}}, "contract")
    if ids:
        return [tid for tid in ids if tid in tickets]
    return []


def prepare_fix_tickets(root: Path, jira: str, kind: str) -> list[str]:
    spawn_fix_tickets(root, jira, kind)
    req = paths.req_dir(root, jira)
    tickets = {t.id: t for t in load_tickets(req)}
    data = st.load(root, jira)
    return fix_ticket_ids(tickets, None, data, kind)


def _remove_ticket_block(text: str, ticket_id: str) -> str:
    """Drop one ticket section (heading to next ticket heading) and its depends_on refs."""
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        m = TICKET_HEADING.match(line.strip())
        if m:
            if m.group(1) == ticket_id:
                skipping = True
                continue
            skipping = False
        if not skipping:
            out.append(line)
    cleaned: list[str] = []
    for line in out:
        stripped = line.strip()
        if stripped.startswith("- depends_on:"):
            prefix = line[: len(line) - len(line.lstrip())]
            tokens = stripped.split(":", 1)[1].split()
            tokens = [t for t in tokens if t != ticket_id]
            line = f"{prefix}- depends_on: {' '.join(tokens)}".rstrip()
        cleaned.append(line)
    result = "\n".join(cleaned)
    if text.endswith("\n"):
        result += "\n"
    return result


def ticket_delete(root: Path, jira: str, ticket_id: str) -> dict[str, Any]:
    """Remove a not-yet-started bug ticket (contract/test) from TICKETS.md.

    Only bug tickets that have not started (state pending/ready, no worktree) can
    be dropped here; anything further along must be handled by hand.
    """
    req = paths.req_dir(root, jira)
    md_path = req / "TICKETS.md"
    with st.jira_lock(jira):
        tickets = {t.id: t for t in load_tickets(req)}
        ticket = tickets.get(ticket_id)
        if ticket is None:
            raise FileNotFoundError(f"no ticket {ticket_id}")
        if ticket.source not in {"contract", "test"}:
            raise ValueError(f"{ticket_id} 不是 bug 票，不能删除")
        data = st.sync_tickets(st.load(root, jira), list(tickets.values()))
        st.refresh_ready(data)
        slot = st.tickets_map(data.get("tickets")).get(ticket_id) or {}
        if slot.get("worktree") or slot.get("child_worktree"):
            raise ValueError(f"{ticket_id} 已开工，不能删除")
        if slot.get("state") not in {"pending", "ready"}:
            raise ValueError(f"{ticket_id} 状态为 {slot.get('state')}，不能删除")
        text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        new_text = _remove_ticket_block(text, ticket_id)
        if new_text == text:
            raise ValueError(f"未能在 TICKETS.md 定位 {ticket_id}")
        md_path.write_text(new_text, encoding="utf-8")
        parsed = load_tickets(req)
        data = st.sync_tickets(st.load(root, jira), parsed)
        st.refresh_ready(data)
        st.save(root, jira, data)
    return {
        "jira": jira,
        "ticket_id": ticket_id,
        "title": ticket.title,
        "source": ticket.source,
    }


def claim_run(root: Path, jira: str, action: str, ids: list[str]) -> tuple[list[str], dict[str, str]]:
    spec = _CLAIM.get(action)
    if spec is None or not ids:
        return [], {}
    state, allowed = spec
    parsed = load_tickets(paths.req_dir(root, jira))
    claimed: list[str] = []
    previous: dict[str, str] = {}
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
            previous[tid] = str(slot.get("state") or "")
            slot["state"] = state
            claimed.append(tid)
        st.save(root, jira, data)
    return claimed, previous


def restore_claim(root: Path, jira: str, previous: dict[str, str]) -> None:
    if not previous:
        return
    with st.jira_lock(jira):
        data = st.load(root, jira)
        tickets = data.get("tickets") or {}
        for tid, prev in previous.items():
            slot = tickets.get(tid)
            if slot is not None:
                slot["state"] = prev
        st.save(root, jira, data)


def implement(
    root: Path,
    jira: str,
    ids: list[str] | None,
    dry_run: bool = False,
    print_mode: bool = False,
    runner: Runner | None = None,
    from_contract: bool = False,
    from_test: bool = False,
    force: bool = False,
) -> list[str]:
    if from_contract and from_test:
        raise ValueError("from_contract and from_test are mutually exclusive")
    req = paths.req_dir(root, jira)
    tickets = {t.id: t for t in load_tickets(req)}
    test_body = ""
    with st.jira_lock(jira):
        data = st.sync_tickets(st.load(root, jira), list(tickets.values()))
        st.refresh_ready(data)
        if not dry_run:
            st.save(root, jira, data)
        contract_summary = (data.get("contract_summary") or "").strip()
        ts = data.get("test") if isinstance(data.get("test"), dict) else {}
        parts = [str(ts.get("summary") or "").strip(), str(ts.get("body") or "").strip()]
        findings = ts.get("findings")
        if isinstance(findings, list) and findings:
            lines: list[str] = []
            for item in findings:
                if not isinstance(item, dict):
                    continue
                fid = str(item.get("id") or "").strip()
                title = str(item.get("title") or "").strip()
                detail = str(item.get("detail") or "").strip()
                lines.append("- " + " ".join(x for x in (fid, title, detail) if x))
            if lines:
                parts.append("\n".join(lines))
        test_body = "\n\n".join(p for p in parts if p)
        if from_contract:
            if not contract_summary:
                raise ValueError("no contract_summary; run review --contract first")
    if from_contract or from_test:
        kind = "contract" if from_contract else "test"
        spawned = spawn_fix_tickets_result(root, jira, kind, persist=not dry_run)
        tickets = {t.id: t for t in load_tickets(req)}
        for t in spawned.tickets:
            tickets[t.id] = t
        data = st.sync_tickets(st.load(root, jira), list(tickets.values()))
        st.refresh_ready(data)
        if not dry_run:
            st.save(root, jira, data)
        targets = (
            from_contract_ids(tickets, ids, data)
            if from_contract
            else fix_ticket_ids(tickets, ids, data, "test")
        )
        if from_test and not ids and not targets:
            raise ValueError("no ready test bug tickets; submit bugs first")
        if from_contract and not ids and not targets:
            raise ValueError(
                "no ready contract bug tickets; run a failed contract review first"
            )
        if ids and not targets:
            raise ValueError(
                f"none of {', '.join(ids)} are {kind} bug tickets"
            )
    else:
        targets = ids or st.ready_ids(data)
    runner = runner or get_runner(root, "implement", dry_run=dry_run, print_mode=print_mode)
    ran: list[str] = []
    extra = attachments.with_images(root, jira, [req / "SPEC.md", req / "TICKETS.md"])
    allowed = (
        _FROM_CONTRACT_STATES
        if (from_contract or from_test) and ids
        else {"ready", "blocked", "implementing"}
    )
    skip_state_check = bool(force) and ids is not None and not from_contract and not from_test
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
            if not isinstance(last_summary, str):
                last_summary = None
            prior_verdict = slot.get("last_verdict")
            if not isinstance(prior_verdict, str):
                prior_verdict = None
            was_blocked = slot.get("state") == "blocked"
            was_fix_ticket = slot.get("source") in {"contract", "test"}
            slot["state"] = "implementing"
            if from_contract and data.get("phase") == "done":
                data["phase"] = "frozen"
            if from_test and data.get("phase") == "testing":
                data["phase"] = "frozen"
                ts = data.get("test") if isinstance(data.get("test"), dict) else {}
                ts["status"] = "fixing"
                data["test"] = ts
            st.save(root, jira, data)
            # Every ticket works in its own child worktree; the parent branch is
            # integration-only, so sibling tickets never share a checkout.
            if not slot.get("child_worktree"):
                ticket_start(root, jira, tid)
                data = st.load(root, jira)
                slot = data["tickets"][tid]
            cwd = _cwd_for_ticket(root, jira, t, slot)
            if not (cwd / ".git").exists():
                raise ValueError(f"missing worktree {cwd}; freeze first")
        # Pick up siblings merged into the parent since this child forked. Leave
        # any conflict in the worktree so the agent resolves it in context.
        sync_conflict = _sync_child_with_parent(root, jira, slot, leave_conflict=True)
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
                test_report=test_body if from_test else None,
                finding=t.finding,
                sync_conflict=sync_conflict,
                prior_verdict=prior_verdict,
            ),
        )
        try:
            result = runner.start(prompt, cwd, extra, repo=t.repo)
        except Exception:
            # A cancelled/crashed run leaves nobody working on the ticket; fall
            # the slot back to ready so the board does not spin on a stale
            # "implementing". (A hard process kill is recovered at web startup.)
            _reset_stuck_slot(root, jira, tid, "implementing", "ready")
            raise
        pending_merge = gitops.unmerged_files(cwd)
        with st.jira_lock(jira):
            data = st.load(root, jira)
            slot = data["tickets"][tid]
            if result.ok and pending_merge:
                slot["state"] = "blocked"
                slot["last_summary"] = (
                    (result.summary or "").rstrip()
                    + "\n\nSYNC_CONFLICT: 合并父分支的冲突尚未解决：\n"
                    + "\n".join(f"- {f}" for f in pending_merge)
                ).strip()
                slot.pop("last_verdict", None)
                st.save(root, jira, data)
                ran.append(tid)
                continue
            if result.ok:
                slot["last_summary"] = result.summary
                is_fix = (
                    from_contract
                    or from_test
                    or was_blocked
                    or was_fix_ticket
                    or prior_verdict == "failed"
                    or _legacy_review_failed(last_summary)
                )
                prefix = "fix" if is_fix else "feat"
                sha = gitops.commit_all(cwd, f"{prefix}({tid}): {t.title or tid}")
                if sha:
                    slot["head_sha"] = sha
                    slot["state"] = "implemented"
                    slot.pop("last_verdict", None)
                else:
                    slot["state"] = "blocked"
                    slot["last_summary"] = (
                        (result.summary or "").rstrip() + "\ncommit failed"
                    ).strip()
                    slot.pop("last_verdict", None)
            else:
                slot["state"] = "blocked"
                slot["last_summary"] = result.summary
                slot.pop("last_verdict", None)
            st.save(root, jira, data)
        ran.append(tid)
    return ran


def _reset_stuck_slot(
    root: Path, jira: str, tid: str, from_state: str, to_state: str
) -> None:
    """Fall a ticket slot back when its run aborts, so the board cannot spin."""
    with st.jira_lock(jira):
        data = st.load(root, jira)
        slot = (data.get("tickets") or {}).get(tid)
        if slot and slot.get("state") == from_state:
            slot["state"] = to_state
            st.save(root, jira, data)


def _run_review_or_cancel(
    root: Path,
    jira: str,
    runner: Runner,
    prompt: str,
    cwd: Path,
    extra: list[Path],
    tid: str,
) -> RunResult:
    try:
        return runner.start(prompt, cwd, extra)
    except Exception:
        # A cancelled/crashed review leaves nobody working on the ticket; fall
        # the slot back to implemented so the board does not spin on a stale
        # "reviewing". (A hard process kill is recovered at web startup.)
        _reset_stuck_slot(root, jira, tid, "reviewing", "implemented")
        raise


def recover_stale_tickets(root: Path) -> list[str]:
    """Recover tickets left mid-flight by a previous, now-dead console process.

    A killed process never reaches the in-process abort reset, so its
    `implementing`/`reviewing` slots would otherwise make the board show a
    perpetual spinner with no way to re-trigger. Called at web startup, when no
    job from this process can yet be running. `reviewing` keeps its
    `last_summary` (merge-conflict guidance) but becomes re-reviewable.
    """
    touched: list[str] = []
    for req in paths.iter_req_dirs(root):
        jira = req.name
        if paths.is_reserved_req_name(jira):
            continue
        with st.jira_lock(jira):
            data = st.load(root, jira)
            changed = False
            for slot in (data.get("tickets") or {}).values():
                state = slot.get("state")
                if state == "reviewing":
                    slot["state"] = "implemented"
                    changed = True
                elif state == "implementing":
                    slot["state"] = "ready"
                    changed = True
            if not changed:
                continue
            st.refresh_ready(data)
            st.save(root, jira, data)
            touched.append(jira)
    return touched


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
    with st.jira_lock(jira):
        data = st.sync_tickets(st.load(root, jira), list(tickets.values()))
        if not dry_run:
            st.save(root, jira, data)
    runner = runner or get_runner(
        root, "contract" if contract else "review", dry_run=dry_run, print_mode=print_mode
    )
    extra = attachments.with_images(root, jira, [req / "SPEC.md", req / "TICKETS.md"])
    ran: list[str] = []
    if contract:
        # REQUIREMENT.md is the product source of truth; without it the reviewer
        # flags code that faithfully implements an explicit product rule (e.g. a
        # display threshold) as a contract gap.
        extra = [req / "REQUIREMENT.md", *extra]
        aliases = list(data.get("repos") or [])
        if not aliases:
            raise ValueError("no repos in STATUS.yaml; freeze first")
        repos = load_repos(root)
        saved_bases = data.get("base_shas")
        if not isinstance(saved_bases, dict):
            saved_bases = {}
        wt_lines: list[str] = []
        wt_paths: list[Path] = []
        diffs: list[str] = []
        for alias in aliases:
            wt = paths.req_worktree(root, jira, alias)
            if not wt.exists():
                raise ValueError(f"missing worktree {wt}; freeze first")
            wt_paths.append(wt)
            repo = repos.get(alias)
            default_base = repo.default_base if repo else "main"
            base = gitops.freeze_base(wt, default_base, saved_bases.get(alias))
            wt_lines.append(
                f"- {alias}: {wt}  (diff vs freeze point {base}, already inlined below)"
            )
            diffs.append(f"### {alias}\n{_diff_vs_base(wt, default_base, base)}")
        listed = "\n".join(wt_lines)
        prompt = session_prompt(
            root,
            "contract",
            jira,
            extra=(
                "Mode: --contract. Review every requirement worktree against SPEC.md contracts.\n"
                "REQUIREMENT.md is the product source of truth for behavior and UI rules; "
                "SPEC.md is the implementation contract. Read REQUIREMENT.md before "
                "reporting any behavior/display gap: if the code implements a rule stated "
                "explicitly in REQUIREMENT.md (e.g. a field is shown only when a count "
                "reaches a threshold), that is NOT a gap even when SPEC.md merely lists the "
                "underlying data field. Report a gap only when the code contradicts "
                "REQUIREMENT.md or omits something SPEC.md requires that REQUIREMENT.md does "
                "not explicitly rule out.\n"
                "Do not spawn sub-agents; pi has none. Do not git-diff the yard repo.\n"
                "The inlined diff is taken at this requirement's freeze point, so it "
                "contains only this requirement's own work. Before blaming a hunk on "
                "this requirement, confirm with `git log <base>..HEAD -- <file>` "
                "(base shown per worktree) that its commits are in that range; code "
                "inherited from the branch base is not scope creep.\n"
                "After the written report, call submit_review exactly once. "
                "verdict is passed or failed. That call is the only pass/fail signal; "
                "do not encode it in the report text.\n"
                "If there are contract gaps, pass them as submit_review findings "
                "(one object per independent gap):\n"
                "  id, title, repo (<repos.yaml alias>), detail,\n"
                "  files (paths whose diff lines you cite; omit for missing code),\n"
                "  depends_on (other finding ids, if this fix must wait).\n"
                "Same-repo gaps may be separate findings.\n"
                f"Worktrees:\n{listed}\n\n"
                + "\n\n".join(diffs)
            ),
        )
        result = runner.start(prompt, root, extra + wt_paths)
        if dry_run:
            return ["__contract__"]
        blocked = _review_blocked(result)
        with st.jira_lock(jira):
            data = st.load(root, jira)
            data["contract_review"] = "failed" if blocked else "passed"
            data["contract_summary"] = result.summary
            if result.verdict is not None:
                # The tool supplied the list, including an empty one. Do not
                # scan the report for a findings block.
                data["contract_findings"] = normalize_findings(result.findings or [])
            else:
                parsed_findings = parse_findings_from_summary(result.summary or "")
                if parsed_findings:
                    data["contract_findings"] = parsed_findings
            st.save(root, jira, data)
        if blocked:
            spawn_fix_tickets(root, jira, "contract")
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
            slot["state"] = "reviewing"
            st.save(root, jira, data)
        # Merge the parent branch into the child before reviewing. Sibling merges
        # advance the parent; a stale child would otherwise be reviewed/merged
        # against the wrong base. A conflict blocks the ticket for implement/fix.
        sync_conflict = _sync_child_with_parent(root, jira, slot, leave_conflict=False)
        if sync_conflict:
            with st.jira_lock(jira):
                data = st.load(root, jira)
                slot = data["tickets"].get(tid)
                if slot:
                    slot["state"] = "blocked"
                    slot["last_summary"] = sync_conflict
                    st.save(root, jira, data)
            ran.append(tid)
            continue
        with st.jira_lock(jira):
            data = st.load(root, jira)
            slot = data["tickets"][tid]
            since = _ticket_base_sha(data, parsed, tid, t.repo, slot, cwd)
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
                f"{_diff_vs_base(cwd, base, since)}\n\n"
                "After the written report, call submit_review exactly once "
                "with verdict passed or failed. That call is the only pass/fail "
                "signal; do not encode the verdict in the report text."
            ),
        )
        result = _run_review_or_cancel(root, jira, runner, prompt, cwd, extra, tid)
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
                        _ticket_done_locked(
                            root, jira, tid, summary=result.summary, verdict="passed"
                        )
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
                    parent = slot.get("worktree") or str(paths.req_worktree(root, jira, t.repo))
                    sha = gitops.commit_all(Path(parent), f"feat({tid}): {t.title or tid}")
                    if sha:
                        slot["head_sha"] = sha
                    slot["state"] = "done"
                    slot["last_verdict"] = "passed"
                    st.save(root, jira, data)
                data = st.load(root, jira)
                st.refresh_ready(data)
                st.save(root, jira, data)
            else:
                slot["state"] = "blocked"
                slot["last_summary"] = result.summary
                if result.verdict == "failed":
                    slot["last_verdict"] = "failed"
                else:
                    slot.pop("last_verdict", None)
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
                _ticket_done_locked(
                    root, jira, ticket_id, summary=summary, verdict="passed"
                )
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
            text = (summary or "").strip() or "Rejected by reviewer."
            slot["last_summary"] = text
            slot["last_verdict"] = "failed"
            slot["state"] = "blocked"
            st.refresh_ready(data)
            st.save(root, jira, data)

        data = st.load(root, jira)
        return dict(data["tickets"][ticket_id])


def contract_review_override(
    root: Path,
    jira: str,
    verdict: str,
    summary: str | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    norm_verdict = (verdict or "").strip().lower()
    if norm_verdict in {"pass", "passed", "ok", "done"}:
        norm_verdict = "passed"
    elif norm_verdict in {"fail", "failed", "blocked"}:
        norm_verdict = "failed"
    else:
        raise ValueError(f"invalid verdict {verdict!r}; must be 'passed' or 'failed'")

    with st.jira_lock(jira):
        data = st.load(root, jira)
        data["contract_review"] = norm_verdict
        if summary is not None:
            data["contract_summary"] = summary.strip()
        if findings:
            from dev_yard.bug_tickets import normalize_findings

            data["contract_findings"] = normalize_findings(findings)
        elif norm_verdict == "failed":
            parsed = parse_findings_from_summary(data.get("contract_summary") or "")
            if parsed:
                data["contract_findings"] = parsed
        st.save(root, jira, data)
        if norm_verdict == "failed":
            spawn_fix_tickets(root, jira, "contract")
        return {
            "contract_review": data.get("contract_review"),
            "contract_summary": data.get("contract_summary"),
        }


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
        for sname, run in sorted((data.get("stage_runs") or {}).items()):
            lines.append(f"  {sname} ok={run.get('ok')} at={run.get('at')}")
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

    since = _ticket_base_sha(data, parsed, ticket_id, repo_alias, slot, cwd)
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


def req_push(
    root: Path,
    jira: str,
    repos: list[str] | None = None,
    remote: str = "origin",
    force: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    req = paths.req_dir(root, jira)
    if not req.exists() or not paths.is_req_dir(req):
        raise FileNotFoundError(f"no requirement {jira}")
    data = st.load(root, jira)

    wt_root = req / "worktrees"
    available_aliases: list[str] = []
    if wt_root.is_dir():
        available_aliases = sorted(
            p.name for p in wt_root.iterdir() if p.is_dir() and (p / ".git").exists()
        )

    if not available_aliases and data.get("repos"):
        for a in data["repos"]:
            wt = paths.req_worktree(root, jira, a)
            if wt.exists() and (wt / ".git").exists():
                available_aliases.append(a)

    if not available_aliases:
        raise ValueError(f"no worktrees found for {jira}; freeze first")

    if repos:
        target_aliases = [a for a in repos if a in available_aliases]
        missing = [a for a in repos if a not in available_aliases]
        if missing:
            raise ValueError(f"worktree not found for repo(s): {', '.join(missing)}")
    else:
        target_aliases = available_aliases

    if not target_aliases:
        raise ValueError("no matching repositories to push")

    branch = resolve_freeze_branch(
        root,
        jira,
        data,
        paths.req_worktree(root, jira, target_aliases[0]),
    )
    results: list[dict[str, Any]] = []
    for alias in target_aliases:
        wt = paths.req_worktree(root, jira, alias)
        if not wt.exists() or not (wt / ".git").exists():
            raise ValueError(f"missing worktree {wt}; freeze first")

        if gitops.has_changes(wt):
            if on_progress:
                on_progress(f"committing uncommitted changes in {alias}...")
            gitops.commit_all(wt, f"chore: commit pending changes before push ({jira})")

        if on_progress:
            on_progress(f"pushing {alias} ({branch}) to {remote}...")

        gitops.push(
            wt,
            remote=remote,
            branch=branch,
            set_upstream=True,
            force=force,
            on_progress=on_progress,
        )
        results.append(
            {
                "repo": alias,
                "alias": alias,
                "branch": branch,
                "remote": remote,
                "worktree": str(wt),
                "status": "pushed",
            }
        )

    return results


def req_sync(
    root: Path,
    jira: str,
    repos: list[str] | None = None,
    strategy: str = "ff-only",
    on_progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Fetch remotes; fast-forward clones; update freeze worktrees onto default_base."""
    if strategy not in gitops.SYNC_STRATEGIES:
        raise ValueError(
            f"unknown sync strategy {strategy!r}; use {', '.join(gitops.SYNC_STRATEGIES)}"
        )
    req = paths.req_dir(root, jira)
    if not req.exists() or not paths.is_req_dir(req):
        raise FileNotFoundError(f"no requirement {jira}")
    registered = load_repos(root)
    if not registered:
        raise ValueError("no repos registered; add one with `dev-yard repo add`")

    if repos:
        missing = [a for a in repos if a not in registered]
        if missing:
            raise ValueError(f"unknown repo alias: {', '.join(missing)}")
        target_aliases = list(repos)
    else:
        target_aliases = sorted(registered)

    results: list[dict[str, Any]] = []
    for alias in target_aliases:
        repo = registered[alias]
        source = repo.source_path(root)
        if on_progress:
            on_progress(f"fetching {alias}...")
        wt = paths.req_worktree(root, jira, alias)
        has_wt = wt.exists() and (wt / ".git").exists()
        if repo.path:
            if not (source / ".git").exists():
                raise gitops.GitError(f"{alias}: {source} is not a git repo")
            gitops.fetch(source, on_progress=on_progress)
        else:
            gitops.ensure_clone(repo.url, source, on_progress=on_progress)
            if has_wt:
                gitops.fetch(source, on_progress=on_progress)
            else:
                gitops.checkout_default_base(source, repo.default_base)
        row: dict[str, Any] = {
            "repo": alias,
            "alias": alias,
            "strategy": strategy,
            "source": str(source),
            "worktree": str(wt) if has_wt else None,
            "status": "fetched",
        }
        if has_wt:
            if gitops.has_changes(wt):
                raise gitops.GitError(
                    f"{alias} worktree has uncommitted changes; commit or stash first"
                )
            ref = gitops.start_point(source, repo.default_base)
            before = gitops.head_sha(wt)
            if on_progress:
                on_progress(f"updating {alias} worktree onto {ref} ({strategy})")
            after = gitops.integrate_onto(wt, ref, strategy)
            row["ref"] = ref
            row["from"] = before
            row["to"] = after
            row["status"] = "up-to-date" if before == after else "synced"
            row["branch"] = gitops.current_branch(wt)
        else:
            try:
                row["ref"] = gitops.start_point(source, repo.default_base)
            except gitops.GitError:
                row["ref"] = repo.default_base
            row["status"] = "fetched"
        results.append(row)
        if on_progress:
            on_progress(f"{alias}: {row['status']}")
    return results


def req_test(root: Path, jira: str, **kwargs: Any):
    from dev_yard.qa import req_test as _req_test

    return _req_test(root, jira, **kwargs)

