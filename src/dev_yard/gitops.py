from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

Progress = Callable[[str], None]


class GitError(RuntimeError):
    pass


_PROGRESS_RE = re.compile(
    r"^(?:remote: )?(?:"
    r"Counting objects|Compressing objects|Receiving objects|Resolving deltas|"
    r"Enumerating objects|Writing objects|Total \d+"
    r")\b",
    re.I,
)


def is_git_progress(line: str) -> bool:
    return bool(_PROGRESS_RE.match(line.strip()))


def git_failure_message(lines: list[str], args: list[str]) -> str:
    useful = [ln for ln in lines if ln.strip() and not is_git_progress(ln)]
    blob = "\n".join(useful if useful else lines).strip()
    return blob or " ".join(args)


def drain_git_output(buf: bytes, on_progress: Progress) -> bytes:
    """Emit CR/LF-delimited git progress lines; return an incomplete tail."""
    while True:
        i_n = buf.find(b"\n")
        i_r = buf.find(b"\r")
        cuts = [i for i in (i_n, i_r) if i >= 0]
        if not cuts:
            return buf
        i = min(cuts)
        line = buf[:i].decode("utf-8", "replace").strip()
        buf = buf[i + 1 :]
        if line:
            on_progress(line)


def run(args: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise GitError(r.stderr.strip() or r.stdout.strip() or " ".join(args))
    return r.stdout.strip()


def _run_progress(args: list[str], on_progress: Progress, cwd: Path | None = None) -> None:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        env=env,
    )
    assert proc.stdout is not None
    buf = b""
    lines: list[str] = []

    def captured(line: str) -> None:
        lines.append(line)
        on_progress(line)

    while True:
        chunk = proc.stdout.read(256)
        if not chunk:
            break
        buf = drain_git_output(buf + chunk, captured)
    if buf.strip():
        captured(buf.decode("utf-8", "replace").strip())
    code = proc.wait()
    if code != 0:
        raise GitError(git_failure_message(lines, args))


def ensure_clone(url: str, dest: Path, on_progress: Progress | None = None) -> None:
    if dest.exists() and (dest / ".git").exists():
        fetch(dest, on_progress=on_progress)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = ["git", "clone", "--progress", url, str(dest)]
    if on_progress is None:
        run(["git", "clone", url, str(dest)])
        return
    on_progress(f"git clone {url} -> {dest}")
    _run_progress(argv, on_progress)


def fetch(source: Path, on_progress: Progress | None = None) -> None:
    if on_progress is None:
        run(["git", "fetch", "--all", "--prune"], cwd=source)
        return
    on_progress(f"git fetch {source}")
    _run_progress(["git", "fetch", "--all", "--prune", "--progress"], on_progress, cwd=source)


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


def _untracked_diff(worktree: Path) -> str:
    names = run(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree
    )
    chunks: list[str] = []
    for rel in names.splitlines():
        path = worktree / rel
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(body) > 100_000:
            body = body[:100_000] + "\n…(truncated)"
        chunks.append(f"--- /dev/null\n+++ b/{rel}\n{body}")
    return "\n".join(chunks)


def diff_against(worktree: Path, base: str) -> str:
    log = run(["git", "log", "--oneline", f"{base}..HEAD"], cwd=worktree)
    # Working tree vs base: committed since base plus unstaged files.
    diff = run(["git", "diff", base], cwd=worktree)
    untracked = _untracked_diff(worktree)
    if not log.strip() and not diff.strip() and not untracked.strip():
        return f"(no changes vs {base})"
    parts = [f"git log {base}..HEAD:\n{log or '(no commits)'}"]
    if diff.strip():
        parts.append(f"git diff {base}:\n{diff}")
    if untracked.strip():
        parts.append(f"untracked:\n{untracked}")
    return "\n\n".join(parts)
