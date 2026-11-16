"""Per-env QA lock and setup-error classification.

Design-time data verification (``verify.sql`` pre-proving / ``seeds.yaml`` / the
reflow loop) is gone: the worker self-serves data at run time through
``dev-yard qa sql`` / ``qa exec``, so the host no longer pre-proves anything.
What is left here is the one thing the host must still own — a per-env lock, so
two requirements never write the same test database at once.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dev_yard.qa_config import TestRejected

LOCK_DIR = Path(".yard-qa") / "locks"

# Signals that a script failed for infrastructure reasons, not because the case
# is wrong. Kept next to the executor so callers agree on what "env" means.
_ENV_VERIFY_HINTS = (
    "usql not found",
    "no db.url",
    "timed out",
    "cannot run",
    "connection",
    "could not connect",
    "no route",
    "unreachable",
    "password authentication",
    "too many connections",
    "server closed the connection",
)
_ENV_VERIFY_CLASSES = frozenset(
    {"unreachable", "auth", "pod_not_found", "timeout", "runner", "config"}
)


def verify_env_error(e: TestRejected) -> bool:
    """True when a script failure is environmental, not a case data gap."""
    cls = str(getattr(e, "error_class", "") or "").lower()
    if cls in _ENV_VERIFY_CLASSES:
        return True
    low = str(e).lower()
    return any(h in low for h in _ENV_VERIFY_HINTS)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def env_lock(
    root: Path,
    env: str,
    what: str = "qa run",
    wait_timeout: float = 0.0,
    on_wait: Callable[[bool], None] | None = None,
) -> Any:
    """Serialize per-env QA work (runs) across requirements.

    `req test` already holds a per-JIRA lock, but seeds share one test DB, so two
    requirements' runs must not overlap. `what` names the holder in the refusal
    message.

    `wait_timeout > 0` makes the caller queue behind a live holder instead of
    failing immediately. A stale lock is still reclaimed at once. When `on_wait`
    is given it is called with `True` on entering a wait and `False` once the
    lock is held, so the board can show "waiting for env".
    """
    lock_dir = root / LOCK_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", env or "env")
    path = lock_dir / f"{safe}.verify.lock"
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    deadline = time.monotonic() + wait_timeout if wait_timeout > 0 else None
    waiting = False
    while True:
        tmp = lock_dir / f".{safe}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(token, encoding="utf-8")
            try:
                os.link(tmp, path)
            except FileExistsError:
                holder = ""
                try:
                    holder = path.read_text(encoding="utf-8").strip()
                except OSError:
                    holder = ""
                head = holder.split(":", 1)[0] if holder else ""
                pid = int(head) if head.isdigit() else None
                if pid is not None and not _pid_alive(pid):
                    # Atomic reclaim: the winner renames, losers get FileNotFound.
                    stale = path.with_name(path.name + f".stale.{uuid.uuid4().hex}")
                    try:
                        os.rename(path, stale)
                    except OSError:
                        pass
                    else:
                        stale.unlink(missing_ok=True)
                    continue
                if deadline is not None and time.monotonic() < deadline:
                    if not waiting:
                        waiting = True
                        if on_wait is not None:
                            on_wait(True)
                    time.sleep(1.0)
                    continue
                raise TestRejected(
                    f"another {what} is running for env {env}; "
                    f"wait for it (or delete {path} if it is stale)"
                ) from None
        finally:
            tmp.unlink(missing_ok=True)
        break
    if waiting and on_wait is not None:
        on_wait(False)
    try:
        yield
    finally:
        try:
            if path.read_text(encoding="utf-8").strip() == token:
                path.unlink(missing_ok=True)
        except OSError:
            pass
