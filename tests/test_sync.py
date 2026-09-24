import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dev_yard import paths
from dev_yard.cli import app
from dev_yard.gitops import GitError, head_sha
from dev_yard.service import (
    init_yard,
    repo_add,
    req_freeze,
    req_open,
    req_pull,
    req_push,
    req_sync,
)
from dev_yard.web.app import create_app
from dev_yard.web.board import requirement_detail


def _setup_yard_with_remotes(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)

    bare_be = tmp_path / "bare_be.git"
    bare_be.mkdir()
    subprocess.check_call(["git", "init", "--bare"], cwd=bare_be)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=bare_be)

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

    bare_fe = tmp_path / "bare_fe.git"
    bare_fe.mkdir()
    subprocess.check_call(["git", "init", "--bare"], cwd=bare_fe)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=bare_fe)

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


def _advance_origin(bare: Path, src: Path, name: str, body: str) -> None:
    other = src.parent / f"{src.name}-{name.replace('.', '-')}"
    subprocess.check_call(["git", "clone", "-b", "main", str(bare), str(other)])
    subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=other)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=other)
    (other / name).write_text(body)
    subprocess.check_call(["git", "add", "."], cwd=other)
    subprocess.check_call(["git", "commit", "-m", f"add {name}"], cwd=other)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=other)


def test_req_sync_missing_requirement(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    with pytest.raises(FileNotFoundError, match="no requirement"):
        req_sync(yard, "NONE")


def test_req_sync_no_repos(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "PROJ-1", source="none")
    with pytest.raises(ValueError, match="no repos registered"):
        req_sync(yard, "PROJ-1")


def test_req_sync_fetches_unfrozen_path_mapped(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, src_be, _ = _setup_yard_with_remotes(tmp_path)
    req_open(yard, "PROJ-2", source="none")
    old_head = head_sha(src_be)
    _advance_origin(bare_be, src_be, "new_be.txt", "from origin")
    results = req_sync(yard, "PROJ-2", repos=["backend"])
    assert results[0]["status"] == "fetched"
    assert results[0]["worktree"] is None
    origin_sha = subprocess.check_output(
        ["git", "rev-parse", "origin/main"], cwd=src_be, text=True
    ).strip()
    assert origin_sha != old_head
    assert head_sha(src_be) == old_head


def test_req_sync_ff_worktree(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, src_be, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-3", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "PROJ-3")
    wt = paths.req_worktree(yard, "PROJ-3", "backend")
    before = head_sha(wt)
    _advance_origin(bare_be, src_be, "new_be.txt", "from origin")
    results = req_sync(yard, "PROJ-3", repos=["backend"])
    assert results[0]["status"] == "synced"
    assert results[0]["from"] == before
    assert (wt / "new_be.txt").read_text() == "from origin"


def test_req_sync_frozen_url_clone_does_not_checkout_source(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)

    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.check_call(["git", "init", "--bare"], cwd=bare)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=bare)

    src = tmp_path / "src"
    src.mkdir()
    subprocess.check_call(["git", "init"], cwd=src)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=src)
    subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=src)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=src)
    (src / "README.md").write_text("be")
    subprocess.check_call(["git", "add", "."], cwd=src)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=src)
    subprocess.check_call(["git", "remote", "add", "origin", str(bare)], cwd=src)
    subprocess.check_call(["git", "push", "-u", "origin", "main"], cwd=src)

    repo_add(yard, "backend", str(bare), "main", "be", None)
    d, _ = req_open(yard, "PROJ-7", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "PROJ-7")
    clone = yard / ".repos" / "backend"
    clone_head = head_sha(clone)
    wt = paths.req_worktree(yard, "PROJ-7", "backend")
    _advance_origin(bare, src, "from_origin.txt", "new")
    results = req_sync(yard, "PROJ-7", repos=["backend"])
    assert results[0]["status"] == "synced"
    assert (wt / "from_origin.txt").read_text() == "new"
    assert head_sha(clone) == clone_head
    origin = subprocess.check_output(
        ["git", "rev-parse", "origin/main"], cwd=clone, text=True
    ).strip()
    assert origin != clone_head


def test_req_sync_dirty_worktree_raises(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, _, _, _, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-4", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "PROJ-4")
    wt = paths.req_worktree(yard, "PROJ-4", "backend")
    (wt / "dirty.txt").write_text("x")
    with pytest.raises(GitError, match="uncommitted"):
        req_sync(yard, "PROJ-4")


def test_req_sync_merge_when_diverged(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, src_be, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-5", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "PROJ-5")
    wt = paths.req_worktree(yard, "PROJ-5", "backend")
    (wt / "feat.txt").write_text("feat")
    subprocess.check_call(["git", "add", "."], cwd=wt)
    subprocess.check_call(["git", "commit", "-m", "feat"], cwd=wt)
    _advance_origin(bare_be, src_be, "main.txt", "main")
    with pytest.raises(GitError):
        req_sync(yard, "PROJ-5", repos=["backend"], strategy="ff-only")
    results = req_sync(yard, "PROJ-5", repos=["backend"], strategy="merge")
    assert results[0]["status"] == "synced"
    assert (wt / "feat.txt").exists()
    assert (wt / "main.txt").exists()


def test_cli_and_web_sync(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, src_be, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, "PROJ-6", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    detail = requirement_detail(yard, "PROJ-6")
    sync_act = next(a for a in detail.actions if a.id == "sync")
    assert sync_act.enabled

    req_freeze(yard, "PROJ-6")
    _advance_origin(bare_be, src_be, "cli.txt", "cli")
    monkeypatch.chdir(yard)
    res = CliRunner().invoke(app, ["req", "sync", "PROJ-6", "backend"])
    assert res.exit_code == 0, res.output
    assert "synced backend" in res.output
    wt = paths.req_worktree(yard, "PROJ-6", "backend")
    assert (wt / "cli.txt").exists()

    _advance_origin(bare_be, src_be, "web.txt", "web")
    client = TestClient(create_app(yard, sync_jobs=True))
    resp = client.post(
        "/api/requirements/PROJ-6/actions/sync",
        json={"repos": ["backend"], "strategy": "ff-only"},
    )
    assert resp.status_code == 200
    job = resp.json()["jobs"][0]
    assert job["action"] == "sync"
    assert job["state"] == "ok"
    assert "backend:synced" in job["log"]
    assert (wt / "web.txt").exists()


def _advance_branch(bare: Path, src: Path, branch: str, name: str, body: str) -> None:
    other = src.parent / f"{src.name}-{name.replace('.', '-')}"
    subprocess.check_call(["git", "clone", "-b", branch, str(bare), str(other)])
    subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=other)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=other)
    (other / name).write_text(body)
    subprocess.check_call(["git", "add", "."], cwd=other)
    subprocess.check_call(["git", "commit", "-m", f"add {name}"], cwd=other)
    subprocess.check_call(["git", "push", "origin", branch], cwd=other)


def _branch_of(wt: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=wt, text=True
    ).strip()


def _frozen_yard(tmp_path: Path, monkeypatch, jira: str):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard, bare_be, _, src_be, _ = _setup_yard_with_remotes(tmp_path)
    d, _ = req_open(yard, jira, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend task\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, jira)
    return yard, bare_be, src_be


def test_req_pull_ff_worktree(tmp_path: Path, monkeypatch):
    yard, bare_be, src_be = _frozen_yard(tmp_path, monkeypatch, "PROJ-8")
    wt = paths.req_worktree(yard, "PROJ-8", "backend")
    branch = _branch_of(wt)
    req_push(yard, "PROJ-8", repos=["backend"])
    before = head_sha(wt)
    _advance_branch(bare_be, src_be, branch, "remote.txt", "from remote")
    results = req_pull(yard, "PROJ-8", repos=["backend"])
    assert results[0]["status"] == "pulled"
    assert results[0]["from"] == before
    assert results[0]["to"] != before
    assert (wt / "remote.txt").read_text() == "from remote"


def test_req_pull_up_to_date_when_remote_not_ahead(tmp_path: Path, monkeypatch):
    yard, _, _ = _frozen_yard(tmp_path, monkeypatch, "PROJ-9")
    req_push(yard, "PROJ-9", repos=["backend"])
    results = req_pull(yard, "PROJ-9", repos=["backend"])
    assert results[0]["status"] == "up-to-date"


def test_req_pull_missing_remote_branch(tmp_path: Path, monkeypatch):
    yard, _, _ = _frozen_yard(tmp_path, monkeypatch, "PROJ-10")
    with pytest.raises(GitError, match="not found"):
        req_pull(yard, "PROJ-10", repos=["backend"])


def test_req_pull_dirty_worktree_raises(tmp_path: Path, monkeypatch):
    yard, _, _ = _frozen_yard(tmp_path, monkeypatch, "PROJ-11")
    wt = paths.req_worktree(yard, "PROJ-11", "backend")
    req_push(yard, "PROJ-11", repos=["backend"])
    (wt / "dirty.txt").write_text("x")
    with pytest.raises(GitError, match="uncommitted"):
        req_pull(yard, "PROJ-11", repos=["backend"])


def test_cli_and_web_pull(tmp_path: Path, monkeypatch):
    yard, bare_be, src_be = _frozen_yard(tmp_path, monkeypatch, "PROJ-12")
    wt = paths.req_worktree(yard, "PROJ-12", "backend")
    branch = _branch_of(wt)
    req_push(yard, "PROJ-12", repos=["backend"])

    detail = requirement_detail(yard, "PROJ-12")
    pull_act = next(a for a in detail.actions if a.id == "pull")
    assert pull_act.enabled
    assert pull_act.stage == "utility"

    _advance_branch(bare_be, src_be, branch, "cli.txt", "cli")
    monkeypatch.chdir(yard)
    res = CliRunner().invoke(app, ["req", "pull", "PROJ-12", "backend"])
    assert res.exit_code == 0, res.output
    assert "pulled backend" in res.output
    assert (wt / "cli.txt").read_text() == "cli"

    _advance_branch(bare_be, src_be, branch, "web.txt", "web")
    client = TestClient(create_app(yard, sync_jobs=True))
    resp = client.post(
        "/api/requirements/PROJ-12/actions/pull",
        json={"repos": ["backend"], "strategy": "ff-only"},
    )
    assert resp.status_code == 200
    job = resp.json()["jobs"][0]
    assert job["action"] == "pull"
    assert job["state"] == "ok"
    assert "backend:pulled" in job["log"]
    assert (wt / "web.txt").read_text() == "web"

