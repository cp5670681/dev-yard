import subprocess
from pathlib import Path

import pytest

from dev_yard.gitops import GitError, current_branch
from dev_yard.service import ensure_on_default_base, init_yard, repo_add


def test_path_clone_not_moved_when_off_base(tmp_path: Path, git_src: Path):
    subprocess.check_call(["git", "checkout", "-b", "wip"], cwd=git_src)
    (git_src / "WIP").write_text("no")
    subprocess.check_call(["git", "add", "."], cwd=git_src)
    subprocess.check_call(["git", "commit", "-m", "wip"], cwd=git_src)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    with pytest.raises(GitError, match="will not move"):
        ensure_on_default_base(yard)
    assert current_branch(git_src) == "wip"
    assert (git_src / "WIP").exists()


def test_path_clone_ok_when_already_on_base(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    mapping = ensure_on_default_base(yard)
    assert mapping["backend"] == git_src.resolve()
    assert current_branch(git_src) == "main"


def test_managed_clone_is_checked_out(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", None)
    clone = yard / ".repos" / "backend"
    subprocess.check_call(["git", "checkout", "-b", "wip"], cwd=clone)
    (clone / "WIP").write_text("no")
    subprocess.check_call(["git", "add", "."], cwd=clone)
    subprocess.check_call(["git", "commit", "-m", "wip"], cwd=clone)
    mapping = ensure_on_default_base(yard)
    assert mapping["backend"] == clone.resolve()
    assert current_branch(clone) == "main"
    assert not (clone / "WIP").exists()
