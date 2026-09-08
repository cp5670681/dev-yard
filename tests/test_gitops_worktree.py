import subprocess
from pathlib import Path

import pytest

from dev_yard.gitops import GitError, branch_delete, run, worktree_add, worktree_remove


def test_worktree_add_clears_empty_leftover(git_src: Path, tmp_path: Path):
    leftover = tmp_path / "wt"
    leftover.mkdir()
    worktree_add(git_src, leftover, "req/AB-1", "main")
    assert (leftover / ".git").exists()
    worktree_remove(git_src, leftover)


def test_worktree_add_rejects_non_git_dir(git_src: Path, tmp_path: Path):
    junk = tmp_path / "wt"
    junk.mkdir()
    (junk / "file").write_text("x")
    with pytest.raises(GitError, match="not a git worktree"):
        worktree_add(git_src, junk, "req/AB-1", "main")


def test_reset_existing_repoints_branch(git_src: Path, tmp_path: Path):
    subprocess.check_call(["git", "checkout", "-b", "req/AB-1/T1"], cwd=git_src)
    (git_src / "OLD").write_text("old")
    subprocess.check_call(["git", "add", "."], cwd=git_src)
    subprocess.check_call(["git", "commit", "-m", "old"], cwd=git_src)
    subprocess.check_call(["git", "checkout", "main"], cwd=git_src)
    child = tmp_path / "child"
    worktree_add(git_src, child, "req/AB-1/T1", "main", reset_existing=True)
    assert not (child / "OLD").exists()
    worktree_remove(git_src, child)
    branch_delete(git_src, "req/AB-1/T1")
    listed = run(["git", "branch", "--list", "req/AB-1/T1"], cwd=git_src)
    assert listed == ""


def test_reset_existing_resets_already_checked_out_worktree(git_src: Path, tmp_path: Path):
    child = tmp_path / "child"
    worktree_add(git_src, child, "req/AB-1/T1", "main")
    (child / "OLD").write_text("old")
    subprocess.check_call(["git", "add", "."], cwd=child)
    subprocess.check_call(["git", "commit", "-m", "old"], cwd=child)
    worktree_add(git_src, child, "req/AB-1/T1", "main", reset_existing=True)
    assert not (child / "OLD").exists()
    worktree_remove(git_src, child)
    branch_delete(git_src, "req/AB-1/T1")


def test_worktree_remove_prunes_missing_dir(git_src: Path, tmp_path: Path):
    child = tmp_path / "child"
    worktree_add(git_src, child, "req/AB-1/T1", "main")
    import shutil

    shutil.rmtree(child)
    worktree_remove(git_src, child)
    worktree_add(git_src, child, "req/AB-1/T1", "main", reset_existing=True)
    assert (child / ".git").exists()
    worktree_remove(git_src, child)
    branch_delete(git_src, "req/AB-1/T1")
