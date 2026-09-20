"""submit-test = merge freeze branch into each repo's test_branch and push."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_yard import gitops, paths
from dev_yard import status as st
from dev_yard.runners import DryRunRunner, RunResult
from dev_yard.service import (
    implement,
    init_yard,
    repo_add,
    repo_set_pi,
    req_freeze,
    req_open,
    review,
)
from dev_yard.test_report import ReportRejected, submit_test


@pytest.fixture(autouse=True)
def _no_jira_env(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True)


def _bare_show(remote: Path, ref: str) -> str:
    return subprocess.check_output(
        ["git", f"--git-dir={remote}", "show", ref], text=True
    )


def _bare_rev(remote: Path, ref: str) -> str:
    return subprocess.check_output(
        ["git", f"--git-dir={remote}", "rev-parse", ref], text=True
    ).strip()


def _make_repo(
    tmp_path: Path, test_branch: str = "PG-test", *, conflict: bool = False
) -> tuple[Path, Path]:
    remote = tmp_path / "origin.git"
    subprocess.check_call(["git", "init", "--bare", "-q", str(remote)])
    work = tmp_path / "work"
    subprocess.check_call(["git", "clone", "-q", str(remote), str(work)])
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    (work / "base.txt").write_text("base\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-q", "-u", "origin", "main")
    _git(work, "checkout", "-q", "-b", test_branch)
    if conflict:
        (work / "shared.txt").write_text("test-branch version\n")
    else:
        (work / "test-only.txt").write_text("test branch only\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "test branch commit")
    _git(work, "push", "-q", "-u", "origin", test_branch)
    _git(work, "checkout", "-q", "main")
    return remote, work


def _frozen_with_contract(
    yard: Path, key: str, remote: Path, work: Path, test_branch: str | None = "PG-test"
) -> None:
    repo_add(yard, "backend", str(remote), "main", "be", str(work))
    if test_branch:
        repo_set_pi(yard, "backend", None, None, test_branch=test_branch)
    d, _ = req_open(yard, key, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, key)
    implement(yard, key, None, runner=DryRunRunner())
    review(yard, key, None, runner=DryRunRunner())
    review(yard, key, None, contract=True, runner=DryRunRunner())


def _commit_in_freeze(yard: Path, key: str, name: str, text: str) -> str:
    wt = paths.req_worktree(yard, key, "backend")
    (wt / name).write_text(text)
    sha = gitops.commit_all(wt, f"feat: {name}")
    assert sha
    return sha


class _ResolvingRunner:
    def __init__(self, content: dict[str, str], *, commit: bool = True) -> None:
        self.content = content
        self.commit = commit

    def start(self, prompt, cwd, extra, repo=None):
        for rel, text in self.content.items():
            path = Path(cwd) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        gitops.add_all(Path(cwd))
        if self.commit:
            gitops.commit_all(Path(cwd), "resolve conflict")
        return RunResult(ok=True, summary="resolved", exit_code=0)


def _resolving_factory(content: dict[str, str], *, commit: bool = True):
    return lambda root, jira, alias: _ResolvingRunner(content, commit=commit)


def test_submit_test_merges_and_pushes_test_branch(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-60", remote, work)
    _commit_in_freeze(yard, "AB-60", "feature.txt", "feature\n")

    data = submit_test(yard, "AB-60")

    assert data["phase"] == "testing"
    rec = data["test"]["integration"]["backend"]
    assert rec["status"] == "pushed"
    assert rec["pushed"] is True
    assert rec["test_branch"] == "PG-test"
    assert "feature" in _bare_show(remote, "PG-test:feature.txt")
    assert "test branch only" in _bare_show(remote, "PG-test:test-only.txt")


def test_repo_without_test_branch_is_skipped(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path)
    before = _bare_rev(remote, "PG-test")
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-61", remote, work, test_branch=None)
    _commit_in_freeze(yard, "AB-61", "feature.txt", "feature\n")

    data = submit_test(yard, "AB-61")

    assert data["phase"] == "testing"
    rec = data["test"]["integration"]["backend"]
    assert rec["status"] == "skipped"
    assert rec["pushed"] is False
    assert _bare_rev(remote, "PG-test") == before


def test_conflict_without_resolve_stops_and_keeps_worktree(
    tmp_path: Path, monkeypatch
):
    remote, work = _make_repo(tmp_path, conflict=True)
    before = _bare_rev(remote, "PG-test")
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-62", remote, work)
    _commit_in_freeze(yard, "AB-62", "shared.txt", "freeze version\n")

    with pytest.raises(ReportRejected, match="conflict"):
        submit_test(yard, "AB-62", ai_resolve=False)

    data = st.load(yard, "AB-62")
    assert data["phase"] == "frozen"
    rec = data["test"]["integration"]["backend"]
    assert rec["conflict"] is True
    assert _bare_rev(remote, "PG-test") == before
    assert Path(rec["worktree"]).is_dir()


def test_conflict_ai_resolves_and_pushes(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path, conflict=True)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-63", remote, work)
    _commit_in_freeze(yard, "AB-63", "shared.txt", "freeze version\n")

    data = submit_test(
        yard,
        "AB-63",
        runner_factory=_resolving_factory({"shared.txt": "merged version\n"}),
    )

    assert data["phase"] == "testing"
    rec = data["test"]["integration"]["backend"]
    assert rec["status"] == "pushed"
    assert rec["conflict"] is False
    assert "merged version" in _bare_show(remote, "PG-test:shared.txt")


def test_conflict_ai_resolves_without_committing(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path, conflict=True)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-69", remote, work)
    _commit_in_freeze(yard, "AB-69", "shared.txt", "freeze version\n")

    data = submit_test(
        yard,
        "AB-69",
        runner_factory=_resolving_factory({"shared.txt": "merged\n"}, commit=False),
    )

    assert data["phase"] == "testing"
    assert "merged" in _bare_show(remote, "PG-test:shared.txt")


def test_conflict_ai_leaves_markers_fails(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path, conflict=True)
    before = _bare_rev(remote, "PG-test")
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-64", remote, work)
    _commit_in_freeze(yard, "AB-64", "shared.txt", "freeze version\n")

    bad = "<<<<<<< HEAD\ntest\n=======\nfreeze\n>>>>>>> req\n"
    with pytest.raises(ReportRejected, match="conflict marker"):
        submit_test(yard, "AB-64", runner_factory=_resolving_factory({"shared.txt": bad}))

    assert st.load(yard, "AB-64")["phase"] == "frozen"
    assert _bare_rev(remote, "PG-test") == before


def test_resubmit_is_incremental_and_idempotent(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-65", remote, work)
    _commit_in_freeze(yard, "AB-65", "feature.txt", "feature\n")

    submit_test(yard, "AB-65")
    first = _bare_rev(remote, "PG-test")

    # Incremental engine sees no freeze change and skips the repo entirely.
    from dev_yard import test_integrate

    outcome = test_integrate.integrate_test_branches(yard, "AB-65")
    assert outcome.repos[0].status == "unchanged"
    assert _bare_rev(remote, "PG-test") == first

    # Idempotent submit: returns without error, pushes nothing.
    again = submit_test(yard, "AB-65")
    assert again["phase"] == "testing"
    assert _bare_rev(remote, "PG-test") == first

    # New fix on the freeze branch: re-submit pushes again.
    _commit_in_freeze(yard, "AB-65", "fix.txt", "fix\n")
    submit_test(yard, "AB-65")
    second = _bare_rev(remote, "PG-test")
    assert second != first
    assert "fix" in _bare_show(remote, "PG-test:fix.txt")


def test_remote_test_branch_missing_reports_clear_error(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-66", remote, work, test_branch="nope-test")
    _commit_in_freeze(yard, "AB-66", "feature.txt", "feature\n")

    with pytest.raises(ReportRejected, match="not found"):
        submit_test(yard, "AB-66")

    assert st.load(yard, "AB-66")["phase"] == "frozen"


def test_teardown_cleans_scratch_test_merge_worktree(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path, conflict=True)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-67", remote, work)
    _commit_in_freeze(yard, "AB-67", "shared.txt", "freeze version\n")

    with pytest.raises(ReportRejected):
        submit_test(yard, "AB-67", ai_resolve=False)

    scratch = paths.test_merge_worktree(yard, "AB-67", "backend")
    assert scratch.is_dir()

    from dev_yard.service import req_delete

    req_delete(yard, "AB-67")
    assert not scratch.exists()


def test_board_gates_resubmit_on_new_changes(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-68", remote, work)
    _commit_in_freeze(yard, "AB-68", "feature.txt", "feature\n")
    submit_test(yard, "AB-68")

    from dev_yard.reqboard import requirement_detail

    actions = {a.id: a for a in requirement_detail(yard, "AB-68").actions}
    assert not actions["submit-test"].enabled
    assert actions["submit-test"].reason == "没有新的改动"

    _commit_in_freeze(yard, "AB-68", "more.txt", "more\n")
    actions = {a.id: a for a in requirement_detail(yard, "AB-68").actions}
    assert actions["submit-test"].enabled
    assert actions["submit-test"].label == "重新提测"


def test_uncommitted_freeze_changes_are_committed(tmp_path: Path):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-70", remote, work)
    wt = paths.req_worktree(yard, "AB-70", "backend")
    (wt / "dirty.txt").write_text("uncommitted\n")  # never committed

    from dev_yard import test_integrate

    assert test_integrate.has_new_changes(yard, "AB-70", st.load(yard, "AB-70"))

    data = submit_test(yard, "AB-70")
    assert data["phase"] == "testing"
    assert "uncommitted" in _bare_show(remote, "PG-test:dirty.txt")


def test_non_fast_forward_push_is_retried(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-71", remote, work)
    _commit_in_freeze(yard, "AB-71", "feature.txt", "feature\n")

    calls = {"n": 0}
    real_push = gitops.push_ref

    def flaky(worktree, remote, src, dst, **kwargs):
        # Only the test-branch push is flaky; the freeze-branch push is not.
        if dst.endswith("/PG-test"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise gitops.GitError("! [rejected] PG-test (non-fast-forward)")
        return real_push(worktree, remote, src, dst, **kwargs)

    monkeypatch.setattr(gitops, "push_ref", flaky)
    data = submit_test(yard, "AB-71")
    assert data["phase"] == "testing"
    assert calls["n"] == 2
    assert "feature" in _bare_show(remote, "PG-test:feature.txt")


def test_force_all_reintegrates_unchanged(tmp_path: Path):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-72", remote, work)
    _commit_in_freeze(yard, "AB-72", "feature.txt", "feature\n")
    submit_test(yard, "AB-72")

    data = submit_test(yard, "AB-72", force_all=True)
    assert data["test"]["integration"]["backend"]["status"] == "pushed"


def test_phase_change_during_long_run_aborts_without_clobber(tmp_path: Path):
    remote, work = _make_repo(tmp_path, conflict=True)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-73", remote, work)
    _commit_in_freeze(yard, "AB-73", "shared.txt", "freeze version\n")

    def factory(root: Path, jira: str, alias: str):
        class R(_ResolvingRunner):
            def start(self, prompt, cwd, extra, repo=None):
                with st.jira_lock(jira):
                    d = st.load(root, jira)
                    d["phase"] = "open"
                    st.save(root, jira, d)
                return super().start(prompt, cwd, extra, repo=repo)

        return R({"shared.txt": "merged\n"})

    with pytest.raises(ReportRejected, match="phase changed"):
        submit_test(yard, "AB-73", runner_factory=factory)
    assert st.load(yard, "AB-73")["phase"] == "open"


def test_setext_and_trailing_whitespace_not_misjudged(tmp_path: Path):
    remote, work = _make_repo(tmp_path, conflict=True)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-74", remote, work)
    _commit_in_freeze(yard, "AB-74", "shared.txt", "freeze version\n")

    # A legitimate setext heading + trailing whitespace, not a conflict marker.
    content = "Title\n=======\nbody   \n"
    data = submit_test(
        yard, "AB-74", runner_factory=_resolving_factory({"shared.txt": content})
    )
    assert data["phase"] == "testing"
    assert "=======" in _bare_show(remote, "PG-test:shared.txt")


def test_submit_test_also_pushes_freeze_branch(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-75", remote, work)
    _commit_in_freeze(yard, "AB-75", "feature.txt", "feature\n")

    from dev_yard.config import resolve_freeze_branch

    branch = resolve_freeze_branch(yard, "AB-75", st.load(yard, "AB-75"))
    with pytest.raises(subprocess.CalledProcessError):
        _bare_rev(remote, branch)

    data = submit_test(yard, "AB-75")

    rec = data["test"]["integration"]["backend"]
    assert rec["freeze_pushed"] is True
    assert "feature" in _bare_show(remote, f"{branch}:feature.txt")


def test_resubmit_unchanged_still_pushes_missing_freeze(tmp_path: Path, monkeypatch):
    remote, work = _make_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    _frozen_with_contract(yard, "AB-76", remote, work)
    _commit_in_freeze(yard, "AB-76", "feature.txt", "feature\n")
    submit_test(yard, "AB-76")

    from dev_yard import test_integrate
    from dev_yard.config import resolve_freeze_branch

    branch = resolve_freeze_branch(yard, "AB-76", st.load(yard, "AB-76"))
    # Simulate an old integration record whose freeze branch was never published.
    data = st.load(yard, "AB-76")
    rec = data["test"]["integration"]["backend"]
    rec.pop("freeze_pushed", None)
    st.save(yard, "AB-76", data)
    _git(remote, "branch", "-D", branch)
    with pytest.raises(subprocess.CalledProcessError):
        _bare_rev(remote, branch)

    outcome = test_integrate.integrate_test_branches(yard, "AB-76")
    assert outcome.repos[0].status == "unchanged"
    assert outcome.repos[0].freeze_pushed is True
    assert "feature" in _bare_show(remote, f"{branch}:feature.txt")
