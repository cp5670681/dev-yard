from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.runners import DryRunRunner
from dev_yard.service import implement, init_yard, repo_add, req_freeze, req_open, review
from dev_yard.test_report import (
    ReportRejected,
    InboundReport,
    accept_test_report,
    parse_inbound,
    submit_test,
)
from dev_yard.web.board import requirement_detail
from dev_yard.web.app import create_app
from fastapi.testclient import TestClient


def _done_with_contract(tmp_path: Path, git_src: Path, key: str) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, key, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, key)
    implement(yard, key, None, runner=DryRunRunner())
    review(yard, key, None, runner=DryRunRunner())
    review(yard, key, None, contract=True, runner=DryRunRunner())
    return yard


def test_submit_test_requires_contract(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-40", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-40")
    implement(yard, "AB-40", None, runner=DryRunRunner())
    review(yard, "AB-40", None, runner=DryRunRunner())
    with pytest.raises(ReportRejected, match="contract_review"):
        submit_test(yard, "AB-40")


def test_submit_and_accept_pass(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _done_with_contract(tmp_path, git_src, "AB-41")
    data = st.load(yard, "AB-41")
    assert data["phase"] != "done"
    assert data["contract_review"] == "passed"
    submit_test(yard, "AB-41")
    assert st.load(yard, "AB-41")["phase"] == "testing"
    accept_test_report(
        yard,
        "AB-41",
        InboundReport(verdict="passed", body="# ok\n", summary="all good", source="web"),
    )
    after = st.load(yard, "AB-41")
    assert after["phase"] == "done"
    assert after["test"]["latest_verdict"] == "passed"
    assert (yard / "reqs" / "AB-41" / "TEST-REPORT.md").is_file()


def test_reject_report_when_not_testing(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _done_with_contract(tmp_path, git_src, "AB-42")
    with pytest.raises(ReportRejected, match="testing"):
        accept_test_report(
            yard, "AB-42", InboundReport(verdict="failed", body="# no\n", source="api")
        )


def test_failed_report_enables_from_test(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _done_with_contract(tmp_path, git_src, "AB-43")
    submit_test(yard, "AB-43")
    accept_test_report(
        yard,
        "AB-43",
        parse_inbound(
            {
                "verdict": "failed",
                "body": "# Alert always 全部\n",
                "summary": "1 fail",
                "findings": [
                    {"id": "F1", "title": "alert", "detail": "wrong text", "repo": "backend"}
                ],
            },
            "api",
        ),
    )
    detail = requirement_detail(yard, "AB-43")
    ids = {a.id: a for a in detail.actions}
    assert ids["fix-test"].enabled
    assert not ids["submit-test"].enabled
    assert ids["fill-test-report"].enabled

    class Cap:
        def __init__(self):
            self.prompts: list[str] = []

        def start(self, prompt, cwd, extra, repo=None):
            self.prompts.append(prompt)
            from dev_yard.runners import RunResult

            return RunResult(ok=True, summary="fixed", exit_code=0)

    cap = Cap()
    ran = implement(yard, "AB-43", None, from_test=True, runner=cap)
    assert ran == ["T1"]
    assert "Previous test report" in cap.prompts[0]
    assert "Alert always" in cap.prompts[0]
    after = st.load(yard, "AB-43")
    assert after["phase"] == "frozen"
    assert after["test"]["status"] == "fixing"
    assert after["tickets"]["T1"]["state"] == "implemented"


def test_inbound_api_token(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _done_with_contract(tmp_path, git_src, "AB-44")
    submit_test(yard, "AB-44")
    client = TestClient(create_app(yard, sync_jobs=True))
    body = {"verdict": "failed", "body": "# fail\n"}
    r = client.post("/api/inbound/reqs/AB-44/test-report", json=body)
    assert r.status_code == 503
    monkeypatch.setenv("YARD_TEST_REPORT_TOKEN", "secret")
    r = client.post("/api/inbound/reqs/AB-44/test-report", json=body)
    assert r.status_code == 401
    r = client.post(
        "/api/inbound/reqs/AB-44/test-report",
        json=body,
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200
    assert r.json()["test"]["latest_verdict"] == "failed"
    r = client.post("/api/inbound/reqs/AB-44/test-report", json=body, headers={"Authorization": "Bearer secret"})
    # still testing, second report ok
    assert r.status_code == 200
    yard2 = _done_with_contract(tmp_path, git_src, "AB-45")
    r = client.post(
        "/api/inbound/reqs/AB-45/test-report",
        json=body,
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 409


def test_web_form_accepts_without_token(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("YARD_TEST_REPORT_TOKEN", raising=False)
    yard = _done_with_contract(tmp_path, git_src, "AB-46")
    submit_test(yard, "AB-46")
    client = TestClient(create_app(yard, sync_jobs=True))
    r = client.post(
        "/api/requirements/AB-46/test-report",
        json={"verdict": "passed", "body": "# pass\n", "summary": "ok"},
    )
    assert r.status_code == 200
    assert r.json()["phase"] == "done"


def test_submit_test_rejects_passed_and_already_testing(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _done_with_contract(tmp_path, git_src, "AB-47")
    submit_test(yard, "AB-47")
    with pytest.raises(ReportRejected, match="already in testing"):
        submit_test(yard, "AB-47")
    accept_test_report(
        yard, "AB-47", InboundReport(verdict="passed", body="# ok\n", source="web")
    )
    with pytest.raises(ReportRejected, match="passed test report"):
        submit_test(yard, "AB-47")
    assert st.load(yard, "AB-47")["phase"] == "done"


def test_report_id_unique_when_file_exists(tmp_path: Path):
    from datetime import datetime, timezone

    from dev_yard.test_report import _report_id

    archive = tmp_path / "test-reports"
    archive.mkdir()
    when = datetime(2026, 9, 9, 14, 30, 15, 123456, tzinfo=timezone.utc)
    first = _report_id(when, archive)
    (archive / f"{first}.md").write_text("a\n")
    second = _report_id(when, archive)
    assert first != second
    assert second.startswith(first)


def test_inbound_bearer_is_case_insensitive(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _done_with_contract(tmp_path, git_src, "AB-48")
    submit_test(yard, "AB-48")
    monkeypatch.setenv("YARD_TEST_REPORT_TOKEN", "secret")
    client = TestClient(create_app(yard, sync_jobs=True))
    r = client.post(
        "/api/inbound/reqs/AB-48/test-report",
        json={"verdict": "failed", "body": "# fail\n"},
        headers={"Authorization": "bearer secret"},
    )
    assert r.status_code == 200
