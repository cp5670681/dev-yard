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


def state_path(root: Path, cfg: QaConfig, acct: QaAccount) -> Path:
    rel = acct.state_file or default_state_file(cfg.active_env, acct.name)
    path = Path(rel)
    return path if path.is_absolute() else root / path


def auth_replay_path(root: Path, cfg: QaConfig, acct: QaAccount) -> Path:
    state = state_path(root, cfg, acct)
    return state.with_suffix(".replay.sh")


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


class _AiAuthPending(TestRejected):
    """Signals that authentication state will be explored dynamically by AI worker."""


def ensure_auth(
    root: Path,
    cfg: QaConfig,
    names: list[str] | None = None,
    on_log: Any | None = None,
) -> dict[str, str]:
    """Pre-check auth states in workspace, or run saved replay scripts.

    Returns `{account: error}` ONLY for accounts that cannot authenticate at all
    (e.g. missing state file AND missing username/password credentials).
    Accounts with credentials will be explored and saved dynamically by the AI worker.
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
        except _AiAuthPending as e:
            if on_log is not None:
                on_log(f"{e}\n")
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
    replay = auth_replay_path(root, cfg, acct)
    state.parent.mkdir(parents=True, exist_ok=True)
    has_creds = bool(acct.username and acct.password)

    if binary is None:
        binary = _which_playwright()

    # 1. Fast path: load saved state file if present
    if state.is_file():
        if on_log is not None:
            on_log(f"$ playwright-cli state-load {state}")
        r = _cli(binary, cfg, "qap-preload", ["state-load", str(state)], root)
        if r.returncode == 0:
            _cli(binary, cfg, "qap-preload", ["close"], root)
            return binary
        _cli(binary, cfg, "qap-preload", ["close"], root)

    # 2. Replay path: run saved explored login script if present
    if replay.is_file():
        if on_log is not None:
            on_log(f"replaying saved auth script: {replay}")
        if _run_auth_replay(binary, cfg, acct, state, replay, root, on_log):
            return binary

    # 3. AI Exploration path:
    if not has_creds:
        raise TestRejected(
            f"missing auth state_file {state} for account {name!r}; "
            "put username/password in qa.yaml so AI can explore login"
        )

    raise _AiAuthPending(
        f"auth for account {name!r} will be explored by AI worker during execution"
    )


def _run_auth_replay(
    binary: str,
    cfg: QaConfig,
    acct: QaAccount,
    state: Path,
    replay: Path,
    root: Path,
    on_log: Any | None,
) -> bool:
    session = "qap-preload"
    env = os.environ.copy()
    env["PLAYWRIGHT_SESSION"] = session
    env["BASE_URL"] = cfg.env.base_url
    env["USERNAME"] = acct.username or ""
    env["PASSWORD"] = acct.password or ""
    if on_log is not None:
        on_log(f"$ bash {replay}")
    try:
        r = subprocess.run(
            ["bash", str(replay)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0:
            saved = _cli(binary, cfg, session, ["state-save", str(state)], root)
            _cli(binary, cfg, session, ["close"], root)
            return saved.returncode == 0 and state.is_file()
    except Exception as e:
        if on_log is not None:
            on_log(f"auth replay error: {e}")
    _cli(binary, cfg, session, ["close"], root)
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
    executor: Any | None = None,
) -> str:
    """Run setup or cleanup. Reverts worktree files the script created (local only)."""
    from dev_yard.script_exec import ExecErrorClass, resolve_executor

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
    inherit = (cfg.env.db_exec or "host") == "inherit"
    if script.suffix.lower() == ".sql" and not inherit:
        return _run_sql(cfg, script, on_log)
    started = time.time()
    local = executor is None or getattr(executor, "site", "local") == "local"
    if executor is None:
        executor = resolve_executor(
            cfg.env, base_url=cfg.env.base_url, worktree=wt, root=root
        )
        local = executor.site == "local"
    else:
        executor.worktree = wt
        local = executor.site == "local"
    before = _porcelain_paths(wt) if local and wt and wt.is_dir() else set()
    try:
        result = executor.run(
            script,
            on_log=on_log,
            env_extra={
                "QA_ENV": cfg.active_env,
                "QA_JIRA": jira,
                "QA_CASE_ID": job.id,
                "QA_SCRIPT_KIND": kind,
            },
        )
        if result.code != 0:
            err = (result.stderr or result.stdout or "").strip() or str(result.code)
            raise TestRejected(
                f"{executor.label} {script.name} failed: {err}",
                error_class=str(result.error_class or ExecErrorClass.SCRIPT),
            )
        return (result.stdout or "").strip()
    finally:
        if local and wt and wt.is_dir():
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
