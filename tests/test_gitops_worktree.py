import subprocess
from pathlib import Path

import pytest

from dev_yard.gitops import (
    GitError,
    branch_delete,
    drain_git_output,
    ensure_clone,
    git_failure_message,
    run,
    worktree_add,
    worktree_remove,
)


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


def test_drain_git_output_splits_cr_and_lf():
    lines: list[str] = []
    leftover = drain_git_output(
        b"Receiving objects:  10% (1/10)\rReceiving objects:  40% (4/10)\nResolving",
        lines.append,
    )
    assert lines == [
        "Receiving objects:  10% (1/10)",
        "Receiving objects:  40% (4/10)",
    ]
    assert leftover == b"Resolving"


def test_git_failure_message_prefers_fatal_over_progress():
    msg = git_failure_message(
        [
            "Receiving objects:  40% (4/10)",
            "fatal: remote hung up unexpectedly",
            "Resolving deltas:  10% (1/10)",
        ],
        ["git", "clone", "x"],
    )
    assert "fatal: remote hung up unexpectedly" in msg
    assert "40%" not in msg


def test_clone_reports_progress(tmp_path: Path, git_src: Path):
    dest = tmp_path / "cloned"
    lines: list[str] = []
    ensure_clone(str(git_src), dest, on_progress=lines.append)
    assert (dest / ".git").exists()
    blob = "\n".join(lines).lower()
    assert "clone" in blob
    assert str(dest) in "\n".join(lines)


def test_run_disables_git_terminal_prompt(monkeypatch):
    import os

    import dev_yard.gitops as gitops_mod

    captured: dict = {}

    def fake_run(args, cwd=None, capture_output=None, text=None, env=None):
        captured["env"] = env
        captured["args"] = args

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(gitops_mod.subprocess, "run", fake_run)
    gitops_mod.run(["git", "status"])
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    # inherit the rest of the environment rather than replacing it
    assert captured["env"]["PATH"] == os.environ["PATH"]


def test_merge_abort_clears_conflicted_state(git_src: Path, tmp_path: Path):
    import subprocess

    from dev_yard.gitops import merge_abort, worktree_add

    (git_src / "f.txt").write_text("base\n")
    subprocess.check_call(["git", "add", "f.txt"], cwd=git_src)
    subprocess.check_call(["git", "commit", "-m", "base"], cwd=git_src)
    worktree_add(git_src, tmp_path / "wt", "req/AB", "main")

    # diverging edits on both sides
    (git_src / "f.txt").write_text("main-side\n")
    subprocess.check_call(["git", "commit", "-am", "main side"], cwd=git_src)
    worktree_add(git_src, tmp_path / "wt2", "req/AB-c", "req/AB")
    (tmp_path / "wt2" / "f.txt").write_text("child-side\n")
    subprocess.check_call(["git", "commit", "-am", "child side"], cwd=tmp_path / "wt2")
    (tmp_path / "wt" / "f.txt").write_text("parent-side\n")
    subprocess.check_call(["git", "commit", "-am", "parent side"], cwd=tmp_path / "wt")
    assert subprocess.call(["git", "merge", "req/AB-c"], cwd=tmp_path / "wt") != 0
    merge_abort(tmp_path / "wt")
    out = subprocess.check_output(["git", "status", "--porcelain"], cwd=tmp_path / "wt")
    assert b"UU" not in out


def test_commit_all_and_has_changes(git_src: Path, tmp_path: Path):
    from dev_yard.gitops import commit_all, has_changes, worktree_add

    wt = tmp_path / "wt"
    worktree_add(git_src, wt, "req/COMMIT-TEST", "main")
    assert not has_changes(wt)
    sha_clean = commit_all(wt, "nothing to commit")
    assert sha_clean is not None

    (wt / "new_file.txt").write_text("hello")
    assert has_changes(wt)
    sha_dirty = commit_all(wt, "feat: add new_file")
    assert sha_dirty is not None
    assert sha_dirty != sha_clean
    assert not has_changes(wt)
    log = run(["git", "log", "-n", "1", "--oneline"], cwd=wt)
    assert "feat: add new_file" in log


def test_push_worktree_to_remote(git_src: Path, tmp_path: Path):
    from dev_yard.gitops import push, worktree_add

    # Create a bare remote repository
    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.check_call(["git", "init", "--bare"], cwd=bare)

    # Add origin remote to git_src
    subprocess.check_call(["git", "remote", "add", "origin", str(bare)], cwd=git_src)

    # Create worktree
    wt = tmp_path / "wt_push"
    worktree_add(git_src, wt, "req/PUSH-1", "main")
    (wt / "feature.txt").write_text("pushed content")
    subprocess.check_call(["git", "add", "."], cwd=wt)
    subprocess.check_call(["git", "commit", "-m", "feat: push test"], cwd=wt)

    # Push to origin
    lines: list[str] = []
    push(wt, remote="origin", branch="req/PUSH-1", on_progress=lines.append)

    # Verify branch exists on remote
    remote_sha = run(["git", "rev-parse", "refs/heads/req/PUSH-1"], cwd=bare)
    local_sha = run(["git", "rev-parse", "HEAD"], cwd=wt)
    assert remote_sha == local_sha
    assert any("push" in line.lower() for line in lines)


