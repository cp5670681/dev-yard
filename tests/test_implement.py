from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.gitops import GitError
from dev_yard.runners import DryRunRunner, RunResult, clip_summary
from dev_yard.service import (
    from_contract_ids,
    implement,
    init_yard,
    repo_add,
    req_freeze,
    req_open,
    review,
)


def test_implement_dry_run(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-8", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-8")
    ran = implement(yard, "AB-8", None, runner=DryRunRunner())
    assert ran == ["T1"]
    reviewed = review(yard, "AB-8", None, runner=DryRunRunner())
    assert reviewed == ["T1"]
    contracted = review(yard, "AB-8", None, contract=True, runner=DryRunRunner())
    assert contracted == ["__contract__"]
    assert st.load(yard, "AB-8")["tickets"]["T1"]["state"] == "done"


def test_cli_dry_run_does_not_mutate_status(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-16", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-16")
    assert st.load(yard, "AB-16")["tickets"]["T1"]["state"] == "ready"
    ran = implement(yard, "AB-16", None, dry_run=True)
    assert ran == ["T1"]
    assert st.load(yard, "AB-16")["tickets"]["T1"]["state"] == "ready"
    implement(yard, "AB-16", None, runner=DryRunRunner())
    reviewed = review(yard, "AB-16", None, dry_run=True)
    assert reviewed == ["T1"]
    assert st.load(yard, "AB-16")["tickets"]["T1"]["state"] == "implemented"


def _ready_req(tmp_path: Path, git_src: Path, key: str) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, key, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, key)
    return yard


def test_implement_retries_implementing(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-11")
    data = st.load(yard, "AB-11")
    data["tickets"]["T1"]["state"] = "implementing"
    st.save(yard, "AB-11", data)
    ran = implement(yard, "AB-11", None, runner=DryRunRunner())
    assert ran == ["T1"]
    assert st.load(yard, "AB-11")["tickets"]["T1"]["state"] == "implemented"


def test_contract_review_requires_freeze(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-12", source="none")
    with pytest.raises(ValueError, match="freeze first"):
        review(yard, "AB-12", None, contract=True, runner=DryRunRunner())


def test_implement_after_tickets_rewrite(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-13")
    d = yard / "reqs" / "AB-13"
    (d / "TICKETS.md").write_text(
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    ran = implement(yard, "AB-13", None, runner=DryRunRunner())
    assert ran == ["T2"]
    data = st.load(yard, "AB-13")
    assert "T1" not in data["tickets"]


def test_review_drops_orphan_tickets(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-17")
    implement(yard, "AB-17", None, runner=DryRunRunner())
    d = yard / "reqs" / "AB-17"
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
        "## T9: gone\n- repo: backend\n"
    )
    data = st.load(yard, "AB-17")
    data["tickets"]["T9"] = {"state": "pending", "repo": "backend"}
    st.save(yard, "AB-17", data)
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    review(yard, "AB-17", None, runner=DryRunRunner())
    assert "T9" not in st.load(yard, "AB-17")["tickets"]


def test_skeleton_cannot_freeze(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-14", source="none")
    with pytest.raises(ValueError, match="no tickets"):
        req_freeze(yard, "AB-14")


class _FailReview(DryRunRunner):
    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path]) -> RunResult:
        return RunResult(ok=True, summary="nits\nREVIEW_FAILED\n", exit_code=0)


def test_same_repo_ready_ticket_gets_child_while_sibling_implementing(
    tmp_path: Path, git_src: Path, monkeypatch
):
    import threading

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-18", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-18")
    t1_in = threading.Event()
    release = threading.Event()

    class Gate:
        def start(self, prompt, cwd, extra_read_paths):
            if "Ticket: T1" in prompt:
                t1_in.set()
                release.wait(timeout=5)
            return RunResult(ok=True, summary="ok")

    t1_done = threading.Event()

    def run_t1():
        implement(yard, "AB-18", ["T1"], runner=Gate())
        t1_done.set()

    threading.Thread(target=run_t1, daemon=True).start()
    assert t1_in.wait(timeout=5)
    ran = implement(yard, "AB-18", ["T2"], runner=Gate())
    assert ran == ["T2"]
    data = st.load(yard, "AB-18")
    assert data["tickets"]["T1"]["state"] == "implementing"
    assert data["tickets"]["T2"]["state"] == "implemented"
    assert data["tickets"]["T2"]["child_worktree"]
    release.set()
    assert t1_done.wait(timeout=5)
    assert st.load(yard, "AB-18")["tickets"]["T1"]["state"] == "implemented"


def test_review_marker_blocks_even_on_exit_zero(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-15")
    implement(yard, "AB-15", None, runner=DryRunRunner())
    review(yard, "AB-15", None, runner=_FailReview())
    assert st.load(yard, "AB-15")["tickets"]["T1"]["state"] == "blocked"


class _Capture:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def start(self, prompt, cwd, extra_read_paths):
        self.prompts.append(prompt)
        return RunResult(ok=True, summary="fixed")


def test_implement_blocked_after_review_includes_report(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-19")
    implement(yard, "AB-19", None, runner=DryRunRunner())
    review(yard, "AB-19", None, runner=_FailReview())
    cap = _Capture()
    ran = implement(yard, "AB-19", ["T1"], runner=cap)
    assert ran == ["T1"]
    assert "Previous review failed" in cap.prompts[0]
    assert "REVIEW_FAILED" in cap.prompts[0]
    assert "nits" in cap.prompts[0]
    assert st.load(yard, "AB-19")["tickets"]["T1"]["state"] == "implemented"


def test_implement_ready_does_not_include_review_report(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-20")
    cap = _Capture()
    implement(yard, "AB-20", None, runner=cap)
    assert "Previous review failed" not in cap.prompts[0]


def test_implement_blocked_without_review_marker_skips_report(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-21")
    data = st.load(yard, "AB-21")
    data["tickets"]["T1"]["state"] = "blocked"
    data["tickets"]["T1"]["last_summary"] = "pi exit 1"
    st.save(yard, "AB-21", data)
    cap = _Capture()
    implement(yard, "AB-21", ["T1"], runner=cap)
    assert "Previous review failed" not in cap.prompts[0]


def test_from_contract_requires_summary(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-22")
    with pytest.raises(ValueError, match="contract_summary"):
        implement(yard, "AB-22", None, from_contract=True, runner=DryRunRunner())


def test_from_contract_picks_last_ticket_per_repo_and_injects_report(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-23", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on: T1\n- parallel: false\n"
    )
    req_freeze(yard, "AB-23")
    implement(yard, "AB-23", None, runner=DryRunRunner())
    review(yard, "AB-23", None, runner=DryRunRunner())
    implement(yard, "AB-23", ["T2"], runner=DryRunRunner())
    review(yard, "AB-23", ["T2"], runner=DryRunRunner())
    data = st.load(yard, "AB-23")
    data["phase"] = "done"
    data["contract_review"] = "passed"
    data["contract_summary"] = "CONTRACT DEFECT: missing follow_members_names"
    st.save(yard, "AB-23", data)
    cap = _Capture()
    ran = implement(yard, "AB-23", None, from_contract=True, runner=cap)
    assert ran == ["T2"]
    assert "Previous contract review" in cap.prompts[0]
    assert "follow_members_names" in cap.prompts[0]
    after = st.load(yard, "AB-23")
    assert after["tickets"]["T2"]["state"] == "implemented"
    assert after["tickets"]["T1"]["state"] == "done"
    assert after["phase"] == "frozen"


def test_from_contract_explicit_ids(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-24")
    implement(yard, "AB-24", None, runner=DryRunRunner())
    review(yard, "AB-24", None, runner=DryRunRunner())
    data = st.load(yard, "AB-24")
    data["contract_summary"] = "gap in T1"
    st.save(yard, "AB-24", data)
    cap = _Capture()
    ran = implement(yard, "AB-24", ["T1"], from_contract=True, runner=cap)
    assert ran == ["T1"]
    assert "gap in T1" in cap.prompts[0]


def test_from_contract_dry_run_does_not_mutate(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-25")
    implement(yard, "AB-25", None, runner=DryRunRunner())
    review(yard, "AB-25", None, runner=DryRunRunner())
    data = st.load(yard, "AB-25")
    data["phase"] = "done"
    data["contract_summary"] = "nits"
    st.save(yard, "AB-25", data)
    ran = implement(yard, "AB-25", None, from_contract=True, dry_run=True)
    assert ran == ["T1"]
    after = st.load(yard, "AB-25")
    assert after["tickets"]["T1"]["state"] == "done"
    assert after["phase"] == "done"


def test_from_contract_ids_last_per_repo():
    class T:
        def __init__(self, repo):
            self.repo = repo

    tickets = {"T1": T("be"), "T2": T("fe"), "T3": T("be")}
    assert from_contract_ids(tickets, None) == ["T3", "T2"]
    assert from_contract_ids(tickets, ["T1"]) == ["T1"]


def test_review_skips_ids_not_implemented(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-26")
    ran = review(yard, "AB-26", ["T1"], runner=DryRunRunner())
    assert ran == []
    assert st.load(yard, "AB-26")["tickets"]["T1"]["state"] == "ready"


def test_review_diff_is_since_previous_same_repo_ticket(
    tmp_path: Path, git_src: Path, monkeypatch
):
    import subprocess

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-27", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on: T1\n- parallel: false\n"
    )
    req_freeze(yard, "AB-27")
    wt = d / "worktrees" / "backend"

    class Committer:
        def __init__(self, filename: str) -> None:
            self.filename = filename
            self.prompts: list[str] = []

        def start(self, prompt, cwd, extra_read_paths):
            self.prompts.append(prompt)
            (cwd / self.filename).write_text(self.filename)
            subprocess.check_call(["git", "add", self.filename], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", self.filename], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    implement(yard, "AB-27", ["T1"], runner=Committer("t1.txt"))
    review(yard, "AB-27", ["T1"], runner=DryRunRunner())
    implement(yard, "AB-27", ["T2"], runner=Committer("t2.txt"))
    cap = _Capture()
    review(yard, "AB-27", ["T2"], runner=cap)
    assert "t2.txt" in cap.prompts[0]
    assert "t1.txt" not in cap.prompts[0]


def test_review_diff_includes_uncommitted_work(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-28")
    implement(yard, "AB-28", None, runner=DryRunRunner())
    wt = yard / "reqs" / "AB-28" / "worktrees" / "backend"
    (wt / "wip.txt").write_text("uncommitted t5")
    cap = _Capture()
    review(yard, "AB-28", ["T1"], runner=cap)
    assert "wip.txt" in cap.prompts[0]
    assert "uncommitted t5" in cap.prompts[0]


def test_clip_summary_keeps_review_tail():
    body = ("noise\n" * 20000) + "## Spec\nmissing field\nREVIEW_FAILED\n"
    clipped = clip_summary(body, "review")
    assert "REVIEW_FAILED" in clipped
    assert clipped.endswith("REVIEW_FAILED")
    assert len(clipped) <= 32000


def test_review_merge_conflict_keeps_ticket_reviewable(
    tmp_path: Path, git_src: Path, monkeypatch
):
    import subprocess

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-31", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-31")

    class Conflicter:
        """Commits the same file with different content in each ticket worktree."""

        def __init__(self) -> None:
            self.n = 0

        def start(self, prompt, cwd, extra_read_paths):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    c = Conflicter()
    implement(yard, "AB-31", ["T1"], runner=c)
    implement(yard, "AB-31", ["T2"], runner=c)
    parent = yard / "reqs" / "AB-31" / "worktrees" / "backend"
    child2 = Path(st.load(yard, "AB-31")["tickets"]["T2"]["child_worktree"])
    assert child2.exists()
    # parent advances after the child branched off -> merge will conflict
    (parent / "shared.txt").write_text("parent later\n")
    subprocess.check_call(["git", "commit", "-am", "parent later"], cwd=parent)

    ran = review(yard, "AB-31", None, runner=DryRunRunner())
    assert ran == ["T1", "T2"]
    data = st.load(yard, "AB-31")
    assert data["tickets"]["T1"]["state"] == "done"
    # T2's merge conflicted: it must stay reviewable, not be marked done
    assert data["tickets"]["T2"]["state"] == "reviewing"
    assert "merge" in (data["tickets"]["T2"]["last_summary"] or "").lower()
    # parent worktree must not be left in a conflicted merge state
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=parent)
    assert b"UU" not in status
    # child worktree/branch kept so the merge can be retried
    assert child2.exists()


def test_ticket_done_conflict_aborts_merge_and_raises(
    tmp_path: Path, git_src: Path, monkeypatch
):
    import subprocess

    from dev_yard.service import ticket_done, ticket_start

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-32", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-32")

    class Conflicter:
        def __init__(self) -> None:
            self.n = 0

        def start(self, prompt, cwd, extra_read_paths):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    c = Conflicter()
    implement(yard, "AB-32", ["T1"], runner=c)
    implement(yard, "AB-32", ["T2"], runner=c)
    # parent advances after the child branched off -> ticket_done merge conflicts
    parent = yard / "reqs" / "AB-32" / "worktrees" / "backend"
    (parent / "shared.txt").write_text("parent later\n")
    subprocess.check_call(["git", "commit", "-am", "parent later"], cwd=parent)
    # mark T1 reviewed-done via review
    review(yard, "AB-32", ["T1"], runner=DryRunRunner())
    with pytest.raises(GitError):
        ticket_done(yard, "AB-32", "T2")
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=parent)
    assert b"UU" not in status
