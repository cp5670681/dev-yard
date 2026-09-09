from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.runners import DryRunRunner, RunResult, clip_summary
from dev_yard.service import implement, init_yard, repo_add, req_freeze, req_open, review


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


def test_clip_summary_keeps_review_tail():
    body = ("noise\n" * 20000) + "## Spec\nmissing field\nREVIEW_FAILED\n"
    clipped = clip_summary(body, "review")
    assert "REVIEW_FAILED" in clipped
    assert clipped.endswith("REVIEW_FAILED")
    assert len(clipped) <= 32000
