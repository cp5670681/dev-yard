import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dev_yard import paths
from dev_yard.cli import app
from dev_yard.service import init_yard, repo_add, req_freeze, req_open, req_push
from dev_yard.web.app import create_app
from dev_yard.web.board import requirement_detail
from fastapi.testclient import TestClient


def _setup_yard_with_remotes(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)

    # Repo 1: backend
    bare_be = tmp_path / "bare_be.git"
    bare_be.mkdir()
    subprocess.check_call(["git", "init", "--bare"], cwd=bare_be)

    src_be = tmp_path / "src_be"
    src_be.mkdir()
    subprocess.check_call(["git", "init"], cwd=src_be)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=src_be)
    subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=src_be)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=src_be)
    (src_be / "README.md").write_text("backend repo")
    subprocess.check_call(["git", "add", "."], cwd=src_be)
    subprocess.check_call(["git", "commit", "-m", "init be"], cwd=src_be)
    subprocess.check_call(["git", "remote", "add", "origin", str(bare_be)], cwd=src_be)
    subprocess.check_call(["git", "push", "-u", "origin", "main"], cwd=src_be)

    # Repo 2: frontend
    bare_fe = tmp_path / "bare_fe.git"
    bare_fe.mkdir()
    subprocess.check_call(["git", "init", "--bare"], cwd=bare_fe)

    src_fe = tmp_path / "src_fe"
    src_fe.mkdir()
    subprocess.check_call(["git", "init"], cwd=src_fe)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=src_fe)
    subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=src_fe)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=src_fe)
    (src_fe / "README.md").write_text("frontend repo")
    subprocess.check_call(["git", "add", "."], cwd=src_fe)
    subprocess.check_call(["git", "commit", "-m", "init fe"], cwd=src_fe)
    subprocess.check_call(["git", "remote", "add", "origin", str(bare_fe)], cwd=src_fe)
    subprocess.check_call(["git", "push", "-u", "origin", "main"], cwd=src_fe)

    repo_add(yard, "backend", str(bare_be), "main", "be", str(src_be))
    repo_add(yard, "frontend", str(bare_fe), "main", "fe", str(src_fe))

    return yard, bare_be, bare_fe, src_be, src_fe


def test_req_push_unfrozen_raises(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, _, _ = _setup_yard_with_remotes(tmp_path)
    req_open(yard, "PROJ-1", source="none")
    with pytest.raises(ValueError, match="no worktrees found"):
        req_push(yard, "PROJ-1")


def test_req_push_nonexistent_raises(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    with pytest.raises(FileNotFoundError, match="no requirement"):
        req_push(yard, "NONEXISTENT")


def test_req_push_success(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, bare_fe, _, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-10", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: frontend task\n- repo: frontend\n- depends_on: T1\n- parallel: false\n"
    )
    wts = req_freeze(yard, "PROJ-10")
    assert len(wts) == 2

    # Add changes in worktrees
    wt_be = paths.req_worktree(yard, "PROJ-10", "backend")
    (wt_be / "be_code.py").write_text("print('be')")
    subprocess.check_call(["git", "add", "."], cwd=wt_be)
    subprocess.check_call(["git", "commit", "-m", "feat: backend feature"], cwd=wt_be)

    wt_fe = paths.req_worktree(yard, "PROJ-10", "frontend")
    (wt_fe / "fe_code.js").write_text("console.log('fe')")
    subprocess.check_call(["git", "add", "."], cwd=wt_fe)
    subprocess.check_call(["git", "commit", "-m", "feat: frontend feature"], cwd=wt_fe)

    progress_lines: list[str] = []
    results = req_push(yard, "PROJ-10", on_progress=progress_lines.append)
    assert len(results) == 2
    assert {r["repo"] for r in results} == {"backend", "frontend"}

    # Verify both remotes have req/PROJ-10 branch
    sha_be_remote = subprocess.check_output(
        ["git", "rev-parse", "refs/heads/req/PROJ-10"], cwd=bare_be, text=True
    ).strip()
    sha_be_local = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=wt_be, text=True
    ).strip()
    assert sha_be_remote == sha_be_local

    sha_fe_remote = subprocess.check_output(
        ["git", "rev-parse", "refs/heads/req/PROJ-10"], cwd=bare_fe, text=True
    ).strip()
    sha_fe_local = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=wt_fe, text=True
    ).strip()
    assert sha_fe_remote == sha_fe_local


def test_req_push_filter_repos(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, bare_fe, _, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-20", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: frontend task\n- repo: frontend\n- depends_on: T1\n- parallel: false\n"
    )
    req_freeze(yard, "PROJ-20")

    # Push only backend
    results = req_push(yard, "PROJ-20", repos=["backend"])
    assert len(results) == 1
    assert results[0]["repo"] == "backend"

    # Backend remote has branch, frontend remote does not
    assert subprocess.call(
        ["git", "rev-parse", "refs/heads/req/PROJ-20"], cwd=bare_be
    ) == 0
    assert subprocess.call(
        ["git", "rev-parse", "refs/heads/req/PROJ-20"], cwd=bare_fe
    ) != 0

    # Unknown repo raises
    with pytest.raises(ValueError, match="worktree not found for repo"):
        req_push(yard, "PROJ-20", repos=["unknown_repo"])


def test_req_push_auto_commits_dirty_worktree(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, _, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-30", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "PROJ-30")

    wt_be = paths.req_worktree(yard, "PROJ-30", "backend")
    (wt_be / "uncommitted.txt").write_text("dirty file")

    results = req_push(yard, "PROJ-30")
    assert len(results) == 1

    # Verify remote has the commit with the uncommitted file
    log = subprocess.check_output(
        ["git", "log", "-n", "1", "--oneline", "refs/heads/req/PROJ-30"], cwd=bare_be, text=True
    )
    assert "chore: commit pending changes before push" in log


def test_cli_push_commands(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, _, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-40", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "PROJ-40")

    runner = CliRunner()
    monkeypatch.chdir(yard)

    # Test dev-yard push
    res1 = runner.invoke(app, ["push", "PROJ-40"])
    assert res1.exit_code == 0
    assert "pushed backend (req/PROJ-40) -> origin" in res1.output

    # Test dev-yard req push
    res2 = runner.invoke(app, ["req", "push", "PROJ-40"])
    assert res2.exit_code == 0
    assert "pushed backend (req/PROJ-40) -> origin" in res2.output


def test_web_push_action(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, _, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-50", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )

    # Before freeze, push action is disabled
    detail_before = requirement_detail(yard, "PROJ-50")
    push_act = next((a for a in detail_before.actions if a.id == "push"), None)
    assert push_act is not None
    assert not push_act.enabled

    # After freeze, push action is enabled
    req_freeze(yard, "PROJ-50")
    detail_after = requirement_detail(yard, "PROJ-50")
    push_act_after = next((a for a in detail_after.actions if a.id == "push"), None)
    assert push_act_after is not None
    assert push_act_after.enabled

    # Trigger push via web API
    client = TestClient(create_app(yard, sync_jobs=True))
    resp = client.post("/api/requirements/PROJ-50/actions/push", json={})
    assert resp.status_code == 200
    jobs = resp.json().get("jobs", [])
    assert len(jobs) == 1
    assert jobs[0]["action"] == "push"
    assert jobs[0]["state"] == "ok"
    assert "pushed PROJ-50 to remote" in jobs[0]["log"]

    # Verify remote has branch
    assert subprocess.call(
        ["git", "rev-parse", "refs/heads/req/PROJ-50"], cwd=bare_be
    ) == 0
