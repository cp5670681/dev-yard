from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise GitError(r.stderr.strip() or r.stdout.strip() or " ".join(args))
    return r.stdout.strip()


def ensure_clone(url: str, dest: Path) -> None:
    if dest.exists() and (dest / ".git").exists():
        run(["git", "fetch", "--all", "--prune"], cwd=dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", url, str(dest)])


def fetch(source: Path) -> None:
    try:
        run(["git", "fetch", "--all", "--prune"], cwd=source)
    except GitError:
        pass


def start_point(source: Path, default_base: str) -> str:
    try:
        run(["git", "rev-parse", "--verify", f"origin/{default_base}"], cwd=source)
        return f"origin/{default_base}"
    except GitError:
        run(["git", "rev-parse", "--verify", default_base], cwd=source)
        return default_base


def worktree_add(source: Path, path: Path, branch: str, start_point: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    existing = run(["git", "branch", "--list", branch], cwd=source)
    if existing:
        run(["git", "worktree", "add", str(path), branch], cwd=source)
    else:
        run(["git", "worktree", "add", "-b", branch, str(path), start_point], cwd=source)


def worktree_remove(source: Path, path: Path) -> None:
    if not path.exists():
        return
    run(["git", "worktree", "remove", "--force", str(path)], cwd=source)


def merge_into(worktree: Path, branch: str) -> None:
    run(["git", "merge", "--no-edit", branch], cwd=worktree)


def current_branch(worktree: Path) -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree)
