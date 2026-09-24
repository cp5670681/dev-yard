"""req export / import-bundle: portable, state-preserving requirement snapshots."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dev_yard import bundle, gitops, paths
from dev_yard import status as st
from dev_yard.cli import app
from dev_yard.service import (
    init_yard,
    repo_add,
    req_freeze,
    req_open,
    ticket_done,
    ticket_start,
)


@pytest.fixture(autouse=True)
def _no_jira_env(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README").write_text("x\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _frozen_yard(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A yard with a frozen AB-1 (T1 done in backend, T2 in progress in frontend)."""
    be = _make_repo(tmp_path / "be")
    fe = _make_repo(tmp_path / "fe")
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(be), "main", "be", str(be))
    repo_add(yard, "frontend", str(fe), "main", "fe", str(fe))
    d, _ = req_open(yard, "AB-1", source="text", payload="# AB-1\n\nhello\n")
    (d / "TICKETS.md").write_text(
        "## T1: one\n- repo: backend\n- depends_on:\n- parallel: false\n"
        "## T2: two\n- repo: frontend\n- depends_on: T1\n- parallel: false\n",
        encoding="utf-8",
    )
    req_freeze(yard, "AB-1")
    child = ticket_start(yard, "AB-1", "T1")
    (child / "feat.txt").write_text("feature\n")
    _git(child, "add", ".")
    _git(child, "commit", "-q", "-m", "feature")
    ticket_done(yard, "AB-1", "T1")
    # T2 stays in progress: a child worktree with a committed edit.
    child2 = ticket_start(yard, "AB-1", "T2")
    (child2 / "wip.txt").write_text("wip\n")
    _git(child2, "add", ".")
    _git(child2, "commit", "-q", "-m", "wip")
    data = st.load(yard, "AB-1")
    data["phase"] = "testing"
    data["contract_review"] = "passed"
    data["test"] = {"status": "awaiting", "latest_verdict": None}
    st.save(yard, "AB-1", data)
    return yard, be, fe


def test_export_import_round_trip(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    before = st.load(yard, "AB-1")
    out = bundle.req_export(yard, "AB-1", tmp_path / "exp")
    assert (out / "manifest.yaml").is_file()
    assert (out / "reqs" / "AB-1" / "REQUIREMENT.md").is_file()
    assert (out / "bundles" / "backend.bundle").is_file()
    # git worktrees are never copied into the bundle.
    assert not (out / "reqs" / "AB-1" / "worktrees").exists()

    # A second machine: repos not registered yet.
    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    data = bundle.req_import_bundle(yard2, out)

    assert data["phase"] == "testing"
    assert data["contract_review"] == "passed"
    assert data["tickets"]["T1"]["state"] == "done"
    assert data["tickets"]["T1"]["head_sha"] == before["tickets"]["T1"]["head_sha"]
    assert data["tickets"]["T2"]["state"] == "ready"

    be_wt = paths.req_worktree(yard2, "AB-1", "backend")
    fe_wt = paths.req_worktree(yard2, "AB-1", "frontend")
    assert gitops.current_branch(be_wt) == "req/AB-1"
    assert (be_wt / "feat.txt").read_text() == "feature\n"
    assert gitops.current_branch(fe_wt) == "req/AB-1"

    child2 = paths.child_worktree(yard2, "AB-1", "frontend", "T2")
    assert (child2 / "wip.txt").read_text() == "wip\n"
    assert data["tickets"]["T2"]["child_worktree"] == str(child2)
    assert data["tickets"]["T1"]["worktree"] == str(be_wt)
    # Repos were auto-registered from the manifest.
    from dev_yard.config import load_repos

    assert set(load_repos(yard2)) == {"backend", "frontend"}


def test_export_strips_absolute_worktree_paths(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    out = bundle.req_export(yard, "AB-1", tmp_path / "exp")
    text = (out / "reqs" / "AB-1" / "STATUS.yaml").read_text(encoding="utf-8")
    assert str(yard) not in text
    exported = yaml.safe_load(text)
    assert "worktree" not in exported["tickets"]["T1"]
    assert "child_worktree" not in exported["tickets"]["T2"]


def test_export_refuses_dirty_then_snapshots(tmp_path: Path):
    yard, be, _fe = _frozen_yard(tmp_path)
    be_wt = paths.req_worktree(yard, "AB-1", "backend")
    (be_wt / "late.txt").write_text("late\n")

    with pytest.raises(ValueError, match="uncommitted changes"):
        bundle.req_export(yard, "AB-1", tmp_path / "exp")

    out = bundle.req_export(yard, "AB-1", tmp_path / "exp", snapshot=True)
    assert not gitops.has_changes(be_wt)

    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    bundle.req_import_bundle(yard2, out)
    restored = paths.req_worktree(yard2, "AB-1", "backend") / "late.txt"
    assert restored.read_text() == "late\n"


def _remote_backed_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin plus a registered clone whose base ref is `origin/main`."""
    remote = tmp_path / "origin.git"
    subprocess.check_call(["git", "init", "--bare", "-q", str(remote)])
    work = tmp_path / "work"
    subprocess.check_call(["git", "clone", "-q", str(remote), str(work)])
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    (work / "README").write_text("x\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-q", "-u", "origin", "main")
    return remote, work


def test_export_empty_branch_has_no_thin_bundle(tmp_path: Path):
    remote, _work = _remote_backed_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(remote), "main", "be", None)
    d, _ = req_open(yard, "AB-2", source="text", payload="# AB-2\n")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n", encoding="utf-8"
    )
    req_freeze(yard, "AB-2")  # no commits beyond origin/main

    out = bundle.req_export(yard, "AB-2", tmp_path / "exp")
    manifest = yaml.safe_load((out / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["repos"]["backend"]["bundle"] is None
    assert manifest["repos"]["backend"]["thin"] is True

    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    data = bundle.req_import_bundle(yard2, out)
    assert data["phase"] == "frozen"
    wt = paths.req_worktree(yard2, "AB-2", "backend")
    assert gitops.current_branch(wt) == "req/AB-2"


def test_full_bundle_restores_offline(tmp_path: Path):
    remote, work = _remote_backed_repo(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(remote), "main", "be", None)
    d, _ = req_open(yard, "AB-4", source="text", payload="# AB-4\n")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n", encoding="utf-8"
    )
    req_freeze(yard, "AB-4")
    wt = paths.req_worktree(yard, "AB-4", "backend")
    (wt / "feature.txt").write_text("feature\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "feature")

    out = bundle.req_export(yard, "AB-4", tmp_path / "exp", full=True)

    # Offline: origin is gone, so the bundle must carry everything.
    shutil.rmtree(remote)
    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    data = bundle.req_import_bundle(yard2, out)
    assert data["phase"] == "frozen"
    restored = paths.req_worktree(yard2, "AB-4", "backend") / "feature.txt"
    assert restored.read_text() == "feature\n"


def test_export_carries_branch_after_child_worktree_removed(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    from dev_yard.config import load_repos

    child2 = paths.child_worktree(yard, "AB-1", "frontend", "T2")
    # Drop the worktree but keep the branch: the branch must still be exported.
    gitops.worktree_remove(load_repos(yard)["frontend"].source_path(yard), child2)
    assert not child2.exists()

    out = bundle.req_export(yard, "AB-1", tmp_path / "exp")
    manifest = yaml.safe_load((out / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["repos"]["frontend"]["children"] == {"T2": "req/AB-1-T2"}

    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    data = bundle.req_import_bundle(yard2, out)
    restored = paths.child_worktree(yard2, "AB-1", "frontend", "T2")
    assert (restored / "wip.txt").read_text() == "wip\n"
    assert data["tickets"]["T2"]["child_worktree"] == str(restored)


def test_normalize_git_url_matches_transports():
    from dev_yard.config import normalize_git_url

    forms = [
        "git@git.rccchina.com:leads-in/research.git",
        "ssh://git@git.rccchina.com/leads-in/research.git",
        "https://git.rccchina.com/leads-in/research.git",
        "https://git.rccchina.com/leads-in/research",
    ]
    assert len({normalize_git_url(u) for u in forms}) == 1
    assert normalize_git_url("/tmp/local/repo") == "/tmp/local/repo"


def test_export_docs_only_before_freeze(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-3", source="text", payload="# AB-3\n\ndraft\n")

    out = bundle.req_export(yard, "AB-3", tmp_path / "exp")
    manifest = yaml.safe_load((out / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["repos"] == {}

    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    data = bundle.req_import_bundle(yard2, out)
    assert data["phase"] == "open"
    assert (paths.req_dir(yard2, "AB-3") / "REQUIREMENT.md").is_file()


def test_import_refuses_existing_without_force(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    out = bundle.req_export(yard, "AB-1", tmp_path / "exp")
    with pytest.raises(ValueError, match="already exists"):
        bundle.req_import_bundle(yard, out)


def test_import_force_overwrites(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    out = bundle.req_export(yard, "AB-1", tmp_path / "exp")
    # Corrupt the live state, then restore over it.
    data = st.load(yard, "AB-1")
    data["tickets"]["T1"]["state"] = "blocked"
    st.save(yard, "AB-1", data)

    restored = bundle.req_import_bundle(yard, out, force=True)
    assert restored["tickets"]["T1"]["state"] == "done"


def test_archive_round_trip(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    out = bundle.req_export(yard, "AB-1", tmp_path / "exp.tar.gz", archive=True)
    assert out.name == "exp.tar.gz" and out.is_file()

    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    data = bundle.req_import_bundle(yard2, out)
    assert data["phase"] == "testing"
    assert paths.req_worktree(yard2, "AB-1", "backend").exists()


def test_export_includes_accounts_only_when_asked(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    accounts = paths.req_accounts_yaml(yard, "AB-1")
    accounts.parent.mkdir(parents=True, exist_ok=True)
    accounts.write_text("env:\n  accounts:\n    - username: u\n      password: p\n")

    plain = bundle.req_export(yard, "AB-1", tmp_path / "plain")
    assert not (plain / "accounts.yaml").exists()

    with_acc = bundle.req_export(
        yard, "AB-1", tmp_path / "with", include_accounts=True
    )
    assert (with_acc / "accounts.yaml").is_file()

    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    bundle.req_import_bundle(yard2, with_acc)
    restored = paths.req_accounts_yaml(yard2, "AB-1")
    assert restored.read_text() == accounts.read_text()


def test_import_url_mismatch_raises(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    out = bundle.req_export(yard, "AB-1", tmp_path / "exp")

    other = _make_repo(tmp_path / "other")
    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    repo_add(yard2, "backend", str(other), "main", "be", None)
    with pytest.raises(ValueError, match="differs from bundle"):
        bundle.req_import_bundle(yard2, out)


def test_export_requires_registered_repo(tmp_path: Path):
    yard, _be, _fe = _frozen_yard(tmp_path)
    data = st.load(yard, "AB-1")
    data["repos"] = sorted(set(data["repos"]) | {"ghost"})
    data["tickets"]["T9"] = {"state": "pending", "repo": "ghost"}
    st.save(yard, "AB-1", data)
    with pytest.raises(ValueError, match="not registered"):
        bundle.req_export(yard, "AB-1", tmp_path / "exp")


def test_cli_export_and_import_bundle(tmp_path: Path, monkeypatch):
    yard, _be, _fe = _frozen_yard(tmp_path)
    monkeypatch.chdir(yard)
    runner = CliRunner()
    out_dir = tmp_path / "cli-exp"

    res = runner.invoke(app, ["req", "export", "AB-1", "-o", str(out_dir)])
    assert res.exit_code == 0, res.output
    assert str(out_dir) in res.stdout

    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    monkeypatch.chdir(yard2)
    res = runner.invoke(app, ["req", "import-bundle", str(out_dir)])
    assert res.exit_code == 0, res.output
    assert "AB-1 phase=testing" in res.stdout
    assert paths.req_worktree(yard2, "AB-1", "backend").exists()
