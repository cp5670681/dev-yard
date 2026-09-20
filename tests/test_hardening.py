"""Regression tests for the security/concurrency hardening pass."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dev_yard import gitops
from dev_yard import status as st
from dev_yard.qa_config import TestRejected
from dev_yard.qa_schedule import CaseJob
from dev_yard.service import init_yard


def test_origin_guard_rejects_cross_site_write(tmp_path: Path):
    from dev_yard.web.app import create_app

    yard = tmp_path / "yard"
    init_yard(yard)
    client = TestClient(create_app(yard, sync_jobs=True))

    blocked = client.post(
        "/api/open",
        json={"jira": "EVIL-1", "source": "none"},
        headers={"Origin": "http://evil.example"},
    )
    assert blocked.status_code == 403

    allowed = client.post(
        "/api/open",
        json={"jira": "OK-1", "source": "none"},
        headers={"Origin": "http://testserver"},
    )
    assert allowed.status_code == 200

    no_origin = client.post("/api/open", json={"jira": "OK-2", "source": "none"})
    assert no_origin.status_code == 200


def test_origin_guard_referer_fallback(tmp_path: Path):
    from dev_yard.web.app import create_app

    yard = tmp_path / "yard"
    init_yard(yard)
    client = TestClient(create_app(yard, sync_jobs=True))
    blocked = client.post(
        "/api/open",
        json={"jira": "EVIL-2", "source": "none"},
        headers={"Referer": "http://evil.example/form"},
    )
    assert blocked.status_code == 403
    # GET is never blocked by the write guard.
    assert client.get("/api/meta").status_code == 200


def test_run_case_script_rejects_traversal(tmp_path: Path):
    from dev_yard.qa_exec import run_case_script

    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    job = CaseJob(
        id="c1",
        title="t",
        repo="be",
        setup="../evil.sh",
        path=str(case_dir / "case-c1.md"),
    )
    with pytest.raises(TestRejected, match="bare filename"):
        run_case_script(tmp_path, "AB-1", None, job, "setup")


def test_run_case_script_rejects_absolute_path(tmp_path: Path):
    from dev_yard.qa_exec import run_case_script

    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    evil = tmp_path / "evil.sh"
    evil.write_text("echo hi\n")
    job = CaseJob(
        id="c1",
        title="t",
        repo="be",
        setup=str(evil),
        path=str(case_dir / "case-c1.md"),
    )
    with pytest.raises(TestRejected, match="bare filename"):
        run_case_script(tmp_path, "AB-1", None, job, "setup")


def test_shell_substitution_is_quoted(monkeypatch):
    from dev_yard.script_exec import _subst_shell

    monkeypatch.setenv("YARD_DANGER", "a; rm -rf /")
    text, names = _subst_shell("run {env:YARD_DANGER} now")
    assert names == ["YARD_DANGER"]
    assert "; rm -rf /" not in text or "'a; rm -rf /'" in text
    assert "'a; rm -rf /'" in text


def test_push_uses_force_with_lease(tmp_path: Path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(gitops, "run", lambda args, cwd=None: calls.append(args) or "")
    gitops.push(tmp_path, force=True, branch="req/AB-1")
    assert ["--force-with-lease"] == [a for a in calls[-1] if a == "--force-with-lease"]
    assert "--force" not in calls[-1]


def test_status_load_tolerates_corrupt_file(tmp_path: Path):
    root = tmp_path
    d = root / "reqs" / "AB-1"
    d.mkdir(parents=True)
    (d / "STATUS.yaml").write_text("envs: [unclosed\n", encoding="utf-8")
    data = st.load(root, "AB-1")
    assert data["phase"] == "open"
    assert data["tickets"] == {}


def test_status_save_is_round_trip_and_leaves_no_temp(tmp_path: Path):
    root = tmp_path
    st.save(root, "AB-1", {"phase": "frozen", "tickets": {"T1": {"state": "ready"}}})
    again = st.load(root, "AB-1")
    assert again["phase"] == "frozen"
    assert again["tickets"]["T1"]["state"] == "ready"
    leftovers = list((root / "reqs" / "AB-1").glob("STATUS.yaml.*.tmp"))
    assert leftovers == []


def test_findings_parallel_coercion_preserves_absent_vs_garbage():
    from dev_yard.bug_tickets import normalize_findings

    rows = normalize_findings(
        [
            {"id": "F1", "title": "a", "repo": "be"},
            {"id": "F2", "title": "b", "repo": "be", "parallel": "maybe"},
            {"id": "F3", "title": "c", "repo": "be", "parallel": "yes"},
        ]
    )
    assert rows[0]["parallel"] is None  # absent → auto-derive
    assert rows[1]["parallel"] is False  # unrecognised → False, not auto
    assert rows[2]["parallel"] is True
