"""Host-side QA helpers: login, setup/cleanup, result contract, worktree revert."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from dev_yard import gitops, paths
from dev_yard.qa_config import QaAccount, QaConfig, TestRejected, default_state_file
from dev_yard.qa_schedule import CaseJob, normalize_status

_ASSERT_TYPES = {"ui", "net", "db"}
_USER_SELECTORS = (
    'input[name="username"]',
    'input[name="user"]',
    'input[name="login"]',
    'input[autocomplete="username"]',
    'input[type="email"]',
    "getByLabel('用户名')",
    "getByPlaceholder('用户名')",
    "getByPlaceholder('Username')",
)
_PASS_SELECTORS = (
    'input[name="password"]',
    'input[type="password"]',
    "getByLabel('密码')",
    "getByPlaceholder('密码')",
    "getByPlaceholder('Password')",
)
_SUBMIT_SELECTORS = (
    'button[type="submit"]',
    "getByRole('button', { name: '登录' })",
    "getByRole('button', { name: 'Log in' })",
    "getByRole('button', { name: 'Sign in' })",
)


def state_path(root: Path, cfg: QaConfig, acct: QaAccount) -> Path:
    rel = acct.state_file or default_state_file(cfg.active_env, acct.name)
    path = Path(rel)
    return path if path.is_absolute() else root / path


def _which_playwright() -> str:
    binary = shutil.which("playwright-cli")
    if not binary:
        raise TestRejected("playwright-cli not found; cannot load or save auth state")
    return binary


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    label: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """subprocess.run that turns timeouts/OS errors into TestRejected.

    `label` is used in error messages instead of the argv so a command carrying
    a credential on its command line is never echoed back.
    """
    what = label or cmd[0]
    try:
        return subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise TestRejected(f"{what} timed out after {timeout}s") from e
    except OSError as e:
        raise TestRejected(f"cannot run {what}: {e}") from e


def _cli(
    binary: str,
    cfg: QaConfig,
    session: str,
    args: list[str],
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    cmd = [binary, "--browser", cfg.browser.channel, f"-s={session}", *args]
    label = f"playwright-cli {args[0]}" if args else "playwright-cli"
    return _run(cmd, cwd=cwd, timeout=timeout, label=label)


def ensure_auth(
    root: Path,
    cfg: QaConfig,
    names: list[str] | None = None,
    on_log: Any | None = None,
) -> dict[str, str]:
    """Load a saved session, or log in once on the host when the file is missing.

    Returns `{account: error}` for accounts whose session could not be set up.
    Auth is per-account best effort: one bad account must not abort the whole
    run, it only blocks the cases that actually use it.
    """
    env = cfg.env
    if names is None:
        names = [env.auth_default] if env.auth_default else []
    failures: dict[str, str] = {}
    binary: str | None = None
    for name in names:
        acct = env.accounts.get(name)
        if acct is None:
            continue
        try:
            binary = _ensure_one(root, cfg, name, acct, binary, on_log)
        except TestRejected as e:
            failures[name] = str(e)
            if on_log is not None:
                on_log(f"auth for {name} failed: {e}\n")
    return failures


def _ensure_one(
    root: Path,
    cfg: QaConfig,
    name: str,
    acct: QaAccount,
    binary: str | None,
    on_log: Any | None,
) -> str:
    state = state_path(root, cfg, acct)
    state.parent.mkdir(parents=True, exist_ok=True)
    has_creds = bool(acct.username and acct.password)
    if state.is_file():
        if binary is None:
            binary = _which_playwright()
        if on_log is not None:
            on_log(f"$ playwright-cli state-load {state}")
        r = _cli(binary, cfg, "qap-preload", ["state-load", str(state)], root)
        if r.returncode == 0:
            _cli(binary, cfg, "qap-preload", ["close"], root)
            return binary
        # Drop the broken session before falling back to a fresh login.
        _cli(binary, cfg, "qap-preload", ["close"], root)
        if not has_creds:
            err = (r.stderr or r.stdout or "").strip() or str(r.returncode)
            raise TestRejected(f"auth state-load failed for account {name!r}: {err}")
    elif not has_creds:
        raise TestRejected(
            f"missing auth state_file {state} for account {name!r}; "
            "put username/password in qa.yaml or log in once"
        )
    if binary is None:
        binary = _which_playwright()
    _host_login(root, cfg, acct, state, binary, on_log)
    _cli(binary, cfg, "qap-preload", ["close"], root)
    return binary


def _host_login(
    root: Path,
    cfg: QaConfig,
    acct: QaAccount,
    state: Path,
    binary: str,
    on_log: Any | None,
) -> None:
    """Best-effort form login. SSO with extra MFA still needs a saved session."""
    session = "qap-preload"
    if on_log is not None:
        on_log(f"host login {acct.name} → {state}")
    opened = _cli(
        binary,
        cfg,
        session,
        ["open", cfg.env.base_url],
        root,
        timeout=180,
    )
    if opened.returncode != 0:
        err = (opened.stderr or opened.stdout or "").strip() or str(opened.returncode)
        raise TestRejected(f"auth open failed for account {acct.name!r}: {err}")
    user_ok = _first_ok(
        binary, cfg, session, root, "fill", _USER_SELECTORS, acct.username
    )
    pass_ok = _first_ok(
        binary, cfg, session, root, "fill", _PASS_SELECTORS, acct.password
    )
    if not (user_ok and pass_ok):
        raise TestRejected(
            f"could not fill login form for account {acct.name!r}; "
            "save a session with playwright-cli state-save first"
        )
    if not _first_ok(binary, cfg, session, root, "click", _SUBMIT_SELECTORS):
        raise TestRejected(
            f"could not submit login form for account {acct.name!r}"
        )
    saved = _cli(binary, cfg, session, ["state-save", str(state)], root)
    if saved.returncode != 0 or not state.is_file():
        err = (saved.stderr or saved.stdout or "").strip() or str(saved.returncode)
        raise TestRejected(f"auth state-save failed for account {acct.name!r}: {err}")


def _first_ok(
    binary: str,
    cfg: QaConfig,
    session: str,
    cwd: Path,
    action: str,
    selectors: tuple[str, ...],
    value: str | None = None,
) -> bool:
    for sel in selectors:
        args = [action, sel] if value is None else [action, sel, value]
        r = _cli(binary, cfg, session, args, cwd, timeout=30)
        if r.returncode == 0:
            return True
    return False


def replay_path(job: CaseJob) -> Path | None:
    if not job.path:
        return None
    path = Path(job.path).with_suffix(".replay.sh")
    return path if path.is_file() else None


def run_case_script(
    root: Path,
    jira: str,
    cfg: QaConfig,
    job: CaseJob,
    kind: str,
    *,
    on_log: Any | None = None,
) -> str:
    """Run setup or cleanup. Reverts worktree files the script created."""
    name = job.setup if kind == "setup" else job.cleanup
    if not name:
        return ""
    case_dir = Path(job.path).parent if job.path else None
    if case_dir is None:
        raise TestRejected(f"{job.id} has {kind} {name!r} but no case path")
    script = case_dir / name
    if not script.is_file():
        raise TestRejected(f"{job.id} {kind} file missing: {script}")
    wt = paths.req_worktree(root, jira, job.repo) if job.repo else None
    started = time.time()
    before = _porcelain_paths(wt) if wt and wt.is_dir() else set()
    try:
        if script.suffix.lower() == ".sql":
            return _run_sql(cfg, script, on_log)
        return _run_runner(cfg, wt, script, on_log)
    finally:
        if wt and wt.is_dir():
            _revert_new_paths(wt, before, window=(started, time.time()))


def _run_sql(cfg: QaConfig, script: Path, on_log: Any | None) -> str:
    if not cfg.env.db_url:
        raise TestRejected("qa.yaml has no db.url; cannot run .sql setup/cleanup")
    binary = shutil.which("usql")
    if not binary:
        raise TestRejected("usql not found; cannot run .sql setup/cleanup")
    cmd = [binary, cfg.env.db_url, "-f", str(script)]
    if on_log is not None:
        on_log(f"$ usql <db.url> -f {script}")
    r = _run(cmd, timeout=300, label=f"usql {script.name}")
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or str(r.returncode)
        raise TestRejected(f"usql {script.name} failed: {err}")
    return (r.stdout or "").strip()


def _run_runner(
    cfg: QaConfig, worktree: Path | None, script: Path, on_log: Any | None
) -> str:
    runner = cfg.env.script_runner
    if not runner:
        raise TestRejected(
            f"non-sql script {script.name} needs qa.yaml script.runner"
        )
    if worktree is None or not worktree.is_dir():
        raise TestRejected(
            f"cannot run {script.name}: freeze worktree missing for this case"
        )
    if not cfg.env.db_url:
        raise TestRejected(
            "script.runner needs qa.yaml db.url so it talks to the same DB as the browser, "
            "not the worktree's local database.yml"
        )
    env = os.environ.copy()
    env["DATABASE_URL"] = cfg.env.db_url
    cmd = [*runner.split(), str(script)]
    if on_log is not None:
        on_log(f"$ (cd {worktree}) {runner} {script}")
    r = _run(
        cmd,
        cwd=worktree,
        env=env,
        timeout=300,
        label=f"{runner} {script.name}",
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or str(r.returncode)
        raise TestRejected(f"{runner} {script.name} failed: {err}")
    return (r.stdout or "").strip()


def _porcelain_paths(worktree: Path) -> set[str]:
    try:
        text = gitops.run(["git", "status", "--porcelain", "-uall"], cwd=worktree)
    except gitops.GitError:
        return set()
    out: set[str] = set()
    for ln in text.splitlines():
        path = ln[3:] if len(ln) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path:
            out.add(path)
    return out


def _revert_new_paths(
    worktree: Path, before: set[str], *, window: tuple[float, float] | None = None
) -> None:
    after = _porcelain_paths(worktree)
    for rel in sorted(after - before):
        target = worktree / rel
        if window is not None and not _created_in_window(target, window):
            # Not ours (or touched after the script finished): leave it for the
            # run-level mutation gate instead of deleting another case's work.
            continue
        try:
            if target.is_file() or target.is_symlink():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
        except OSError:
            try:
                gitops.run(["git", "checkout", "--", rel], cwd=worktree)
            except gitops.GitError:
                pass
    # Drop empty tmp dirs the script left behind.
    tmp = worktree / "tmp"
    if tmp.is_dir() and "tmp" not in before:
        try:
            next(tmp.iterdir())
        except StopIteration:
            tmp.rmdir()
        except OSError:
            pass


def _created_in_window(target: Path, window: tuple[float, float]) -> bool:
    start, end = window
    try:
        mtime = target.lstat().st_mtime
    except OSError:
        return False
    return start - 1.0 <= mtime <= end + 1.0


def normalize_case_result(
    data: dict[str, Any], job: CaseJob, *, require_assertions: bool
) -> dict[str, Any] | None:
    """Return a cleaned result, or None if the contract is broken."""
    status = normalize_status(data.get("status"))
    assertions = _normalize_assertions(data.get("assertions"))
    if require_assertions and status in {"passed", "failed"}:
        if assertions is None:
            return None
        if status == "passed" and any(
            str(a.get("status")) == "failed" for a in assertions
        ):
            status = "failed"
    failure = data.get("failure") if isinstance(data.get("failure"), dict) else None
    if status == "failed" and not failure:
        failed = next(
            (a for a in (assertions or []) if str(a.get("status")) == "failed"),
            None,
        )
        if failed:
            failure = {
                "step": 0,
                "step_desc": str(failed.get("expected") or "assertion failed"),
                "evidence": "",
            }
        else:
            return None
    return {
        "status": status,
        "reason": str(data.get("reason") or ""),
        "repo": str(data.get("repo") or job.repo),
        "title": str(data.get("title") or job.title),
        "covers": data.get("covers") or job.covers,
        "failure": failure,
        "assertions": assertions or [],
        "raw": data,
    }


def _normalize_assertions(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        typ = str(item.get("type") or "").strip().lower()
        if typ == "network":
            typ = "net"
        if typ not in _ASSERT_TYPES:
            return None
        if "expected" not in item or "actual" not in item:
            return None
        row = {
            "type": typ,
            "expected": item.get("expected"),
            "actual": item.get("actual"),
            "status": normalize_status(item.get("status")),
        }
        if item.get("carrier"):
            row["carrier"] = item.get("carrier")
        out.append(row)
    return out
