import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dev_yard import status as st
from dev_yard.cli import app
from dev_yard.runners import DryRunRunner, RunResult
from dev_yard.service import (
    contract_review_override,
    implement,
    init_yard,
    repo_add,
    req_freeze,
    req_open,
    resolve_ticket_conflict,
    review,
    ticket_merge,
    ticket_review_override,
)

runner = CliRunner()


def _setup_req(tmp_path: Path, git_src: Path, key: str = "PROJ-101") -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, key, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: first task\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: second task\n- repo: backend\n- depends_on: T1\n- parallel: false\n"
    )
    req_freeze(yard, key)
    return yard


def test_review_override_pass(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _setup_req(tmp_path, git_src)

    # Implement T1
    implement(yard, "PROJ-101", ["T1"], runner=DryRunRunner())
    assert st.load(yard, "PROJ-101")["tickets"]["T1"]["state"] == "implemented"
    assert st.load(yard, "PROJ-101")["tickets"]["T2"]["state"] == "pending"

    # Human review override records the pass and does not merge.
    res = ticket_review_override(
        yard,
        "PROJ-101",
        "T1",
        verdict="passed",
        summary="Human approved: looks great!",
    )
    assert res["state"] == "approved"
    assert res["last_verdict"] == "passed"
    assert res["last_summary"] == "Human approved: looks great!"
    assert res["child_worktree"]
    data = st.load(yard, "PROJ-101")
    assert data["tickets"]["T1"]["state"] == "approved"
    assert data["tickets"]["T2"]["state"] == "pending"

    merged = ticket_merge(yard, "PROJ-101", "T1")
    assert merged["state"] == "done"
    assert "merge_conflict" not in merged
    data = st.load(yard, "PROJ-101")
    assert data["tickets"]["T1"]["state"] == "done"
    assert data["tickets"]["T2"]["state"] == "ready"


def test_merge_action_job_merges_approved_ticket(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _setup_req(tmp_path, git_src)
    implement(yard, "PROJ-101", ["T1"], runner=DryRunRunner())
    ticket_review_override(yard, "PROJ-101", "T1", verdict="passed", summary="ok")

    from dev_yard.web.jobs import JobRunner, default_execute

    job = JobRunner(yard, execute=default_execute, sync=True).submit(
        "merge", "PROJ-101", ticket_ids=["T1"]
    )
    assert job.state == "ok", job.log
    assert "T1 已合并" in job.log
    assert st.load(yard, "PROJ-101")["tickets"]["T1"]["state"] == "done"


def test_review_override_fail_and_reimplement(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _setup_req(tmp_path, git_src)

    # Implement T1
    implement(yard, "PROJ-101", ["T1"], runner=DryRunRunner())
    assert st.load(yard, "PROJ-101")["tickets"]["T1"]["state"] == "implemented"

    # Human review override -> failed with specific notes
    res = ticket_review_override(
        yard,
        "PROJ-101",
        "T1",
        verdict="failed",
        summary="Please add null check and unit test for error case.",
    )
    assert res["state"] == "blocked"
    assert res["last_verdict"] == "failed"
    assert res["last_summary"] == "Please add null check and unit test for error case."

    # Now verify implement picks it up when targeted and runs
    ran = implement(yard, "PROJ-101", ["T1"], runner=DryRunRunner())
    assert ran == ["T1"]
    assert st.load(yard, "PROJ-101")["tickets"]["T1"]["state"] == "implemented"


def test_review_override_invalid_verdict_or_ticket(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _setup_req(tmp_path, git_src)

    with pytest.raises(ValueError, match="invalid verdict"):
        ticket_review_override(yard, "PROJ-101", "T1", verdict="unknown")

    with pytest.raises(ValueError, match="unknown ticket"):
        ticket_review_override(yard, "PROJ-101", "NONEXISTENT", verdict="passed")


def test_cli_review_override(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _setup_req(tmp_path, git_src)
    monkeypatch.chdir(yard)

    implement(yard, "PROJ-101", ["T1"], runner=DryRunRunner())

    # CLI review-override --verdict failed --summary "Needs fix"
    res = runner.invoke(
        app,
        ["review-override", "PROJ-101", "T1", "-v", "failed", "-m", "Needs fix"],
    )
    assert res.exit_code == 0
    assert "Updated T1: state=blocked" in res.stdout

    slot = st.load(yard, "PROJ-101")["tickets"]["T1"]
    assert slot["state"] == "blocked"
    assert "Needs fix" in slot["last_summary"]

    # CLI review-override --verdict passed
    res2 = runner.invoke(
        app,
        ["review-override", "PROJ-101", "T1", "-v", "passed", "-m", "All good now"],
    )
    assert res2.exit_code == 0
    assert "Updated T1: state=approved" in res2.stdout

    slot2 = st.load(yard, "PROJ-101")["tickets"]["T1"]
    assert slot2["state"] == "approved"
    assert slot2["last_verdict"] == "passed"
    assert slot2["last_summary"] == "All good now"


def test_contract_review_override(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _setup_req(tmp_path, git_src)
    monkeypatch.chdir(yard)

    # Human contract review override -> failed
    res = contract_review_override(
        yard,
        "PROJ-101",
        verdict="failed",
        summary="Missing auth header in RPC call.",
    )
    assert res["contract_review"] == "failed"
    assert res["contract_summary"] == "Missing auth header in RPC call."

    data = st.load(yard, "PROJ-101")
    assert data["contract_review"] == "failed"
    assert "Missing auth header" in data["contract_summary"]

    # CLI review-override --contract --verdict passed
    res2 = runner.invoke(
        app,
        ["review-override", "PROJ-101", "--contract", "-v", "passed", "-m", "All contracts aligned"],
    )
    assert res2.exit_code == 0
    assert "Updated contract review: passed" in res2.stdout

    data2 = st.load(yard, "PROJ-101")
    assert data2["contract_review"] == "passed"
    assert data2["contract_summary"] == "All contracts aligned"


def test_merge_conflict_stays_approved_and_resolves_without_rereview(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-33", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-33")

    class Writer:
        def __init__(self) -> None:
            self.n = 0

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    writer = Writer()
    implement(yard, "AB-33", ["T1"], runner=writer)
    implement(yard, "AB-33", ["T2"], runner=writer)
    ticket_review_override(yard, "AB-33", "T1", verdict="passed", summary="T1 ok")
    ticket_merge(yard, "AB-33", "T1")
    ticket_review_override(yard, "AB-33", "T2", verdict="passed", summary="T2 ok")

    with pytest.raises(ValueError, match="不用重新审查"):
        ticket_merge(yard, "AB-33", "T2")

    parent = yard / "reqs" / "AB-33" / "worktrees" / "backend"
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=parent)
    assert b"UU" not in status
    slot = st.load(yard, "AB-33")["tickets"]["T2"]
    assert slot["state"] == "approved"
    assert slot["last_verdict"] == "passed"
    assert slot["last_summary"] == "T2 ok"
    assert "冲突" in slot["merge_conflict"]
    assert slot["child_worktree"]

    seen: list[str] = []

    reads: list[list[Path]] = []

    class Resolver:
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            seen.append(prompt)
            reads.append(list(extra_read_paths))
            (cwd / "shared.txt").write_text("both\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            return RunResult(ok=True, summary="resolved")

    resolved = resolve_ticket_conflict(yard, "AB-33", "T2", runner=Resolver())
    assert resolved["state"] == "approved"
    assert "merge_conflict" not in resolved
    assert seen and "already passed" in seen[0]
    assert "Do not change behavior to match SPEC.md" in seen[0]
    assert "再走实现/审查" not in seen[0]
    assert "<<<<<<< HEAD is this ticket" in seen[0]
    assert reads and all(path.name != "SPEC.md" for path in reads[0])
    assert any(path.name == "TICKETS.md" for path in reads[0])

    merged = ticket_merge(yard, "AB-33", "T2")
    assert merged["state"] == "done"
    assert (parent / "shared.txt").read_text(encoding="utf-8") == "both\n"


def test_resolve_skill_names_ticket_as_ours():
    text = (
        Path(__file__).resolve().parents[1] / ".pi" / "skills" / "resolve-ticket" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "HEAD" in text and "ours" in text
    assert text.index("已通过的票") < text.index("兄弟票已经合进去")


def test_resolve_keeps_ticket_side_and_commits_merge(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-34", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-34")

    class Writer:
        def __init__(self) -> None:
            self.n = 0

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    writer = Writer()
    implement(yard, "AB-34", ["T1"], runner=writer)
    implement(yard, "AB-34", ["T2"], runner=writer)
    ticket_review_override(yard, "AB-34", "T1", verdict="passed", summary="T1 ok")
    ticket_merge(yard, "AB-34", "T1")
    ticket_review_override(yard, "AB-34", "T2", verdict="passed", summary="T2 ok")
    with pytest.raises(ValueError, match="不用重新审查"):
        ticket_merge(yard, "AB-34", "T2")

    class KeepOurs:
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            subprocess.check_call(["git", "checkout", "--ours", "--", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            return RunResult(ok=True, summary="kept")

    child = Path(st.load(yard, "AB-34")["tickets"]["T2"]["child_worktree"])
    before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=child).decode().strip()
    resolved = resolve_ticket_conflict(yard, "AB-34", "T2", runner=KeepOurs())
    assert "merge_conflict" not in resolved
    after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=child).decode().strip()
    assert after != before
    subprocess.check_call(["git", "rev-parse", "--verify", "HEAD^2"], cwd=child)
    merge_head = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        cwd=child,
        capture_output=True,
    )
    assert merge_head.returncode != 0
    merged = ticket_merge(yard, "AB-34", "T2")
    assert merged["state"] == "done"
    parent = yard / "reqs" / "AB-34" / "worktrees" / "backend"
    assert (parent / "shared.txt").read_text(encoding="utf-8") == "side 2\n"


def test_merge_git_error_without_conflict_stays_approved(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard import gitops

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-35", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-35")

    class Writer:
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            (cwd / "shared.txt").write_text("ticket\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", "c"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    implement(yard, "AB-35", ["T1"], runner=Writer())
    ticket_review_override(yard, "AB-35", "T1", verdict="passed", summary="ok")
    parent = yard / "reqs" / "AB-35" / "worktrees" / "backend"
    (parent / "shared.txt").write_text("dirty\n")
    with pytest.raises(gitops.GitError):
        ticket_merge(yard, "AB-35", "T1")
    slot = st.load(yard, "AB-35")["tickets"]["T1"]
    assert slot["state"] == "approved"
    assert slot["last_verdict"] == "passed"
    assert "merge_conflict" not in slot
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=parent)
    assert b"UU" not in status


def test_resolve_does_not_overwrite_a_rejection(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-36", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-36")

    class Writer:
        def __init__(self) -> None:
            self.n = 0

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    writer = Writer()
    implement(yard, "AB-36", ["T1"], runner=writer)
    implement(yard, "AB-36", ["T2"], runner=writer)
    ticket_review_override(yard, "AB-36", "T1", verdict="passed", summary="T1 ok")
    ticket_merge(yard, "AB-36", "T1")
    ticket_review_override(yard, "AB-36", "T2", verdict="passed", summary="T2 ok")
    with pytest.raises(ValueError, match="不用重新审查"):
        ticket_merge(yard, "AB-36", "T2")

    class RejectDuringResolve:
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            (cwd / "shared.txt").write_text("both\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            ticket_review_override(yard, "AB-36", "T2", verdict="failed", summary="nope")
            return RunResult(ok=True, summary="resolved")

    with pytest.raises(ValueError, match="已不是通过状态"):
        resolve_ticket_conflict(yard, "AB-36", "T2", runner=RejectDuringResolve())
    slot = st.load(yard, "AB-36")["tickets"]["T2"]
    assert slot["state"] == "blocked"
    assert slot["last_verdict"] == "failed"
    assert slot["last_summary"] == "nope"
    child = Path(slot["child_worktree"])
    verify = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        cwd=child,
        capture_output=True,
        text=True,
    )
    assert verify.returncode != 0


def test_resolve_refuses_committed_conflict_markers(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-37", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-37")

    class Writer:
        def __init__(self) -> None:
            self.n = 0

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    writer = Writer()
    implement(yard, "AB-37", ["T1"], runner=writer)
    implement(yard, "AB-37", ["T2"], runner=writer)
    ticket_review_override(yard, "AB-37", "T1", verdict="passed", summary="T1 ok")
    ticket_merge(yard, "AB-37", "T1")
    ticket_review_override(yard, "AB-37", "T2", verdict="passed", summary="T2 ok")
    with pytest.raises(ValueError, match="不用重新审查"):
        ticket_merge(yard, "AB-37", "T2")

    class CommitMarkers:
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            (cwd / "shared.txt").write_text("<<<<<<< HEAD\nticket\n=======\nparent\n>>>>>>> freeze\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", "bad merge"], cwd=cwd)
            return RunResult(ok=True, summary="committed markers")

    with pytest.raises(RuntimeError, match="冲突还在"):
        resolve_ticket_conflict(yard, "AB-37", "T2", runner=CommitMarkers())
    slot = st.load(yard, "AB-37")["tickets"]["T2"]
    assert slot["state"] == "approved"
    assert "merge_conflict" in slot
    child = Path(slot["child_worktree"])
    assert "<<<<<<<" not in (child / "shared.txt").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="不用重新审查"):
        ticket_merge(yard, "AB-37", "T2")


class _PassReview(DryRunRunner):
    def start(self, prompt, cwd, extra_read_paths, repo=None) -> RunResult:
        return RunResult(ok=True, summary="looks good", verdict="passed")


def test_review_batch_survives_a_merge_error_that_is_not_a_conflict(
    tmp_path: Path, git_src: Path, monkeypatch
):
    """One ticket failing to merge must not abort the rest of the batch.

    A dirty parent worktree makes `git merge` refuse without leaving unmerged
    paths, so `_parent_content_conflict` is False. The review itself still
    passed: record it, keep going, and leave the ticket mergeable.
    """
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-40", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-40")

    class Writer:
        def __init__(self) -> None:
            self.n = 0

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    implement(yard, "AB-40", ["T1", "T2"], runner=Writer())
    parent = yard / "reqs" / "AB-40" / "worktrees" / "backend"
    (parent / "shared.txt").write_text("dirty\n")

    ran = review(yard, "AB-40", ["T1", "T2"], runner=_PassReview())
    assert set(ran) == {"T1", "T2"}
    data = st.load(yard, "AB-40")
    for tid in ("T1", "T2"):
        slot = data["tickets"][tid]
        assert slot["state"] == "approved"
        assert slot["last_verdict"] == "passed"
        assert "merge_conflict" not in slot
        assert slot["last_summary"].startswith("looks good")
        assert "shared.txt" in slot["last_summary"]
