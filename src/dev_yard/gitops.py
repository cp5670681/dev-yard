from __future__ import annotations

import codecs
import os
import re
import signal
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

Progress = Callable[[str], None]

# Network git ops (clone/fetch/push) hang forever without a deadline; a remote
# that stops answering would otherwise wedge the CLI/agent.
DEFAULT_TIMEOUT = 600.0

# Userinfo in a URL (https://user:token@host/...). Masked before any echo.
_CRED_URL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")


class GitError(RuntimeError):
    pass


def _timeout() -> float:
    try:
        return float(os.environ.get("YARD_GIT_TIMEOUT", "") or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT


def redact(text: str) -> str:
    """Mask credentials embedded in URLs before logging or surfacing errors."""
    return _CRED_URL_RE.sub(r"\1***@", text or "")


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
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
    blob = redact("\n".join(useful if useful else lines).strip())
    return blob or redact(" ".join(args))


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
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        r = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, env=env, timeout=_timeout()
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(
            f"git command timed out after {int(_timeout())}s: {redact(' '.join(args))}"
        ) from e
    if r.returncode != 0:
        raise GitError(redact(r.stderr.strip() or r.stdout.strip() or " ".join(args)))
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
        start_new_session=True,
    )
    assert proc.stdout is not None
    buf = b""
    lines: list[str] = []
    timed_out = False

    def captured(line: str) -> None:
        lines.append(line)
        on_progress(line)

    def _on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        _kill_group(proc)

    timer = threading.Timer(_timeout(), _on_timeout)
    timer.start()
    try:
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buf = drain_git_output(buf + chunk, captured)
        if buf.strip():
            captured(buf.decode("utf-8", "replace").strip())
        code = proc.wait()
    finally:
        timer.cancel()
    if timed_out:
        raise GitError(
            f"git command timed out after {int(_timeout())}s: {redact(' '.join(args))}"
        )
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
    on_progress(f"git clone {redact(url)} -> {dest}")
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
        # Path is new; leftover refs must not silently reuse stale commits.
        try:
            run(["git", "worktree", "prune"], cwd=source)
        except GitError:
            pass
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


def worktree_prune(source: Path) -> None:
    try:
        run(["git", "worktree", "prune"], cwd=source)
    except GitError:
        pass


def detached_worktree(source: Path, path: Path, ref: str) -> None:
    """Recreate `path` as a clean detached worktree at `ref`.

    Any leftover worktree/dir from a previous failed integration is removed
    first, so `git worktree add` cannot fail with "already exists".
    """
    if path.exists():
        if _is_git_worktree(path):
            worktree_remove(source, path)
        else:
            try:
                next(path.iterdir())
            except StopIteration:
                path.rmdir()
            else:
                raise GitError(f"{path} exists and is not a git worktree")
    worktree_prune(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "--detach", str(path), ref], cwd=source)


def branch_delete(source: Path, branch: str) -> None:
    try:
        run(["git", "branch", "-D", branch], cwd=source)
    except GitError:
        pass


SYNC_STRATEGIES = ("ff-only", "merge", "rebase")


def merge_into(worktree: Path, branch: str) -> None:
    run(["git", "merge", "--no-edit", branch], cwd=worktree)


def merge_abort(worktree: Path) -> None:
    """Best-effort cleanup after a failed merge; never raises."""
    try:
        run(["git", "merge", "--abort"], cwd=worktree)
    except GitError:
        pass


def rebase_abort(worktree: Path) -> None:
    """Best-effort cleanup after a failed rebase; never raises."""
    try:
        run(["git", "rebase", "--abort"], cwd=worktree)
    except GitError:
        pass


def head_sha(worktree: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], cwd=worktree)


def merge_base(worktree: Path, ref: str) -> str | None:
    """Divergence point of `worktree` HEAD and `ref`; None if unrelated/unknown."""
    try:
        return run(["git", "merge-base", "HEAD", ref], cwd=worktree)
    except GitError:
        return None


def rev_parse(path: Path, ref: str) -> str | None:
    """Resolve a ref to a commit sha; None when unknown to this repo."""
    try:
        return run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=path)
    except GitError:
        return None


def freeze_base(worktree: Path, default_base: str, saved: str | None = None) -> str:
    """Diff base at a requirement's freeze point.

    Diffing a long-lived branch against the live `origin/<base>` mixes in
    upstream commits that landed after the requirement forked, so a stale
    branch looks like it reverted or introduced them. The recorded freeze sha
    (written by `req_freeze`) wins; otherwise fall back to the fork point.
    """
    if isinstance(saved, str) and saved.strip() and rev_parse(worktree, saved):
        return saved
    base_ref = start_point(worktree, default_base)
    return merge_base(worktree, base_ref) or base_ref


def changed_files(worktree: Path, base: str) -> set[str]:
    """Tracked files whose content differs from `base` (committed + unstaged)."""
    try:
        out = run(["git", "diff", "--name-only", base], cwd=worktree)
    except GitError:
        return set()
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def untracked_files(worktree: Path) -> set[str]:
    """Files present in the working tree but not tracked by git."""
    try:
        out = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree)
    except GitError:
        return set()
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def first_parent(worktree: Path, sha: str) -> str | None:
    """The commit a merge/fast-forward landed on top of; None for root/unknown."""
    try:
        return run(["git", "rev-parse", "--verify", f"{sha}^"], cwd=worktree)
    except GitError:
        return None


def unmerged_files(worktree: Path) -> list[str]:
    """Paths left in a conflicted merge/rebase state; empty when clean."""
    try:
        out = run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=worktree)
    except GitError:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def integrate_onto(worktree: Path, ref: str, strategy: str = "ff-only") -> str:
    """Fast-forward, merge, or rebase `worktree` onto `ref`. Returns new HEAD."""
    if strategy not in SYNC_STRATEGIES:
        raise ValueError(f"unknown sync strategy {strategy!r}")
    if has_changes(worktree):
        raise GitError(f"{worktree} has uncommitted changes")
    try:
        if strategy == "ff-only":
            run(["git", "merge", "--ff-only", ref], cwd=worktree)
        elif strategy == "merge":
            run(["git", "merge", "--no-edit", ref], cwd=worktree)
        else:
            run(["git", "rebase", ref], cwd=worktree)
    except GitError:
        if strategy == "merge":
            merge_abort(worktree)
        elif strategy == "rebase":
            rebase_abort(worktree)
        raise
    return head_sha(worktree)


def current_branch(worktree: Path) -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree)


def assert_branch_name(branch: str) -> None:
    """Raise ValueError unless `branch` is a legal local branch ref."""
    name = (branch or "").strip()
    if not name or name == "HEAD":
        raise ValueError("invalid git branch name")
    try:
        run(["git", "check-ref-format", f"refs/heads/{name}"])
    except GitError as e:
        raise ValueError(f"invalid git branch name {name!r}") from e


def push(
    worktree: Path,
    remote: str = "origin",
    branch: str | None = None,
    set_upstream: bool = True,
    force: bool = False,
    on_progress: Progress | None = None,
) -> str:
    target_branch = branch or current_branch(worktree)
    argv = ["git", "push"]
    if set_upstream:
        argv.append("-u")
    if force:
        argv.append("--force-with-lease")
    if on_progress is not None:
        argv.append("--progress")
    argv.extend([remote, target_branch])

    if on_progress is None:
        return run(argv, cwd=worktree)
    on_progress(f"git push {remote} {target_branch} (cwd={worktree})")
    _run_progress(argv, on_progress, cwd=worktree)
    return f"pushed {target_branch} to {remote}"


def fetch_branch(
    source: Path, remote: str, branch: str, on_progress: Progress | None = None
) -> None:
    """Fetch one remote branch, updating `refs/remotes/<remote>/<branch>`."""
    argv = ["git", "fetch", "--prune"]
    if on_progress is not None:
        argv.append("--progress")
    argv.extend([remote, branch])
    if on_progress is None:
        run(argv, cwd=source)
        return
    on_progress(f"git fetch {remote} {branch}")
    _run_progress(argv, on_progress, cwd=source)


def push_ref(
    worktree: Path,
    remote: str,
    src: str,
    dst: str,
    on_progress: Progress | None = None,
) -> None:
    """Push `<src>:<dst>` (e.g. detached `HEAD:refs/heads/<branch>`), non-force."""
    argv = ["git", "push"]
    if on_progress is not None:
        argv.append("--progress")
    argv.extend([remote, f"{src}:{dst}"])
    if on_progress is None:
        run(argv, cwd=worktree)
        return
    on_progress(f"git push {remote} {src}:{dst} (cwd={worktree})")
    _run_progress(argv, on_progress, cwd=worktree)


def add_all(worktree: Path) -> None:
    run(["git", "add", "-A"], cwd=worktree)


def has_merge_head(worktree: Path) -> bool:
    return rev_parse(worktree, "MERGE_HEAD") is not None


# `<<<<<<<` / `>>>>>>>` are unambiguous; `=======` alone is a setext underline.
_MARKER_BEGIN_RE = re.compile(r"^(?:<{7}|>{7})(?:\s|$)")
_MARKER_MID_PREFIX = "======="


def _scan_text_conflict_markers(text: str) -> list[str]:
    lines = text.splitlines()
    strong = [ln for ln in lines if _MARKER_BEGIN_RE.match(ln)]
    if not strong:
        return []
    return strong + [ln for ln in lines if ln.startswith(_MARKER_MID_PREFIX)]


def _changed_files(argv: list[str], worktree: Path) -> list[str]:
    try:
        out = run(argv, cwd=worktree)
    except GitError:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _scan_worktree_files(worktree: Path, files: list[str]) -> list[str]:
    out: list[str] = []
    for rel in files:
        try:
            text = (worktree / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.extend(f"{rel}: {marker}" for marker in _scan_text_conflict_markers(text))
    return out


def check_conflict_markers(worktree: Path) -> list[str]:
    """Leftover conflict markers in the *staged* changes.

    Deliberately does not trust `git diff --check`: git flags any added line
    whose first 7 chars are `<`/`=`/`>` followed by EOL/space, which
    false-positives on Markdown/RST setext underlines (`=======`). We require an
    unambiguous `<<<<<<<`/`>>>>>>>` line before treating a file as conflicted;
    a lone `=======` (setext) is ignored.
    """
    files = _changed_files(["git", "diff", "--cached", "--name-only"], worktree)
    return _scan_worktree_files(worktree, files)


def check_commit_conflict_markers(worktree: Path, rev: str = "HEAD") -> list[str]:
    """Leftover conflict markers introduced by commit `rev` (setext-safe)."""
    files = _changed_files(["git", "diff", "--name-only", f"{rev}^", rev], worktree)
    return _scan_worktree_files(worktree, files)



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
        rel = rel.strip()
        if not rel:
            continue
        path = worktree / rel
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(body) > 100_000:
            body = body[:100_000] + "\n…(truncated)"
        body_lines = body.splitlines()
        line_count = len(body_lines)
        diff_lines = [
            f"diff --git a/{rel} b/{rel}",
            "new file mode 100644",
            "--- /dev/null",
            f"+++ b/{rel}",
            f"@@ -0,0 +1,{line_count} @@",
        ]
        for bl in body_lines:
            diff_lines.append(f"+{bl}")
        chunks.append("\n".join(diff_lines))
    return "\n\n".join(chunks)


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


def has_changes(worktree: Path) -> bool:
    try:
        status = run(["git", "status", "--porcelain"], cwd=worktree)
        return bool(status.strip())
    except GitError:
        return False


_PORCELAIN_LINE = re.compile(r"^\s*([A-Z?!]{1,2})\s+(.+)$")


def parse_porcelain_line(line: str) -> tuple[str, str] | None:
    """`XY path` → (status, path), decoding git's C-quoted paths.

    `gitops.run` strips the leading space of the first line, so the status
    columns are matched by regex rather than by fixed offset.
    """
    m = _PORCELAIN_LINE.match(line)
    if not m:
        return None
    status = m.group(1).strip() or "??"
    path = m.group(2)
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    path = path.strip()
    if path.startswith('"') and path.endswith('"') and len(path) >= 2:
        try:
            decoded, _ = codecs.escape_decode(path[1:-1].encode("utf-8"))
            path = decoded.decode("utf-8", "surrogateescape")
        except (ValueError, UnicodeDecodeError):
            path = path[1:-1]
    if not path:
        return None
    return status, path


def porcelain_paths(worktree: Path) -> set[str]:
    """Changed/untracked paths in a worktree; empty set if git status fails."""
    try:
        text = run(["git", "status", "--porcelain", "-uall"], cwd=worktree)
    except GitError:
        return set()
    out: set[str] = set()
    for line in text.splitlines():
        entry = parse_porcelain_line(line)
        if entry is not None:
            out.add(entry[1])
    return out


def commit_all(worktree: Path, message: str) -> str | None:
    """Stage all changes and commit if working tree is dirty. Returns HEAD SHA."""
    try:
        if not (worktree / ".git").exists():
            return None
        if not has_changes(worktree):
            return run(["git", "rev-parse", "HEAD"], cwd=worktree)
        run(["git", "add", "-A"], cwd=worktree)
        staged = run(["git", "diff", "--cached", "--name-only"], cwd=worktree)
        if not staged.strip():
            return run(["git", "rev-parse", "HEAD"], cwd=worktree)
        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "dev-yard")
        env.setdefault("GIT_AUTHOR_EMAIL", "dev-yard@local")
        env.setdefault("GIT_COMMITTER_NAME", "dev-yard")
        env.setdefault("GIT_COMMITTER_EMAIL", "dev-yard@local")
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=worktree,
            capture_output=True,
            text=True,
            env=env,
            timeout=_timeout(),
            check=True,
        )
        return run(["git", "rev-parse", "HEAD"], cwd=worktree)
    except (GitError, subprocess.SubprocessError):
        return None

