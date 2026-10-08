from __future__ import annotations

import codecs
import os
import re
import shutil
import signal
import subprocess
import tempfile
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


def run(
    args: list[str],
    cwd: Path | None = None,
    *,
    env: dict[str, str] | None = None,
) -> str:
    base = os.environ.copy()
    base["GIT_TERMINAL_PROMPT"] = "0"
    if env:
        base.update(env)
    env = base
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


def _subjects(repo: Path, tip: str, *log_args: str) -> list[tuple[str, str, str]]:
    """`(sha, parents, subject)` rows from `git log tip` plus extra log args."""
    try:
        text = run(
            ["git", "log", *log_args, "--format=%H%x1f%P%x1f%s", tip],
            cwd=repo,
        )
    except GitError:
        return []
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        sha, parents, subject = (line.split("\x1f", 2) + ["", ""])[:3]
        if sha:
            rows.append((sha, parents, subject))
    return rows


def _own_merges(repo: Path, tip: str, ticket_branch: str) -> list[tuple[str, str]]:
    """Merges of `ticket_branch` that are in `tip`'s history.

    Each row is `(merge_sha, first_parent)`. A merge *into* the ticket branch
    (`Merge branch 'freeze' into <ticket>`) is not one of these.
    """
    needle = f"Merge branch '{ticket_branch}'"
    rows = _subjects(
        repo,
        tip,
        "-F",
        "--merges",
        "--grep",
        needle,
    )
    found: list[tuple[str, str]] = []
    for sha, parents, subject in rows:
        if subject != needle and not subject.startswith(needle + " "):
            continue
        parent_shas = parents.split()
        if len(parent_shas) < 2:
            continue
        found.append((sha, parent_shas[0]))
    return found


def _own_direct_commits(repo: Path, tip: str, ticket_id: str) -> list[tuple[str, str]]:
    """Commits landed on the parent itself as `feat/fix(<id>)` or `merge: <id>`."""
    tid = re.escape(ticket_id)
    pattern = rf"^(feat|fix)\({tid}\): |^merge: {tid}$"
    subject_re = re.compile(rf"^(?:feat|fix)\({tid}\): |^merge: {tid}$")
    rows = _subjects(repo, tip, "-E", "--grep", pattern)
    found: list[tuple[str, str]] = []
    for sha, parents, subject in rows:
        if not subject_re.match(subject):
            continue
        parent_shas = parents.split()
        if parent_shas:
            found.append((sha, parent_shas[0]))
    return found


def _oldest_ancestor(repo: Path, candidates: list[str]) -> str | None:
    """The candidate that is an ancestor of every other, if they are linear."""
    origin: str | None = None
    for sha in candidates:
        if origin is None:
            origin = sha
            continue
        if is_ancestor(repo, sha, origin):
            origin = sha
        elif not is_ancestor(repo, origin, sha):
            return None
    return origin


def _patch_paths(diff: bytes) -> list[str]:
    paths: list[str] = []
    for raw in diff.splitlines():
        line = raw.decode("utf-8", "replace")
        if not line.startswith("diff --git "):
            continue
        marker = " b/"
        if marker not in line:
            continue
        path = line.split(marker, 1)[1]
        if path and path != "dev/null":
            paths.append(path)
    return paths


def _apply_commit(repo: Path, parent: str, commit: str, env: dict[str, str]) -> None:
    """Apply `commit`'s patch onto the index in `env`. Raises GitError."""
    diff = subprocess.run(
        ["git", "diff-tree", "-p", "--binary", "-M", parent, commit],
        cwd=repo,
        capture_output=True,
        env=env,
        timeout=_timeout(),
        check=False,
    )
    if diff.returncode != 0:
        raise GitError(redact(diff.stderr.decode("utf-8", "replace").strip() or "diff-tree"))
    if not diff.stdout.strip():
        return
    cached = subprocess.run(
        ["git", "apply", "--cached", "--whitespace=nowarn"],
        cwd=repo,
        input=diff.stdout,
        capture_output=True,
        env=env,
        timeout=_timeout(),
        check=False,
    )
    if cached.returncode == 0:
        return
    paths = _patch_paths(diff.stdout)
    if paths:
        checkout = subprocess.run(
            ["git", "checkout-index", "-f", "--", *paths],
            cwd=repo,
            capture_output=True,
            env=env,
            timeout=_timeout(),
            check=False,
        )
        if checkout.returncode != 0:
            raise GitError(
                redact(checkout.stderr.decode("utf-8", "replace").strip() or "checkout-index")
            )
    three = subprocess.run(
        ["git", "apply", "--3way", "--whitespace=nowarn"],
        cwd=repo,
        input=diff.stdout,
        capture_output=True,
        env=env,
        timeout=_timeout(),
        check=False,
    )
    if three.returncode != 0:
        detail = three.stderr.decode("utf-8", "replace").strip()
        raise GitError(redact(detail or "apply --3way"))
    add = subprocess.run(
        ["git", "add", "-A", "--", *paths] if paths else ["git", "add", "-A"],
        cwd=repo,
        capture_output=True,
        env=env,
        timeout=_timeout(),
        check=False,
    )
    if add.returncode != 0:
        raise GitError(redact(add.stderr.decode("utf-8", "replace").strip() or "git add"))


def _in_requirement(repo: Path, sha: str, not_before: str | None) -> bool:
    """True when `sha` landed after the requirement's recorded freeze."""
    if not not_before or not rev_parse(repo, not_before):
        return True
    if sha == not_before:
        return False
    return not is_ancestor(repo, sha, not_before)


def baseline_without_ticket(
    repo: Path,
    tip: str,
    ticket_branch: str,
    ticket_id: str,
    not_before: str | None = None,
) -> str | None:
    """Tree of `tip` with this ticket's already-merged work removed.

    Re-implementing a ticket resets its branch onto the freeze tip, which
    already contains earlier merges of the same ticket. Diffing against that
    tip hides those merges and makes a later attempt look like it only
    changed the last commit. Sibling commits that landed in between stay.

    `not_before` is the requirement's freeze sha. Older `feat(<id>)` commits
    from another effort on the same repo are left in the tree.

    Returns a commit sha (cached under `refs/dev-yard/baseline/`) or None when
    `tip` does not contain this ticket. The sha is not an ancestor of `tip`.
    """
    if not tip or not ticket_branch or not ticket_id:
        return None
    scope = not_before or "none"
    ref = f"refs/dev-yard/baseline/{ticket_branch}/{scope}/{tip}"
    cached = rev_parse(repo, ref)
    if cached:
        return cached
    merges = [
        row
        for row in _own_merges(repo, tip, ticket_branch)
        if _in_requirement(repo, row[0], not_before)
    ]
    directs = [
        row
        for row in _own_direct_commits(repo, tip, ticket_id)
        if _in_requirement(repo, row[0], not_before)
    ]
    if not merges and not directs:
        return None
    excluded: set[str] = {sha for sha, _parent in directs}
    for merge_sha, first in merges:
        excluded.add(merge_sha)
        try:
            introduced = run(["git", "rev-list", f"{first}..{merge_sha}"], cwd=repo)
        except GitError:
            return None
        excluded.update(introduced.split())
    origin = _oldest_ancestor(
        repo, [parent for _sha, parent in (*merges, *directs)]
    )
    if not origin or not is_ancestor(repo, origin, tip):
        return None
    try:
        replay = run(["git", "rev-list", "--reverse", f"{origin}..{tip}"], cwd=repo)
    except GitError:
        return None
    index_path: str | None = None
    work_path: str | None = None
    try:
        index_file = tempfile.NamedTemporaryFile(prefix="yard-baseline-", delete=False)
        index_file.close()
        index_path = index_file.name
        work_path = tempfile.mkdtemp(prefix="yard-baseline-")
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = index_path
        env["GIT_WORK_TREE"] = work_path
        env["GIT_TERMINAL_PROMPT"] = "0"
        run(["git", "read-tree", origin], cwd=repo, env=env)
        for sha in replay.split():
            if not sha or sha in excluded:
                continue
            try:
                parents = run(["git", "rev-list", "-1", "--parents", sha], cwd=repo).split()
            except GitError:
                return None
            parent_shas = parents[1:]
            if len(parent_shas) != 1:
                # Sibling merge commits carry no unique patch once their
                # branch commits are replayed. Conflict-only merge resolutions
                # are not reconstructed.
                continue
            _apply_commit(repo, parent_shas[0], sha, env)
        tree = run(["git", "write-tree"], cwd=repo, env=env)
        message = f"dev-yard baseline without {ticket_branch} at {tip}"
        sha = run(
            ["git", "commit-tree", tree, "-p", origin, "-m", message],
            cwd=repo,
            env=_commit_env(),
        )
    except GitError:
        return None
    finally:
        if index_path:
            try:
                os.unlink(index_path)
            except OSError:
                pass
        if work_path:
            shutil.rmtree(work_path, ignore_errors=True)
    try:
        run(["git", "update-ref", ref, sha], cwd=repo)
    except GitError:
        return sha
    return sha


def own_work_log(
    repo: Path,
    endpoint: str,
    ticket_branch: str,
    ticket_id: str,
    not_before: str | None = None,
) -> str:
    """Oneline log of this ticket's commits reachable from `endpoint`.

    Used when the diff base is a synthetic tree and `base..HEAD` would list
    every sibling commit since the ticket first branched.
    """
    if not endpoint or not ticket_branch or not ticket_id:
        return ""
    shas: set[str] = set()
    for merge_sha, first in _own_merges(repo, endpoint, ticket_branch):
        if not _in_requirement(repo, merge_sha, not_before):
            continue
        shas.add(merge_sha)
        try:
            introduced = run(["git", "rev-list", f"{first}..{merge_sha}"], cwd=repo)
        except GitError:
            continue
        shas.update(introduced.split())
    for sha, _parent in _own_direct_commits(repo, endpoint, ticket_id):
        if _in_requirement(repo, sha, not_before):
            shas.add(sha)
    freeze = ticket_branch[: -(len(ticket_id) + 1)]
    if freeze and rev_parse(repo, freeze):
        try:
            ahead = run(["git", "rev-list", endpoint, "--not", freeze], cwd=repo)
        except GitError:
            ahead = ""
        shas.update(ahead.split())
    shas.discard("")
    if not shas:
        return ""
    try:
        return run(
            ["git", "log", "--oneline", "--no-walk", "--date-order", *sorted(shas)],
            cwd=repo,
        )
    except GitError:
        return ""


def is_ancestor(worktree: Path, ancestor: str, descendant: str) -> bool:
    """True when `ancestor` is reachable from `descendant`; False otherwise/unknown."""
    try:
        run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=worktree)
        return True
    except GitError:
        return False


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


def checked_out_branch(worktree: Path | None) -> str | None:
    """Branch a worktree is on, or None for detached/missing/unknown."""
    if worktree is None or not (worktree / ".git").exists():
        return None
    try:
        name = current_branch(worktree)
    except GitError:
        return None
    return name if name and name != "HEAD" else None


def init_repo(path: Path) -> None:
    """Create an empty repository (used to bootstrap an offline import)."""
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q", str(path)])


def bundle_create(
    out: Path,
    refs: list[str],
    cwd: Path,
    *,
    exclude: str | None = None,
) -> None:
    """Write a git bundle of `refs`; `exclude` drops commits reachable from it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = ["git", "bundle", "create", str(out), *refs]
    if exclude:
        argv += ["--not", exclude]
    run(argv, cwd=cwd)


def bundle_list_heads(bundle: Path, cwd: Path) -> list[str]:
    """Refs contained in a bundle (`<sha> <ref>` lines), for a precise fetch."""
    out = run(["git", "bundle", "list-heads", str(bundle)], cwd=cwd)
    return [line.split()[-1] for line in out.splitlines() if line.strip()]


def fetch_bundle(
    source: Path,
    bundle: Path,
    refspecs: list[str],
    on_progress: Progress | None = None,
) -> None:
    """Fetch refs from a bundle file (used to import an exported requirement)."""
    if not refspecs:
        return
    argv = ["git", "fetch", "--no-tags", str(bundle), *refspecs]
    if on_progress is None:
        run(argv, cwd=source)
        return
    on_progress(f"git fetch {bundle} (cwd={source})")
    _run_progress(argv, on_progress, cwd=source)


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


def commit_merge(worktree: Path, message: str) -> str:
    """Finish an in-progress merge, even when the resolution matches HEAD.

    `commit_all` treats an empty porcelain as "nothing to do" and returns the
    current HEAD. Keeping the ticket side of a conflict is that case: the index
    matches HEAD while `MERGE_HEAD` is still set, and skipping the commit leaves
    the merge open.
    """
    if not (worktree / ".git").exists():
        raise GitError(f"{worktree} is not a git worktree")
    if not has_merge_head(worktree):
        raise GitError(f"{worktree} has no merge in progress")
    pending = unmerged_files(worktree)
    if pending:
        raise GitError("unmerged paths: " + ", ".join(pending))
    proc = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=worktree,
        capture_output=True,
        text=True,
        env=_commit_env(),
        timeout=_timeout(),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitError(detail or "git commit failed")
    if has_merge_head(worktree):
        raise GitError("merge is still in progress after commit")
    return head_sha(worktree)


def conflict_marker_paths(worktree: Path) -> list[str]:
    """Tracked paths that still contain a whole conflict-marker block.

    Both markers must sit in the same file. A lone `<<<<<<< ` line (a diff
    quoted in docs, a test fixture, a changelog) is not a conflict, and callers
    rewind a merge commit when this reports anything, so a false positive would
    drop a real resolution.
    """
    if not (worktree / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            [
                "git",
                "grep",
                "-l",
                "--all-match",
                "-e",
                "^<<<<<<< ",
                "-e",
                "^>>>>>>> ",
                "--",
                ".",
            ],
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=_timeout(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode not in (0, 1):
        return []
    return [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]


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
    # Working tree vs base: committed since base plus unstaged files.
    # A folded baseline is a side commit, not an ancestor. `base..HEAD` would
    # then list every commit on HEAD that the side commit does not contain.
    linear = is_ancestor(worktree, base, "HEAD")
    log = ""
    if linear:
        log = run(["git", "log", "--oneline", f"{base}..HEAD"], cwd=worktree)
    diff = run(["git", "diff", base], cwd=worktree)
    untracked = _untracked_diff(worktree)
    if linear and not log.strip() and not diff.strip() and not untracked.strip():
        return f"(no changes vs {base})"
    if not linear and not diff.strip() and not untracked.strip():
        return f"(no changes vs {base})"
    if linear:
        parts = [f"git log {base}..HEAD:\n{log or '(no commits)'}"]
    else:
        parts = [
            "git log: (baseline folds this ticket's earlier merges; "
            "the diff below is the net change)"
        ]
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


def _git_config_global(key: str) -> str:
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get", key],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def global_git_identity() -> tuple[str, str] | None:
    """Operator name and email from `git config --global`, if both are set."""
    name = _git_config_global("user.name")
    email = _git_config_global("user.email")
    if name and email:
        return name, email
    return None


def _commit_env() -> dict[str, str]:
    """Identity for requirement commits.

    An explicit ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` pair wins (tests and CI).
    Otherwise use the operator's global git account. ``dev-yard`` is only the
    last resort so a machine with no identity can still commit.
    """
    env = os.environ.copy()
    author_ready = bool(env.get("GIT_AUTHOR_NAME") and env.get("GIT_AUTHOR_EMAIL"))
    committer_ready = bool(env.get("GIT_COMMITTER_NAME") and env.get("GIT_COMMITTER_EMAIL"))
    if author_ready and committer_ready:
        return env
    found = global_git_identity()
    name, email = found if found else ("dev-yard", "dev-yard@local")
    env.setdefault("GIT_AUTHOR_NAME", name)
    env.setdefault("GIT_AUTHOR_EMAIL", email)
    env.setdefault("GIT_COMMITTER_NAME", name)
    env.setdefault("GIT_COMMITTER_EMAIL", email)
    return env


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
        env = _commit_env()
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

