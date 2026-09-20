"""Submit-test integration: merge freeze branches into per-repo test branches.

`submit_test` (test_report.py) validates the requirement, then hands the git
work to :func:`integrate_test_branches` here. The flow per repo is:

    fetch origin <test> -> detached worktree at origin/<test>
    -> merge <freeze> -> (AI resolve on conflict) -> push HEAD:refs/heads/<test>

The shared test branch is never checked out locally (detached + explicit push
ref), so several requirements can integrate in parallel without fighting over a
local branch. Only after every non-skipped repo pushed does the caller flip
`phase=testing`.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dev_yard import gitops, paths
from dev_yard import status as st
from dev_yard.config import (
    Repo,
    load_git_settings,
    load_repos,
    resolve_freeze_branch,
    resolve_pi_choice,
)
from dev_yard.runners import Runner, get_runner
from dev_yard.skillbind import session_prompt_for
from dev_yard.stages import RESOLVE_MERGE_SPEC

STATUS_PUSHED = "pushed"
STATUS_UNCHANGED = "unchanged"
STATUS_SKIPPED = "skipped"
STATUS_CONFLICT = "conflict"
STATUS_FAILED = "failed"

RunnerFactory = Callable[[Path, str, str], Runner]

# Process-wide, per (source repo, test branch) lock. `submit_test` releases the
# per-Jira lock around the long AI work, so this is what actually serialises
# two concurrent web jobs pushing the same shared test branch.
_BRANCH_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_BRANCH_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def branch_lock(source: Path, branch: str):
    key = (str(source), branch)
    with _BRANCH_LOCKS_GUARD:
        lock = _BRANCH_LOCKS.setdefault(key, threading.Lock())
    with lock:
        yield


class _IntegrationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: str = STATUS_FAILED,
        conflict: bool = False,
        keep_worktree: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.conflict = conflict
        self.keep_worktree = keep_worktree


@dataclass
class RepoIntegration:
    alias: str
    test_branch: str | None = None
    status: str = STATUS_SKIPPED
    freeze_sha: str | None = None
    test_sha_before: str | None = None
    merge_sha: str | None = None
    pushed: bool = False
    conflict: bool = False
    worktree: str | None = None
    pushed_at: str | None = None
    detail: str = ""

    def to_status(self) -> dict[str, Any]:
        return {
            "test_branch": self.test_branch,
            "freeze_sha": self.freeze_sha,
            "test_sha_before": self.test_sha_before,
            "merge_sha": self.merge_sha,
            "pushed": self.pushed,
            "skipped": self.status == STATUS_SKIPPED,
            "conflict": self.conflict,
            "worktree": self.worktree,
            "pushed_at": self.pushed_at,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class IntegrationOutcome:
    repos: list[RepoIntegration] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def status_map(self) -> dict[str, dict[str, Any]]:
        return {r.alias: r.to_status() for r in self.repos}

    def summary(self) -> str:
        """One line per repo: `alias=pushed <sha8>` / `alias=unchanged` / ..."""
        if not self.repos:
            return "(no repos to integrate)"
        parts = []
        for r in self.repos:
            if r.status == STATUS_PUSHED and r.merge_sha:
                label = f"pushed {r.merge_sha[:8]}"
            else:
                label = r.status
            parts.append(f"{r.alias}={label}")
        return ", ".join(parts)


def integration_report(data: dict[str, Any]) -> list[str]:
    """Human-readable lines for the `test.integration` slot (CLI + web job)."""
    integration = _integration_slot(data)
    lines: list[str] = []
    for alias, rec in sorted(integration.items()):
        status = rec.get("status") or ("pushed" if rec.get("pushed") else "skipped")
        lines.append(f"{alias}: {status} ({rec.get('test_branch') or '-'})")
    return lines


def _progress(on_progress: Callable[[str], None] | None, message: str) -> None:
    if on_progress is not None:
        on_progress(message)


def target_repos(root: Path, data: dict[str, Any]) -> list[tuple[str, Repo]]:
    """Repos referenced by this requirement's tickets, in stable order."""
    repos = load_repos(root)
    aliases = sorted(
        {
            str(slot["repo"])
            for slot in st.tickets_map(data.get("tickets")).values()
            if slot.get("repo")
        }
    )
    out: list[tuple[str, Repo]] = []
    for alias in aliases:
        repo = repos.get(alias)
        if repo is not None:
            out.append((alias, repo))
    return out


def eligible_repos(root: Path, data: dict[str, Any]) -> list[tuple[str, Repo]]:
    """Target repos that have a (non-empty) test_branch configured."""
    return [(a, r) for a, r in target_repos(root, data) if (r.test_branch or "").strip()]


def _freeze_branch(
    root: Path, jira: str, data: dict[str, Any], aliases: list[str]
) -> str:
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        if (wt / ".git").exists():
            return resolve_freeze_branch(root, jira, data, wt)
    return resolve_freeze_branch(root, jira, data)


def has_new_changes(root: Path, jira: str, data: dict[str, Any]) -> bool:
    """True when at least one eligible repo has unpushed or advanced freeze work.

    Used both for re-submit gating and board button state. Repos without a
    `test_branch` are ignored (they never integrate).
    """
    eligible = eligible_repos(root, data)
    if not eligible:
        return False
    integration = _integration_slot(data)
    freeze = _freeze_branch(root, jira, data, [a for a, _ in eligible])
    for alias, repo in eligible:
        record = integration.get(alias) or {}
        wt = paths.req_worktree(root, jira, alias)
        if (wt / ".git").exists() and gitops.has_changes(wt):
            return True
        head = gitops.rev_parse(repo.source_path(root), freeze)
        if not record.get("pushed"):
            return True
        if record.get("test_branch") != repo.test_branch:
            return True
        if head and record.get("freeze_sha") != head:
            return True
    return False


def _commit_freeze_pending(
    root: Path, jira: str, alias: str, on_progress: Callable[[str], None] | None
) -> None:
    """Fold uncommitted freeze-worktree edits into the freeze branch.

    Mirrors `req_push`: integration merges the freeze *branch*, so pending edits
    would otherwise be silently dropped from the test branch.
    """
    wt = paths.req_worktree(root, jira, alias)
    if not (wt / ".git").exists() or not gitops.has_changes(wt):
        return
    _progress(on_progress, f"{alias}: committing uncommitted freeze changes")
    gitops.commit_all(wt, f"chore: commit pending freeze changes ({jira})")


def _integration_slot(data: dict[str, Any]) -> dict[str, Any]:
    test = data.get("test")
    if not isinstance(test, dict):
        return {}
    integration = test.get("integration")
    return integration if isinstance(integration, dict) else {}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _default_runner_factory(root: Path, jira: str, alias: str) -> Runner:
    provider, model = resolve_pi_choice(root, "implement", repo=alias)
    return get_runner(
        root,
        RESOLVE_MERGE_SPEC.name,
        print_mode=True,
        spec=RESOLVE_MERGE_SPEC,
        provider=provider,
        model=model,
    )


def integrate_test_branches(
    root: Path,
    jira: str,
    *,
    remote: str = "origin",
    ai_resolve: bool | None = None,
    force_all: bool = False,
    on_progress: Callable[[str], None] | None = None,
    runner_factory: RunnerFactory | None = None,
) -> IntegrationOutcome:
    """Merge each eligible repo's freeze branch into its configured test branch."""
    settings = load_git_settings(root)
    if ai_resolve is None:
        ai_resolve = settings.ai_resolve_conflicts
    data = st.load(root, jira)
    targets = target_repos(root, data)
    if not targets:
        return IntegrationOutcome()
    integration = _integration_slot(data)
    freeze = _freeze_branch(root, jira, data, [a for a, _ in targets])

    results: list[RepoIntegration] = []
    error: str | None = None
    for alias, repo in targets:
        rec = RepoIntegration(alias=alias, test_branch=repo.test_branch)
        results.append(rec)
        if not (repo.test_branch or "").strip():
            rec.status = STATUS_SKIPPED
            rec.detail = "no test_branch configured"
            _progress(on_progress, f"{alias}: skipped (no test_branch)")
            continue
        try:
            _integrate_one(
                root,
                jira,
                alias,
                repo,
                freeze,
                integration,
                remote=remote,
                ai_resolve=bool(ai_resolve),
                force_all=force_all,
                on_progress=on_progress,
                runner_factory=runner_factory,
                rec=rec,
            )
        except _IntegrationError as e:
            rec.status = e.status
            rec.conflict = e.conflict
            rec.detail = str(e)
            if e.keep_worktree:
                rec.worktree = str(paths.test_merge_worktree(root, jira, alias))
            error = error or str(e)
            break
        except gitops.GitError as e:
            rec.status = STATUS_FAILED
            rec.detail = str(e)
            error = error or f"{alias}: {e}"
            break

    outcome = IntegrationOutcome(repos=results)
    if error:
        outcome.error = f"{error}\nrepos: {outcome.summary()}"
    return outcome


def _integrate_one(
    root: Path,
    jira: str,
    alias: str,
    repo: Repo,
    freeze: str,
    integration: dict[str, Any],
    *,
    remote: str,
    ai_resolve: bool,
    force_all: bool,
    on_progress: Callable[[str], None] | None,
    runner_factory: RunnerFactory | None,
    rec: RepoIntegration,
) -> None:
    source = repo.source_path(root)
    test = str(repo.test_branch)
    try:
        gitops.assert_branch_name(test)
    except ValueError as e:
        rec.status = STATUS_SKIPPED
        rec.detail = f"invalid test_branch {test!r}: {e}"
        _progress(on_progress, f"{alias}: skipped (invalid test_branch {test!r})")
        return

    _commit_freeze_pending(root, jira, alias, on_progress)
    freeze_sha = gitops.rev_parse(source, freeze)
    if not freeze_sha:
        raise _IntegrationError(
            f"{alias}: freeze branch {freeze!r} not found in {source}"
        )
    rec.freeze_sha = freeze_sha

    prev = integration.get(alias) or {}
    if (
        not force_all
        and prev.get("pushed")
        and prev.get("freeze_sha") == freeze_sha
        and prev.get("test_branch") == test
    ):
        rec.status = STATUS_UNCHANGED
        rec.pushed = True
        rec.test_sha_before = prev.get("test_sha_before")
        rec.merge_sha = prev.get("merge_sha")
        rec.pushed_at = prev.get("pushed_at")
        rec.detail = "freeze branch unchanged since last integration"
        _progress(on_progress, f"{alias}: unchanged ({freeze_sha[:8]}); skip")
        return

    with branch_lock(source, test):
        _merge_and_push(
            root,
            jira,
            alias,
            source,
            freeze,
            test,
            rec,
            remote=remote,
            ai_resolve=ai_resolve,
            on_progress=on_progress,
            runner_factory=runner_factory,
        )


def _merge_and_push(
    root: Path,
    jira: str,
    alias: str,
    source: Path,
    freeze: str,
    test: str,
    rec: RepoIntegration,
    *,
    remote: str,
    ai_resolve: bool,
    on_progress: Callable[[str], None] | None,
    runner_factory: RunnerFactory | None,
) -> None:
    merge_wt = paths.test_merge_worktree(root, jira, alias)

    def missing() -> _IntegrationError:
        return _IntegrationError(
            f"{alias}: remote test branch {test!r} not found on {remote}; "
            "create it first or fix repos.yaml"
        )

    _progress(on_progress, f"{alias}: fetch {remote} {test}")
    try:
        gitops.fetch_branch(source, remote, test, on_progress=on_progress)
    except gitops.GitError as e:
        message = str(e)
        if "remote ref" in message or "couldn't find" in message:
            raise missing() from e
        raise _IntegrationError(f"{alias}: fetch {test} failed: {message}") from e

    rec.test_sha_before = gitops.rev_parse(source, f"{remote}/{test}")
    if not rec.test_sha_before:
        raise missing()

    last_push_error: str = ""
    for attempt in (1, 2):
        _progress(
            on_progress,
            f"{alias}: merge {freeze} into {remote}/{test}"
            + (" (retry)" if attempt == 2 else ""),
        )
        gitops.detached_worktree(source, merge_wt, f"{remote}/{test}")
        rec.worktree = str(merge_wt)
        _do_merge(
            root,
            jira,
            alias,
            merge_wt,
            freeze,
            rec,
            ai_resolve=ai_resolve,
            on_progress=on_progress,
            runner_factory=runner_factory,
        )
        try:
            gitops.push_ref(
                merge_wt, remote, "HEAD", f"refs/heads/{test}", on_progress=on_progress
            )
        except gitops.GitError as e:
            last_push_error = str(e)
            if attempt == 2:
                break
            _progress(
                on_progress,
                f"{alias}: push rejected (non-fast-forward); refetching {test} and retrying",
            )
            gitops.fetch_branch(source, remote, test, on_progress=on_progress)
            continue
        rec.merge_sha = gitops.head_sha(merge_wt)
        rec.status = STATUS_PUSHED
        rec.pushed = True
        rec.conflict = False
        rec.pushed_at = _now_iso()
        rec.worktree = None
        rec.detail = ""
        gitops.worktree_remove(source, merge_wt)
        _progress(
            on_progress,
            f"{alias}: pushed {test} <- {freeze} ({rec.merge_sha[:8]})",
        )
        return

    raise _IntegrationError(
        f"{alias}: push {test} rejected after retry: {last_push_error}",
        status=STATUS_FAILED,
    )


def _do_merge(
    root: Path,
    jira: str,
    alias: str,
    merge_wt: Path,
    freeze: str,
    rec: RepoIntegration,
    *,
    ai_resolve: bool,
    on_progress: Callable[[str], None] | None,
    runner_factory: RunnerFactory | None,
) -> None:
    base = gitops.head_sha(merge_wt)
    try:
        gitops.merge_into(merge_wt, freeze)
        rec.conflict = False
        return
    except gitops.GitError as e:
        files = gitops.unmerged_files(merge_wt)
        if not files:
            raise _IntegrationError(f"{alias}: merge {freeze} failed: {e}") from e

    rec.conflict = True
    if not ai_resolve:
        raise _IntegrationError(
            f"{alias}: merge conflict in {len(files)} file(s): {', '.join(files)}\n"
            f"  worktree: {merge_wt}\n"
            f"  resolve manually, then re-run: dev-yard req submit-test {jira}",
            status=STATUS_CONFLICT,
            conflict=True,
            keep_worktree=True,
        )

    _progress(
        on_progress, f"{alias}: conflict in {len(files)} file(s); attempting AI resolve"
    )
    _ai_resolve(
        root,
        jira,
        alias,
        merge_wt,
        freeze,
        files,
        base=base,
        on_progress=on_progress,
        runner_factory=runner_factory,
    )


def _conflict_excerpts(merge_wt: Path, files: list[str], limit: int = 4000) -> str:
    chunks: list[str] = []
    for rel in files:
        try:
            text = (merge_wt / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        marked = [
            i
            for i, line in enumerate(lines)
            if line.startswith(("<<<<<<<", "=======", ">>>>>>>"))
        ]
        if not marked:
            continue
        lo = max(0, min(marked) - 2)
        hi = min(len(lines), max(marked) + 3)
        body = "\n".join(lines[lo:hi])
        if len(body) > 1200:
            body = body[:1200] + "\n…(truncated)"
        chunks.append(f"--- {rel} ---\n{body}")
    return "\n\n".join(chunks)[:limit]


def _ai_resolve(
    root: Path,
    jira: str,
    alias: str,
    merge_wt: Path,
    freeze: str,
    files: list[str],
    *,
    base: str,
    on_progress: Callable[[str], None] | None,
    runner_factory: RunnerFactory | None,
) -> None:
    log = ""
    try:
        log = gitops.run(
            ["git", "log", "--oneline", "--no-merges", f"HEAD..{freeze}"], cwd=merge_wt
        )
    except gitops.GitError:
        log = ""
    extra_parts = [
        f"Conflicted files ({len(files)}): {', '.join(files)}",
        f"Conflicting excerpts:\n{_conflict_excerpts(merge_wt, files)}",
        f"Commits this requirement brings (HEAD..{freeze}):\n{log or '(none)'}",
    ]
    prompt = session_prompt_for(RESOLVE_MERGE_SPEC, root, jira, extra="\n\n".join(extra_parts))
    factory = runner_factory or _default_runner_factory
    runner = factory(root, jira, alias)
    result = runner.start(prompt, merge_wt, [paths.req_dir(root, jira)])
    if not result.ok:
        raise _IntegrationError(
            f"{alias}: AI resolve-merge failed (exit={result.exit_code}): {result.summary}",
            status=STATUS_CONFLICT,
            conflict=True,
            keep_worktree=True,
        )
    _verify_resolution(alias, jira, merge_wt, base)
    _progress(on_progress, f"{alias}: AI resolved {len(files)} file(s)")


def _verify_resolution(alias: str, jira: str, merge_wt: Path, base: str) -> None:
    def fail(message: str) -> None:
        raise _IntegrationError(
            message,
            status=STATUS_CONFLICT,
            conflict=True,
            keep_worktree=True,
        )

    pending = gitops.unmerged_files(merge_wt)
    if pending:
        fail(f"{alias}: unresolved merge files after AI: {', '.join(pending)}")

    gitops.add_all(merge_wt)

    # If the agent resolved but did not commit, the staged diff still carries the
    # resolution; check it before committing. If the agent already committed,
    # MERGE_HEAD is gone and the markers (if any) live in the commit itself.
    if gitops.has_merge_head(merge_wt):
        markers = gitops.check_conflict_markers(merge_wt)
        if markers:
            fail(f"{alias}: leftover conflict markers: {'; '.join(markers)}")
        gitops.commit_all(merge_wt, f"merge: integrate freeze branch ({jira})")

    if gitops.has_changes(merge_wt):
        fail(f"{alias}: worktree still dirty after AI resolve")

    head = gitops.rev_parse(merge_wt, "HEAD")
    if not head or head == base:
        fail(f"{alias}: merge did not advance HEAD (still {base[:8]})")

    markers = gitops.check_commit_conflict_markers(merge_wt)
    if markers:
        fail(f"{alias}: leftover conflict markers in merge commit: {'; '.join(markers)}")
