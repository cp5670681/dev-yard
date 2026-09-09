from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dev_yard import gitops, status as st
from dev_yard.service import init_yard, repo_add, req_delete, req_freeze, req_open, ticket_start
from dev_yard.web.app import create_app


def _client(yard: Path) -> TestClient:
    return TestClient(create_app(yard, sync_jobs=True))


def test_req_delete_docs_only_keeps_glossary(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-70", source="none")
    (yard / "reqs" / "CONTEXT.md").write_text("# glossary\n")
    adr = yard / "reqs" / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001.md").write_text("# adr\n")
    req_delete(yard, "AB-70")
    assert not (yard / "reqs" / "AB-70").exists()
    assert (yard / "reqs" / "CONTEXT.md").read_text() == "# glossary\n"
    assert (adr / "0001.md").read_text() == "# adr\n"


def test_req_delete_removes_worktrees_and_branches(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-71", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-71")
    child = ticket_start(yard, "AB-71", "T1")
    assert child.exists()
    listed = gitops.run(["git", "branch", "--list", "req/AB-71"], cwd=git_src)
    assert "req/AB-71" in listed
    req_delete(yard, "AB-71")
    assert not d.exists()
    assert not child.exists()
    assert gitops.run(["git", "branch", "--list", "req/AB-71"], cwd=git_src) == ""
    assert gitops.run(["git", "branch", "--list", "req/AB-71-T1"], cwd=git_src) == ""


def test_req_delete_missing(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    with pytest.raises(FileNotFoundError):
        req_delete(yard, "NOPE-1")


def test_api_delete_requirement(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-72", source="none")
    client = _client(yard)
    r = client.delete("/api/requirements/AB-72")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.get("/api/requirements").json() == []
    assert client.delete("/api/requirements/AB-72").status_code == 404
    vue = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "views" / "DashboardView.vue"
    ).read_text()
    assert "askDelete" in vue
    assert "forgetRecent" in vue
    assert "删除需求？" in vue
    detail = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "views" / "RequirementView.vue"
    ).read_text()
    assert "deleteOpen" in detail
    assert "doDelete" in detail
    assert "forgetRecent" in detail
    recents = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "composables" / "recents.ts"
    ).read_text()
    assert "export function forgetRecent" in recents


def test_api_delete_conflict_when_job_running(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-73", source="none")
    from dev_yard.web.jobs import Job, JobRunner

    runner = JobRunner(yard, sync=True)
    job = Job(id="deadbeef01", jira="AB-73", action="grill", state="running")
    runner._jobs[job.id] = job
    client = TestClient(create_app(yard, job_runner=runner, sync_jobs=True))
    r = client.delete("/api/requirements/AB-73")
    assert r.status_code == 409
    assert st.load(yard, "AB-73")["jira"] == "AB-73"
