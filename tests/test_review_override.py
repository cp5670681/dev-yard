from pathlib import Path

import pytest
from typer.testing import CliRunner

from dev_yard import status as st
from dev_yard.cli import app
from dev_yard.runners import DryRunRunner
from dev_yard.service import (
    contract_review_override,
    implement,
    init_yard,
    repo_add,
    req_freeze,
    req_open,
    review,
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

    # Human review override -> passed
    res = ticket_review_override(
        yard,
        "PROJ-101",
        "T1",
        verdict="passed",
        summary="Human approved: looks great!",
    )
    assert res["state"] == "done"
    assert res["last_summary"] == "Human approved: looks great!"

    # Downstream T2 should now be ready
    data = st.load(yard, "PROJ-101")
    assert data["tickets"]["T1"]["state"] == "done"
    assert data["tickets"]["T2"]["state"] == "ready"


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
    assert "REVIEW_FAILED" in res["last_summary"]
    assert "Please add null check" in res["last_summary"]

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
    assert "Updated T1: state=done" in res2.stdout

    slot2 = st.load(yard, "PROJ-101")["tickets"]["T1"]
    assert slot2["state"] == "done"
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
    assert "Missing auth header" in res["contract_summary"]
    assert "REVIEW_FAILED" in res["contract_summary"]

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
