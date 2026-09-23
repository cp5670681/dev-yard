from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.gitops import GitError
from dev_yard.runners import DryRunRunner, RunResult, clip_summary
from dev_yard.service import (
    from_contract_ids,
    implement,
    init_yard,
    recover_stale_tickets,
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
    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path], repo=None) -> RunResult:
        return RunResult(
            ok=False,
            summary="nits\nREVIEW_FAILED\n",
            exit_code=0,
            verdict="failed",
        )


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
        def start(self, prompt, cwd, extra_read_paths, repo=None):
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
    slot = st.load(yard, "AB-15")["tickets"]["T1"]
    assert slot["state"] == "blocked"
    assert slot["last_verdict"] == "failed"


class _PassReviewDespitePhrase(DryRunRunner):
    def start(self, prompt, cwd, extra_read_paths, repo=None) -> RunResult:
        return RunResult(
            ok=True,
            summary="汇总结论：通过（不写 REVIEW_FAILED）。",
            exit_code=0,
            verdict="passed",
        )


def test_review_verdict_ignores_marker_inside_prose(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-15B")
    implement(yard, "AB-15B", None, runner=DryRunRunner())
    review(yard, "AB-15B", None, runner=_PassReviewDespitePhrase())
    slot = st.load(yard, "AB-15B")["tickets"]["T1"]
    assert slot["state"] == "done"
    assert slot["last_verdict"] == "passed"
    assert "REVIEW_FAILED" in slot["last_summary"]


def test_implement_and_review_attach_requirement_images(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-19")
    d = yard / "reqs" / "AB-19"
    assets = d / "assets" / "669971526"
    assets.mkdir(parents=True)
    shot = assets / "entry1.png"
    shot.write_bytes(b"x")

    class Capture:
        def __init__(self) -> None:
            self.calls: list[list[Path]] = []

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.calls.append(list(extra_read_paths))
            return RunResult(ok=True, summary="ok")

    impl = Capture()
    implement(yard, "AB-19", None, runner=impl)
    assert shot in impl.calls[0]

    rev = Capture()
    review(yard, "AB-19", None, runner=rev)
    assert shot in rev.calls[0]

    contract = Capture()
    review(yard, "AB-19", None, contract=True, runner=contract)
    assert shot in contract.calls[0]


class _Capture:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def start(self, prompt, cwd, extra_read_paths, repo=None):
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
    data["contract_review"] = "failed"
    data["contract_summary"] = "CONTRACT DEFECT: missing follow_members_names"
    st.save(yard, "AB-23", data)
    cap = _Capture()
    ran = implement(yard, "AB-23", None, from_contract=True, runner=cap)
    assert ran == ["B1"]
    assert "Previous contract review" in cap.prompts[0]
    assert "follow_members_names" in cap.prompts[0]
    assert "sibling bug tickets" in cap.prompts[0]
    assert "finding repo:backend" in cap.prompts[0]
    assert "this ticket's repo" not in cap.prompts[0]
    after = st.load(yard, "AB-23")
    assert after["tickets"]["B1"]["state"] == "implemented"
    assert after["tickets"]["T2"]["state"] == "done"
    assert after["tickets"]["T1"]["state"] == "done"
    assert after["phase"] == "frozen"


def test_from_contract_explicit_ids(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-24")
    implement(yard, "AB-24", None, runner=DryRunRunner())
    review(yard, "AB-24", None, runner=DryRunRunner())
    data = st.load(yard, "AB-24")
    data["contract_review"] = "failed"
    data["contract_summary"] = "gap in T1"
    st.save(yard, "AB-24", data)
    cap = _Capture()
    ran = implement(yard, "AB-24", ["B1"], from_contract=True, runner=cap)
    assert ran == ["B1"]
    assert "gap in T1" in cap.prompts[0]


def test_from_test_explicit_normal_id_is_rejected(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-24b")
    implement(yard, "AB-24b", None, runner=DryRunRunner())
    review(yard, "AB-24b", None, runner=DryRunRunner())
    with pytest.raises(ValueError, match="none of T1 are test bug tickets"):
        implement(yard, "AB-24b", ["T1"], from_test=True, runner=DryRunRunner())


def test_from_contract_dry_run_does_not_mutate(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-25")
    implement(yard, "AB-25", None, runner=DryRunRunner())
    review(yard, "AB-25", None, runner=DryRunRunner())
    data = st.load(yard, "AB-25")
    data["phase"] = "done"
    data["contract_review"] = "failed"
    data["contract_summary"] = "nits"
    st.save(yard, "AB-25", data)
    tickets_before = (yard / "reqs" / "AB-25" / "TICKETS.md").read_text()
    ran = implement(yard, "AB-25", None, from_contract=True, dry_run=True)
    assert ran == ["B1"]
    after = st.load(yard, "AB-25")
    assert after["tickets"]["T1"]["state"] == "done"
    assert "B1" not in after["tickets"]
    assert after["phase"] == "done"
    assert (yard / "reqs" / "AB-25" / "TICKETS.md").read_text() == tickets_before


def test_from_contract_passed_does_not_spawn(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-26b")
    implement(yard, "AB-26b", None, runner=DryRunRunner())
    review(yard, "AB-26b", None, runner=DryRunRunner())
    data = st.load(yard, "AB-26b")
    data["contract_review"] = "passed"
    data["contract_summary"] = "all good"
    st.save(yard, "AB-26b", data)
    with pytest.raises(ValueError, match="no ready contract bug tickets"):
        implement(yard, "AB-26b", None, from_contract=True, runner=DryRunRunner())
    assert "B1" not in st.load(yard, "AB-26b")["tickets"]


def test_from_contract_ids_prefers_bug_tickets():
    from dev_yard.tickets import Ticket

    tickets = {
        "T1": Ticket("T1", "a", "be"),
        "B1": Ticket("B1", "gap", "be", source="contract", finding="F1"),
    }
    data = {"tickets": {"T1": {"state": "done"}, "B1": {"state": "ready"}}}
    assert from_contract_ids(tickets, None, data) == ["B1"]
    # Explicit ids only keep tickets of the right source; a normal ticket is not
    # silently re-implemented as a contract fix.
    assert from_contract_ids(tickets, ["T1"], data) == []
    assert from_contract_ids(tickets, ["B1"], data) == ["B1"]


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

    class Committer:
        def __init__(self, filename: str) -> None:
            self.filename = filename
            self.prompts: list[str] = []

        def start(self, prompt, cwd, extra_read_paths, repo=None):
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
    # Every ticket now works in its own child worktree.
    wt = Path(st.load(yard, "AB-28")["tickets"]["T1"]["child_worktree"])
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

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    c = Conflicter()
    implement(yard, "AB-31", ["T1"], runner=c)
    implement(yard, "AB-31", ["T2"], runner=c)
    parent = yard / "reqs" / "AB-31" / "worktrees" / "backend"
    children = [
        Path(st.load(yard, "AB-31")["tickets"][tid]["child_worktree"]) for tid in ("T1", "T2")
    ]
    assert all(child.exists() for child in children)
    # parent advances after the children branched off -> syncing will conflict
    (parent / "shared.txt").write_text("parent later\n")
    subprocess.check_call(["git", "add", "shared.txt"], cwd=parent)
    subprocess.check_call(["git", "commit", "-m", "parent later"], cwd=parent)

    ran = review(yard, "AB-31", None, runner=DryRunRunner())
    assert ran == ["T1", "T2"]
    data = st.load(yard, "AB-31")
    # Both children now fork from the pre-merge freeze point, so both must sync
    # and hit the same conflict before review; neither is marked done.
    for tid in ("T1", "T2"):
        assert data["tickets"][tid]["state"] == "blocked"
        assert "SYNC_CONFLICT" in (data["tickets"][tid]["last_summary"] or "")
    # parent worktree must not be left in a conflicted merge state
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=parent)
    assert b"UU" not in status
    # child worktrees/branches kept so the sync can be retried
    assert all(child.exists() for child in children)


def test_ticket_done_conflict_aborts_merge_and_raises(
    tmp_path: Path, git_src: Path, monkeypatch
):
    import subprocess

    from dev_yard.service import ticket_done

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

        def start(self, prompt, cwd, extra_read_paths, repo=None):
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
    subprocess.check_call(["git", "add", "shared.txt"], cwd=parent)
    subprocess.check_call(["git", "commit", "-m", "parent later"], cwd=parent)
    with pytest.raises(GitError):
        ticket_done(yard, "AB-32", "T1")
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=parent)
    assert b"UU" not in status


def test_implement_skips_pending_explicit_id(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-70", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on: T1\n- parallel: false\n"
    )
    req_freeze(yard, "AB-70")
    ran = implement(yard, "AB-70", ["T2"], runner=DryRunRunner())
    assert ran == []
    assert st.load(yard, "AB-70")["tickets"]["T2"]["state"] == "pending"
    ran_force = implement(yard, "AB-70", ["T2"], runner=DryRunRunner(), force=True)
    assert ran_force == ["T2"]


def test_same_repo_ready_after_sibling_implemented_gets_child(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-71", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-71")
    implement(yard, "AB-71", ["T1"], runner=DryRunRunner())
    ran = implement(yard, "AB-71", ["T2"], runner=DryRunRunner())
    assert ran == ["T2"]
    data = st.load(yard, "AB-71")
    assert data["tickets"]["T2"]["child_worktree"]


def test_commit_failure_blocks_ticket(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-72")
    data = st.load(yard, "AB-72")
    data["tickets"]["T1"]["last_verdict"] = "failed"
    st.save(yard, "AB-72", data)
    monkeypatch.setattr("dev_yard.service.gitops.commit_all", lambda *a, **k: None)
    ran = implement(yard, "AB-72", None, runner=DryRunRunner())
    assert ran == ["T1"]
    slot = st.load(yard, "AB-72")["tickets"]["T1"]
    assert slot["state"] == "blocked"
    assert "commit failed" in (slot["last_summary"] or "")
    assert "last_verdict" not in slot


def test_sequential_tickets_auto_commit_and_diff_isolation(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard.service import ticket_diff

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-99", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: first\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: second\n- repo: backend\n- depends_on:\n- T1\n- parallel: false\n"
    )
    req_freeze(yard, "AB-99")

    class Ticket1Runner:
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            (cwd / "file1.txt").write_text("file 1 content\n")
            return RunResult(ok=True, summary="t1 implemented")

    class Ticket2Runner:
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            (cwd / "file2.txt").write_text("file 2 content\n")
            return RunResult(ok=True, summary="t2 implemented")

    # Implement & review T1
    implement(yard, "AB-99", ["T1"], runner=Ticket1Runner())
    review(yard, "AB-99", ["T1"], runner=DryRunRunner())

    status_data = st.load(yard, "AB-99")
    t1_sha = status_data["tickets"]["T1"].get("head_sha")
    assert t1_sha is not None
    assert status_data["tickets"]["T1"]["state"] == "done"

    # T1 diff should show file1.txt
    t1_diff = ticket_diff(yard, "AB-99", "T1")
    assert any(f["path"] == "file1.txt" for f in t1_diff["files"])
    assert not any(f["path"] == "file2.txt" for f in t1_diff["files"])

    # Implement T2
    implement(yard, "AB-99", ["T2"], runner=Ticket2Runner())
    status_data = st.load(yard, "AB-99")
    t2_sha = status_data["tickets"]["T2"].get("head_sha")
    assert t2_sha is not None
    assert t2_sha != t1_sha

    # T2 diff should ONLY show file2.txt, NOT file1.txt!
    t2_diff = ticket_diff(yard, "AB-99", "T2")
    assert any(f["path"] == "file2.txt" for f in t2_diff["files"])
    assert not any(f["path"] == "file1.txt" for f in t2_diff["files"])


def test_first_ticket_gets_own_child_worktree(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-81")
    implement(yard, "AB-81", None, runner=DryRunRunner())
    slot = st.load(yard, "AB-81")["tickets"]["T1"]
    parent = yard / "reqs" / "AB-81" / "worktrees" / "backend"
    assert slot["child_worktree"]
    assert slot["child_worktree"] != str(parent)
    assert Path(slot["child_worktree"]).exists()


def test_parallel_sync_conflict_recovered_by_implement(
    tmp_path: Path, git_src: Path, monkeypatch
):
    import subprocess

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-82", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-82")

    class Conflicter:
        def __init__(self) -> None:
            self.n = 0

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.n += 1
            (cwd / "shared.txt").write_text(f"side {self.n}\n")
            subprocess.check_call(["git", "add", "shared.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", f"c{self.n}"], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    c = Conflicter()
    implement(yard, "AB-82", ["T1"], runner=c)
    implement(yard, "AB-82", ["T2"], runner=c)
    parent = yard / "reqs" / "AB-82" / "worktrees" / "backend"

    # T1 merges into the parent first, leaving T2 forked from the freeze point.
    review(yard, "AB-82", ["T1"], runner=DryRunRunner())
    assert st.load(yard, "AB-82")["tickets"]["T1"]["state"] == "done"

    # T2's review now syncs the parent and surfaces the real conflict.
    review(yard, "AB-82", ["T2"], runner=DryRunRunner())
    t2 = st.load(yard, "AB-82")["tickets"]["T2"]
    assert t2["state"] == "blocked"
    assert "SYNC_CONFLICT" in (t2["last_summary"] or "")

    class Resolver:
        """Resolves the sync conflict implement leaves in the child worktree."""

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            conflicts = subprocess.check_output(
                ["git", "diff", "--name-only", "--diff-filter=U"], cwd=cwd, text=True
            ).split()
            assert conflicts, "implement must leave the sync conflict in place"
            for rel in conflicts:
                (cwd / rel).write_text("resolved\n")
                subprocess.check_call(["git", "add", rel], cwd=cwd)
            return RunResult(ok=True, summary="resolved")

    implement(yard, "AB-82", ["T2"], runner=Resolver())
    assert st.load(yard, "AB-82")["tickets"]["T2"]["state"] == "implemented"

    review(yard, "AB-82", ["T2"], runner=DryRunRunner())
    data = st.load(yard, "AB-82")
    assert data["tickets"]["T2"]["state"] == "done"
    assert data["tickets"]["T2"]["child_worktree"] is None
    assert (parent / "shared.txt").read_text() == "resolved\n"


def test_parallel_child_review_diff_excludes_sibling_work(
    tmp_path: Path, git_src: Path, monkeypatch
):
    import subprocess

    from dev_yard.service import ticket_diff

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-80", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: true\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: true\n"
    )
    req_freeze(yard, "AB-80")

    class Committer:
        def __init__(self, name: str) -> None:
            self.name = name

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            (cwd / f"{self.name}.txt").write_text(self.name)
            subprocess.check_call(["git", "add", f"{self.name}.txt"], cwd=cwd)
            subprocess.check_call(["git", "commit", "-m", self.name], cwd=cwd)
            return RunResult(ok=True, summary="ok")

    # Both tickets are claimed before either runs, so each gets an isolated child
    # worktree forked from the same freeze point (the real parallel dispatch).
    data = st.load(yard, "AB-80")
    data["tickets"]["T1"]["state"] = "implementing"
    data["tickets"]["T2"]["state"] = "implementing"
    st.save(yard, "AB-80", data)

    implement(yard, "AB-80", ["T1"], runner=Committer("t1"))
    implement(yard, "AB-80", ["T2"], runner=Committer("t2"))

    data = st.load(yard, "AB-80")
    assert data["tickets"]["T1"]["child_worktree"]
    assert data["tickets"]["T2"]["child_worktree"]

    # T1 merges into the parent first; T2 forked from the pre-merge freeze point
    # and must not be diffed against T1's head (that shows T1 as deleted).
    assert review(yard, "AB-80", ["T1"], runner=DryRunRunner()) == ["T1"]
    assert st.load(yard, "AB-80")["tickets"]["T1"]["state"] == "done"

    t2_diff = ticket_diff(yard, "AB-80", "T2")
    paths = {f["path"] for f in t2_diff["files"]}
    assert "t2.txt" in paths
    assert "t1.txt" not in paths

    cap = _Capture()
    review(yard, "AB-80", ["T2"], runner=cap)
    assert "t2.txt" in cap.prompts[0]
    assert "t1.txt" not in cap.prompts[0]


class _CancelRunner(DryRunRunner):
    def start(self, prompt, cwd, extra_read_paths, repo=None):
        from dev_yard.web.jobs import JobCancelled

        raise JobCancelled("implement cancelled (pi exit -9)")


def test_implement_cancel_resets_implementing_to_ready(tmp_path, git_src, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-60")
    with pytest.raises(Exception, match="cancelled"):
        implement(yard, "AB-60", ["T1"], runner=_CancelRunner())
    assert st.load(yard, "AB-60")["tickets"]["T1"]["state"] == "ready"


def test_review_cancel_resets_reviewing_to_implemented(tmp_path, git_src, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-61")
    implement(yard, "AB-61", None, runner=DryRunRunner())
    with pytest.raises(Exception, match="cancelled"):
        review(yard, "AB-61", None, runner=_CancelRunner())
    assert st.load(yard, "AB-61")["tickets"]["T1"]["state"] == "implemented"


class _BoomRunner(DryRunRunner):
    def start(self, prompt, cwd, extra_read_paths, repo=None):
        raise RuntimeError("pi crashed")


def test_review_crash_resets_reviewing_to_implemented(tmp_path, git_src, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-62")
    implement(yard, "AB-62", None, runner=DryRunRunner())
    with pytest.raises(RuntimeError, match="pi crashed"):
        review(yard, "AB-62", None, runner=_BoomRunner())
    assert st.load(yard, "AB-62")["tickets"]["T1"]["state"] == "implemented"


def test_recover_stale_tickets_resets_leftovers(tmp_path, git_src, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _ready_req(tmp_path, git_src, "AB-63")
    data = st.load(yard, "AB-63")
    data["tickets"]["T1"]["state"] = "reviewing"
    data["tickets"]["T1"]["last_summary"] = "merge conflict guidance"
    st.save(yard, "AB-63", data)

    assert recover_stale_tickets(yard) == ["AB-63"]
    slot = st.load(yard, "AB-63")["tickets"]["T1"]
    assert slot["state"] == "implemented"
    assert slot["last_summary"] == "merge conflict guidance"

    data = st.load(yard, "AB-63")
    data["tickets"]["T1"]["state"] = "implementing"
    st.save(yard, "AB-63", data)
    assert recover_stale_tickets(yard) == ["AB-63"]
    assert st.load(yard, "AB-63")["tickets"]["T1"]["state"] == "ready"

    assert recover_stale_tickets(yard) == []
