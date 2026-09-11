from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.bug_tickets import parse_findings_from_summary, spawn_fix_tickets
from dev_yard.runners import DryRunRunner
from dev_yard.service import implement, init_yard, repo_add, req_freeze, req_open, review
from dev_yard.tickets import parse_tickets
from dev_yard.test_report import (
    InboundReport,
    Finding,
    accept_test_report,
    parse_inbound,
    submit_test,
)


def test_parse_bug_headings():
    text = """
## T1: feat
- repo: backend

## B1: gap
- repo: backend
- depends_on: T1
- parallel: true
- source: contract
- finding: F1
"""
    ts = parse_tickets(text)
    assert [t.id for t in ts] == ["T1", "B1"]
    assert ts[1].source == "contract"
    assert ts[1].finding == "F1"
    assert ts[1].depends_on == ["T1"]
    assert ts[1].parallel is True


def _two_repo_req(tmp_path: Path, git_src: Path, key: str) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    repo_add(yard, "frontend", str(git_src), "main", "fe", str(git_src))
    d, _ = req_open(yard, key, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: be\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: fe\n- repo: frontend\n- depends_on:\n- parallel: true\n"
    )
    data = st.load(yard, key)
    from dev_yard.tickets import load_tickets

    data = st.sync_tickets(data, load_tickets(d))
    data["tickets"]["T1"]["state"] = "done"
    data["tickets"]["T2"]["state"] = "done"
    data["repos"] = ["backend", "frontend"]
    st.save(yard, key, data)
    return yard


def test_spawn_parallel_and_depends(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _two_repo_req(tmp_path, git_src, "AB-80")
    data = st.load(yard, "AB-80")
    data["contract_review"] = "failed"
    data["contract_summary"] = "gaps"
    data["contract_findings"] = [
        {
            "id": "F1",
            "title": "api field",
            "detail": "missing name",
            "repo": "backend",
            "depends_on": [],
        },
        {
            "id": "F2",
            "title": "ui bind",
            "detail": "show name",
            "repo": "frontend",
            "depends_on": ["F1"],
        },
        {
            "id": "F3",
            "title": "other be",
            "detail": "unrelated",
            "repo": "backend",
            "depends_on": [],
        },
    ]
    st.save(yard, "AB-80", data)
    created = spawn_fix_tickets(yard, "AB-80", "contract")
    assert created == ["B1", "B2", "B3"]
    tix = {t.id: t for t in parse_tickets((yard / "reqs" / "AB-80" / "TICKETS.md").read_text())}
    assert tix["B1"].depends_on == ["T1"]
    assert tix["B1"].parallel is True
    assert tix["B2"].depends_on == ["B1", "T2"]
    assert tix["B2"].parallel is False
    assert tix["B3"].depends_on == ["T1"]
    assert tix["B3"].parallel is True
    after = st.load(yard, "AB-80")
    assert after["tickets"]["B1"]["state"] == "ready"
    assert after["tickets"]["B3"]["state"] == "ready"
    assert after["tickets"]["B2"]["state"] == "pending"
    assert after["tickets"]["T1"]["state"] == "done"
    again = spawn_fix_tickets(yard, "AB-80", "contract")
    assert again == ["B1", "B2", "B3"]
    body = (yard / "reqs" / "AB-80" / "TICKETS.md").read_text()
    assert body.count("## B1:") == 1


def test_from_contract_runs_bug_tickets_not_last_feature(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-81", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on: T1\n- parallel: false\n"
    )
    req_freeze(yard, "AB-81")
    implement(yard, "AB-81", None, runner=DryRunRunner())
    review(yard, "AB-81", None, runner=DryRunRunner())
    implement(yard, "AB-81", ["T2"], runner=DryRunRunner())
    review(yard, "AB-81", ["T2"], runner=DryRunRunner())
    data = st.load(yard, "AB-81")
    data["phase"] = "done"
    data["contract_review"] = "failed"
    data["contract_summary"] = "CONTRACT DEFECT: missing follow_members_names"
    st.save(yard, "AB-81", data)
    ran = implement(yard, "AB-81", None, from_contract=True, runner=DryRunRunner())
    assert ran == ["B1"]
    after = st.load(yard, "AB-81")
    assert after["tickets"]["B1"]["state"] == "implemented"
    assert after["tickets"]["T2"]["state"] == "done"
    assert after["phase"] == "frozen"


def test_failed_report_spawns_dependent_bugs(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-82", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-82")
    implement(yard, "AB-82", None, runner=DryRunRunner())
    review(yard, "AB-82", None, runner=DryRunRunner())
    review(yard, "AB-82", None, contract=True, runner=DryRunRunner())
    submit_test(yard, "AB-82")
    accept_test_report(
        yard,
        "AB-82",
        InboundReport(
            verdict="failed",
            body="# fail\n",
            summary="2 fails",
            source="api",
            findings=[
                Finding(id="F1", title="a", detail="one", repo="backend"),
                Finding(
                    id="F2",
                    title="b",
                    detail="two",
                    repo="backend",
                    depends_on=["F1"],
                ),
            ],
        ),
    )
    after = st.load(yard, "AB-82")
    assert after["tickets"]["B1"]["state"] == "ready"
    assert after["tickets"]["B2"]["state"] == "pending"
    ran = implement(yard, "AB-82", None, from_test=True, runner=DryRunRunner())
    assert ran == ["B1"]
    assert st.load(yard, "AB-82")["tickets"]["T1"]["state"] == "done"


def test_second_bug_round_new_finding_ids(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-85", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-85")
    implement(yard, "AB-85", None, runner=DryRunRunner())
    review(yard, "AB-85", None, runner=DryRunRunner())
    review(yard, "AB-85", None, contract=True, runner=DryRunRunner())
    submit_test(yard, "AB-85")
    accept_test_report(
        yard,
        "AB-85",
        parse_inbound(
            {
                "verdict": "failed",
                "findings": [{"id": "F1", "title": "first", "repo": "backend"}],
                "body": "r1",
            },
            "api",
        ),
    )
    data = st.load(yard, "AB-85")
    data["tickets"]["B1"]["state"] = "done"
    data["phase"] = "frozen"
    st.save(yard, "AB-85", data)
    accept_test_report(
        yard,
        "AB-85",
        parse_inbound(
            {
                "verdict": "failed",
                "findings": [{"id": "F1", "title": "second", "repo": "backend"}],
                "body": "r2",
            },
            "api",
        ),
    )
    tix = {t.id: t for t in parse_tickets((d / "TICKETS.md").read_text())}
    assert "B1" in tix and "B2" in tix
    assert tix["B1"].title == "first"
    assert tix["B2"].title == "second"
    assert tix["B1"].finding != tix["B2"].finding


def test_findings_without_repo_are_dropped(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _two_repo_req(tmp_path, git_src, "AB-83")
    data = st.load(yard, "AB-83")
    data["contract_review"] = "failed"
    data["contract_summary"] = "gap"
    data["contract_findings"] = [
        {"id": "F1", "title": "shared gap", "detail": "both", "repo": ""}
    ]
    st.save(yard, "AB-83", data)
    created = spawn_fix_tickets(yard, "AB-83", "contract")
    assert created == ["B1", "B2"]
    tix = {t.id: t for t in parse_tickets((yard / "reqs" / "AB-83" / "TICKETS.md").read_text())}
    assert tix["B1"].finding.startswith("repo:")
    assert tix["B2"].finding.startswith("repo:")


def test_parse_inbound_rejects_empty_repo():
    from dev_yard.test_report import ReportRejected, parse_inbound

    with pytest.raises(ReportRejected, match="repo is required"):
        parse_inbound(
            {
                "verdict": "failed",
                "findings": [{"id": "F1", "title": "x", "repo": ""}],
                "body": "x",
            },
            "api",
        )


def test_parse_findings_from_summary():
    text = """REVIEW_FAILED

gaps in API

findings:
  - id: F1
    title: missing field
    repo: backend
    detail: follow_members_names
    depends_on: []
  - id: F2
    title: ui
    repo: frontend
    depends_on:
      - F1
"""
    got = parse_findings_from_summary(text)
    assert [f["id"] for f in got] == ["F1", "F2"]
    assert got[0]["repo"] == "backend"
    assert got[1]["depends_on"] == ["F1"]


def test_spawn_from_contract_summary_findings(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _two_repo_req(tmp_path, git_src, "AB-84")
    data = st.load(yard, "AB-84")
    data["contract_summary"] = (
        "REVIEW_FAILED\n\nfindings:\n"
        "  - id: F1\n    title: api\n    repo: backend\n    detail: x\n"
        "  - id: F2\n    title: ui\n    repo: frontend\n    depends_on: [F1]\n"
    )
    st.save(yard, "AB-84", data)
    created = spawn_fix_tickets(yard, "AB-84", "contract")
    assert created == ["B1", "B2"]
    tix = {t.id: t for t in parse_tickets((yard / "reqs" / "AB-84" / "TICKETS.md").read_text())}
    assert tix["B2"].depends_on[0] == "B1"
