from pathlib import Path

import pytest
from typer.testing import CliRunner

from dev_yard import gitops
from dev_yard import status as st
from dev_yard.cli import app
from dev_yard.config import GitSettings, save_git_settings
from dev_yard.service import (
    init_yard,
    repo_add,
    req_freeze,
    req_open,
    req_reset_phase,
    ticket_start,
)


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
    (yard / "reqs" / "CONTEXT.md").write_text("# glossary\n")
    adr = yard / "reqs" / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-test.md").write_text("# adr\n")
    wts = req_freeze(yard, "AB-9")
    assert wts[0].exists()
    assert (wts[0] / "README").exists()
    assert not (wts[0] / "CONTEXT.md").exists()
    assert not (wts[0] / "docs" / "adr" / "0001-test.md").exists()
    assert (yard / "reqs" / "CONTEXT.md").read_text() == "# glossary\n"
    assert (adr / "0001-test.md").read_text() == "# adr\n"
    assert st.load(yard, "AB-9")["branch"] == "req/AB-9"
    listed = gitops.run(["git", "branch", "--list", "req/AB-9"], cwd=git_src)
    assert "req/AB-9" in listed


def test_freeze_uses_configured_branch_template(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    save_git_settings(yard, GitSettings(freeze_branch="feature/{jira}"))
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-91", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    wts = req_freeze(yard, "AB-91")
    assert gitops.current_branch(wts[0]) == "feature/AB-91"
    assert st.load(yard, "AB-91")["branch"] == "feature/AB-91"
    child = ticket_start(yard, "AB-91", "T1")
    assert gitops.current_branch(child) == "feature/AB-91-T1"
    save_git_settings(yard, GitSettings(freeze_branch="feat/{jira}"))
    req_freeze(yard, "AB-91", force=True)
    assert gitops.current_branch(wts[0]) == "feature/AB-91"
    assert st.load(yard, "AB-91")["branch"] == "feature/AB-91"


def test_legacy_freeze_keeps_worktree_branch_after_template_change(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    from dev_yard.web.board import requirement_detail

    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-92", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    wts = req_freeze(yard, "AB-92")
    data = st.load(yard, "AB-92")
    data.pop("branch", None)
    st.save(yard, "AB-92", data)
    save_git_settings(yard, GitSettings(freeze_branch="feature/{jira}"))
    detail = requirement_detail(yard, "AB-92")
    assert detail.branch == "req/AB-92"
    child = ticket_start(yard, "AB-92", "T1")
    assert gitops.current_branch(wts[0]) == "req/AB-92"
    assert gitops.current_branch(child) == "req/AB-92-T1"


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


def test_freeze_refuses_testing_without_force(tmp_path: Path, git_src: Path, monkeypatch):
    import pytest

    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-60", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-60")
    data = st.load(yard, "AB-60")
    data["phase"] = "testing"
    st.save(yard, "AB-60", data)
    with pytest.raises(ValueError, match="testing"):
        req_freeze(yard, "AB-60")
    req_freeze(yard, "AB-60", force=True)
    assert st.load(yard, "AB-60")["phase"] == "frozen"


def test_force_freeze_resets_existing_worktree(tmp_path: Path, git_src: Path, monkeypatch):
    import subprocess

    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-61", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    wts = req_freeze(yard, "AB-61")
    wt = wts[0]
    (wt / "OLD").write_text("old")
    subprocess.check_call(["git", "add", "."], cwd=wt)
    subprocess.check_call(["git", "commit", "-m", "old"], cwd=wt)
    data = st.load(yard, "AB-61")
    data["phase"] = "testing"
    st.save(yard, "AB-61", data)
    req_freeze(yard, "AB-61", force=True)
    assert st.load(yard, "AB-61")["phase"] == "frozen"
    assert not (wt / "OLD").exists()


def test_reset_phase_tears_down_worktrees_and_state(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-62", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    (d / "REQUIREMENT.md").write_text("# keep me\n")
    wts = req_freeze(yard, "AB-62")
    child = ticket_start(yard, "AB-62", "T1")
    data = st.load(yard, "AB-62")
    data["tickets"]["T1"]["state"] = "done"
    data["tickets"]["T1"]["head_sha"] = "abcdef123456"
    data["tickets"]["T1"]["last_summary"] = "implemented feature"
    data["tickets"]["T1"]["last_verdict"] = "failed"
    data["contract_review"] = "passed"
    data["contract_summary"] = "all contracts aligned"
    data["contract_findings"] = [{"id": "F1", "title": "test"}]
    data["test"] = {"status": "awaiting", "latest_verdict": None}
    data["phase"] = "testing"
    st.save(yard, "AB-62", data)

    out = req_reset_phase(yard, "AB-62")

    assert out["phase"] == "open"
    assert out["tickets"]["T1"]["state"] == "ready"
    assert "worktree" not in out["tickets"]["T1"]
    assert "child_worktree" not in out["tickets"]["T1"]
    assert "head_sha" not in out["tickets"]["T1"]
    assert "last_summary" not in out["tickets"]["T1"]
    assert "last_verdict" not in out["tickets"]["T1"]
    assert "branch" not in out
    assert "base_shas" not in out
    assert "contract_review" not in out
    assert "contract_summary" not in out
    assert "contract_findings" not in out
    assert "test" not in out
    assert not wts[0].exists()
    assert not child.exists()
    assert not (yard / ".yard-worktrees" / "AB-62").exists()
    assert (d / "REQUIREMENT.md").read_text() == "# keep me\n"
    listed = gitops.run(["git", "branch", "--list", "req/AB-62"], cwd=git_src)
    assert "req/AB-62" not in listed


def test_reset_phase_rejects_already_open(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-63", source="none")
    with pytest.raises(ValueError, match="already phase=open"):
        req_reset_phase(yard, "AB-63")


def test_status_save_preserves_unicode(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    data = {
        "jira": "AB-11",
        "phase": "frozen",
        "tickets": {
            "T1": {
                "state": "done",
                "last_summary": "代码评审完成，测试通过",
            }
        },
    }
    st.save(yard, "AB-11", data)
    content = (yard / "reqs" / "AB-11" / "STATUS.yaml").read_text(encoding="utf-8")
    assert "代码评审完成，测试通过" in content
    assert "\\u" not in content


def test_cli_reset_phase(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-64", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-64")
    monkeypatch.chdir(yard)
    runner = CliRunner()
    res = runner.invoke(app, ["req", "reset-phase", "AB-64", "-y"])
    assert res.exit_code == 0
    assert "AB-64 phase=open" in res.stdout
    assert st.load(yard, "AB-64")["phase"] == "open"

