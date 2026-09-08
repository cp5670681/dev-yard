from pathlib import Path

from dev_yard import status as st
from dev_yard.service import init_yard, repo_add, req_freeze, req_open


def test_freeze_creates_worktree(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-9", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    wts = req_freeze(yard, "AB-9")
    assert wts[0].exists()
    assert (wts[0] / "README").exists()


def test_freeze_coerces_list_tickets_in_status(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-10", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    (d / "STATUS.yaml").write_text(
        "jira: AB-10\nphase: grilled\ntickets:\n  - T1: backend\nrepos:\n  - backend\n"
    )
    wts = req_freeze(yard, "AB-10")
    assert wts[0].exists()
    data = st.load(yard, "AB-10")
    assert data["phase"] == "frozen"
    assert data["tickets"]["T1"]["repo"] == "backend"
    assert data["tickets"]["T1"]["state"] == "ready"
    assert data["tickets"]["T1"]["worktree"] == str(wts[0])
