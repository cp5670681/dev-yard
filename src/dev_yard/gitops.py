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
        fetch(dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", url, str(dest)])


def fetch(source: Path) -> None:
    run(["git", "fetch", "--all", "--prune"], cwd=source)


def start_point(source: Path, default_base: str) -> str:
    try:
        run(["git", "rev-parse", "--verify", f"origin/{default_base}"], cwd=source)
        return f"origin/{default_base}"
    except GitError:
        run(["git", "rev-parse", "--verify", default_base], cwd=source)
        return default_base


def _is_git_worktree(path: Path) -> bool:
    return path.exists() and (path / ".git").exists()


def worktree_add(
    source: Path,
    path: Path,
    branch: str,
    start_point: str,
    *,
    reset_existing: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _is_git_worktree(path):
            if reset_existing:
                run(["git", "reset", "--hard", start_point], cwd=path)
            return
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
        else:
            raise GitError(f"{path} exists and is not a git worktree")
    existing = run(["git", "branch", "--list", branch], cwd=source)
    if existing:
        if reset_existing:
            run(["git", "branch", "-f", branch, start_point], cwd=source)
        run(["git", "worktree", "add", str(path), branch], cwd=source)
    else:
        run(["git", "worktree", "add", "-b", branch, str(path), start_point], cwd=source)


def worktree_remove(source: Path, path: Path) -> None:
    try:
        run(["git", "worktree", "remove", "--force", str(path)], cwd=source)
    except GitError:
        try:
            run(["git", "worktree", "prune"], cwd=source)
        except GitError:
            pass


def branch_delete(source: Path, branch: str) -> None:
    try:
        run(["git", "branch", "-D", branch], cwd=source)
    except GitError:
        pass


def merge_into(worktree: Path, branch: str) -> None:
    run(["git", "merge", "--no-edit", branch], cwd=worktree)


def current_branch(worktree: Path) -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree)


def checkout_default_base(source: Path, default_base: str) -> None:
    """Put a managed clone on default_base; fast-forward to origin when that ref exists."""
    fetch(source)
    run(["git", "checkout", default_base], cwd=source)
    try:
        run(["git", "rev-parse", "--verify", f"origin/{default_base}"], cwd=source)
    except GitError:
        return
    run(["git", "merge", "--ff-only", f"origin/{default_base}"], cwd=source)


def diff_against(worktree: Path, base: str) -> str:
    log = run(["git", "log", "--oneline", f"{base}..HEAD"], cwd=worktree)
    diff = run(["git", "diff", f"{base}...HEAD"], cwd=worktree)
    if not diff.strip():
        return f"(no changes vs {base})"
    return f"git log {base}..HEAD:\n{log}\n\ngit diff {base}...HEAD:\n{diff}"
