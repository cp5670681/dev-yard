from __future__ import annotations

import re
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dev_yard.qa_config import QaWorker, TestRejected
from dev_yard.runners import JobCancelled

TERMINAL = frozenset({"passed", "failed", "blocked", "skipped"})
_P_RANK = re.compile(r"^P(\d+)$", re.I)
_HTTP_5XX = re.compile(r"\b5\d\d\b|5xx", re.I)
_DB_REASON = re.compile(r"\bdb\b|usql|database", re.I)
# `cancelled`/`canceled`, as a whole word — not any "cancel" verb elsewhere.
_CANCEL_RE = re.compile(r"cancell?ed", re.I)
# Fallback signals that mean "the harness/environment broke", not the product.
_ENV_REASON = re.compile(
    r"timeout|unreachable|connection|no route|worker exit|malformed result|"
    r"missing case result|not a mapping|bad result|cleanup failed|env fault|"
    r"setup failed|fuse",
    re.I,
)
BLOCKED_KINDS = ("case-defect", "env", "cancelled", "other")


def format_blocked_kind(kind: Any) -> str:
    """`case-defect=1 env=2` for the non-zero buckets ('' when none).

    Single source for the CLI, web job log and markdown report so the four
    buckets never drift apart.
    """
    if not isinstance(kind, dict):
        return ""
    return " ".join(f"{k}={kind.get(k, 0)}" for k in BLOCKED_KINDS if kind.get(k))


def normalize_status(status: Any) -> str:
    s = str(status or "blocked").strip().lower()
    return s if s in TERMINAL else "blocked"


@dataclass
class PoolSlot:
    id: str
    provider: str | None
    model: str | None
    concurrency: int
    priority: int
    inflight: int = 0

    @classmethod
    def from_worker(cls, w: QaWorker) -> PoolSlot:
        return cls(
            id=w.id,
            provider=w.provider,
            model=w.model,
            concurrency=w.concurrency,
            priority=w.priority,
        )


@dataclass
class CaseJob:
    id: str
    title: str
    repo: str
    depends_on: list[str] = field(default_factory=list)
    priority: str = "P1"
    body: str = ""
    path: str = ""
    covers: list[str] = field(default_factory=list)
    module: str = ""
    account: str = ""
    setup: str = ""
    cleanup: str = ""
    verify: str = ""
    state: str = "pending"
    pool: str | None = None
    model: str | None = None
    provider: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    reason: str = ""
    blocked_class: str = ""
    failure: dict[str, Any] | None = None
    assertions: list[Any] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    identity: str = ""
    defect_class: str = ""
    # How many times an environment-class block has been requeued in this run
    # (M2). Bounded by `retry_attempts`; never written to result.yaml.
    attempts: int = 0


RunCase = Callable[[CaseJob, PoolSlot], dict[str, Any]]
ProgressCb = Callable[[], None]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def case_rank(priority: str) -> int:
    m = _P_RANK.match(str(priority or "P1").strip())
    return int(m.group(1)) if m else 100


def validate_dag(cases: list[CaseJob]) -> None:
    ids = {c.id for c in cases}
    for c in cases:
        for dep in c.depends_on:
            if dep not in ids:
                raise TestRejected(
                    f"depends_on {dep!r} on {c.id} does not exist"
                )
    visiting: set[str] = set()
    seen: set[str] = set()
    graph = {c.id: list(c.depends_on) for c in cases}

    def dfs(node: str) -> None:
        if node in seen:
            return
        if node in visiting:
            raise TestRejected(f"depends_on cycle involving {node}")
        visiting.add(node)
        for dep in graph.get(node, ()):
            dfs(dep)
        visiting.remove(node)
        seen.add(node)

    for c in cases:
        dfs(c.id)


def _env_signal(reason_lower: str) -> str | None:
    """Shared matcher: the specific env-fault class, or None if it is not env.

    `env_block_class` (breaker) and `blocked_kind` (reporting) must agree, or a
    reason can trip the breaker while being reported as `other` (and vice
    versa). Keep this the single source of truth.
    """
    if "login" in reason_lower or "auth" in reason_lower:
        return "login"
    if _HTTP_5XX.search(reason_lower):
        return "http-5xx"
    if _DB_REASON.search(reason_lower):
        return "db"
    if "worker exit" in reason_lower:
        return "worker-exit"
    if _ENV_REASON.search(reason_lower):
        return "env"
    return None


def env_block_class(reason: str, blocked_class: str = "") -> str | None:
    """The breaker class for a blocked case, or None if it must not trip it.

    A worker-declared `blocked_class` wins over guessing from free-form text;
    the reason regex is only a fallback for results that predate the field.
    Unknown reasons return None: the breaker must only trip on a *recognised*,
    repeated environment fault, never on two unrelated untyped blockers.
    """
    r = (reason or "").lower()
    # Host-generated reasons are classified first: they must keep their original
    # breaker semantics regardless of any `blocked_class` the host attached.
    # A cancelled run is not an environment fault: it must not trip the breaker
    # (otherwise a cancel stamps every remaining case as blocked "worker exit").
    if _CANCEL_RE.search(r):
        return None
    # A case-defect is the case's own seed gap, not the environment: it must not
    # trip the breaker either (else two bad seeds abort the whole run).
    if r.startswith("case-defect:"):
        return None
    # Host-side setup fuse already skipped remaining setup cases; do not
    # also trip the schedule breaker (that would block no-setup cases).
    if r.startswith(("env fault:", "setup failed:")):
        return None
    declared = (blocked_class or "").strip().lower()
    if declared in {"case-defect", "other", "cancelled"}:
        # Explicitly "not an environment fault" (or unrecognised): never let two
        # of them abort the rest of the run.
        return None
    if declared in {"env", "auth", "undeployed"}:
        return declared
    # No usable declared class: fall back to the text signal.
    return _env_signal(r)


def blocked_kind(reason: str, blocked_class: str = "") -> str:
    """Bucket a blocked case for reporting: case-defect | env | cancelled | other."""
    declared = (blocked_class or "").strip().lower()
    if declared in {"case-defect", "env", "cancelled", "other"}:
        return declared
    if declared in {"undeployed", "auth"}:
        return "env"
    r = (reason or "").strip()
    low = r.lower()
    if low.startswith("case-defect:"):
        return "case-defect"
    if _CANCEL_RE.search(low):
        return "cancelled"
    if low.startswith(("env fault:", "setup failed:")):
        return "env"
    return "env" if _env_signal(low) else "other"


def _retryable_block(job: CaseJob, retry_attempts: int) -> bool:
    """True when a blocked case should be requeued rather than ended (M2).

    Environment-class failures are retried even for cases that declare
    setup/cleanup — a model/network crash is transient regardless of the seed.
    Host-run script failures (`setup failed:` / `env fault:` / `cleanup failed:`)
    are the setup fuse's job and are not retried here; a case-defect / unknown
    reason is a real verdict; a cancelled case is never retried.
    """
    if retry_attempts <= 0 or job.attempts >= retry_attempts:
        return False
    reason = (job.reason or "").lower()
    if reason.startswith(("setup failed:", "env fault:", "cleanup failed:")):
        return False
    return blocked_kind(job.reason, job.blocked_class) == "env"


def pick_pool(
    pools: list[PoolSlot], tripped: set[str] | None = None
) -> PoolSlot | None:
    blocked = tripped or set()
    free = [
        p for p in pools if p.id not in blocked and p.inflight < p.concurrency
    ]
    if not free:
        return None
    return min(free, key=lambda p: (p.priority, p.id))


def pick_case(cases: list[CaseJob], busy_accounts: set[str] | None = None) -> CaseJob | None:
    busy = busy_accounts or set()
    ready = [
        c
        for c in cases
        if c.state == "ready" and not (busy and c.account and c.account in busy)
    ]
    if not ready:
        return None
    return min(ready, key=lambda c: (case_rank(c.priority), c.id))


def refresh_ready(cases: list[CaseJob], when: str | None = None) -> None:
    by_id = {c.id: c for c in cases}
    stamp = when or now_iso()
    progressed = True
    while progressed:
        progressed = False
        for c in cases:
            if c.state not in {"pending", "ready"}:
                continue
            if not c.depends_on:
                if c.state != "ready":
                    c.state = "ready"
                    progressed = True
                continue
            deps = [by_id[d] for d in c.depends_on]
            blocked_dep = next(
                (d for d in deps if d.state in {"failed", "blocked", "skipped"}),
                None,
            )
            if blocked_dep is not None:
                c.state = "skipped"
                c.reason = f"skipped: {blocked_dep.id} is {blocked_dep.state}"
                c.ended_at = stamp
                progressed = True
            elif all(d.state == "passed" for d in deps):
                if c.state != "ready":
                    c.state = "ready"
                    progressed = True
            else:
                if c.state != "pending":
                    c.state = "pending"
                    progressed = True


def progress_payload(
    run_id: str,
    env: str,
    pools: list[PoolSlot],
    cases: list[CaseJob],
    env_fault: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "env": env,
        "pools": [
            {
                "id": p.id,
                "provider": p.provider,
                "model": p.model,
                "concurrency": p.concurrency,
                "priority": p.priority,
                "inflight": p.inflight,
            }
            for p in sorted(pools, key=lambda x: (x.priority, x.id))
        ],
        "cases": [
            {
                "id": c.id,
                "title": c.title,
                "state": c.state,
                "repo": c.repo,
                "depends_on": list(c.depends_on),
                "pool": c.pool,
                "model": c.model,
                "started_at": c.started_at,
                "ended_at": c.ended_at,
                "reason": c.reason,
                "blocked_class": c.blocked_class,
                "attempts": c.attempts,
            }
            for c in cases
        ],
    }
    if env_fault:
        payload["env_fault"] = env_fault
    return payload


def progress_line(payload: dict[str, Any]) -> str:
    cases = payload.get("cases") or []
    pools = payload.get("pools") or []
    running = [c for c in cases if c.get("state") == "running"]
    queue = sum(1 for c in cases if c.get("state") == "ready")
    if not running:
        return f"queue {queue}"
    c = running[-1]
    pool = next((p for p in pools if p.get("id") == c.get("pool")), None)
    inflight = pool.get("inflight") if pool else "?"
    conc = pool.get("concurrency") if pool else "?"
    pid = pool.get("id") if pool else "?"
    return (
        f"running {c.get('id')} on {c.get('model') or '?'} "
        f"({pid} {inflight}/{conc}); queue {queue}"
    )


def run_schedule(
    cases: list[CaseJob],
    pools: list[PoolSlot],
    run_case: RunCase,
    on_progress: ProgressCb | None = None,
    serialize_accounts: bool = False,
    cancel_check: Callable[[], bool] | None = None,
    retry_attempts: int = 0,
    retry_backoff: float = 0.0,
    on_pool_trip: Callable[[str, str], None] | None = None,
) -> None:
    validate_dag(cases)
    refresh_ready(cases)
    max_workers = max(1, sum(p.concurrency for p in pools))
    # A tripped pool stops receiving work. Other pools keep draining the queue.
    # Remaining cases are stamped only once every pool has tripped.
    tripped: dict[str, str] = {}
    last_block_class: dict[str, str | None] = {}
    cancelled = False

    def ping() -> None:
        if on_progress is not None:
            on_progress()

    def pools_open() -> bool:
        return any(p.id not in tripped for p in pools)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        inflight: dict[Any, tuple[CaseJob, PoolSlot]] = {}
        inflight_accounts: set[str] = set()
        while True:
            if cancel_check is not None and cancel_check():
                # Stop dispatching new cases. In-flight pi workers are killed by
                # their spawn hook; a setup/cleanup script already running is not,
                # so draining can still take as long as that script.
                cancelled = True
            refresh_ready(cases)
            if pools_open() and not cancelled:
                while True:
                    if cancel_check is not None and cancel_check():
                        cancelled = True
                        break
                    slot = pick_pool(pools, set(tripped))
                    job = pick_case(
                        cases, inflight_accounts if serialize_accounts else None
                    )
                    if slot is None or job is None:
                        break
                    job.state = "running"
                    job.pool = slot.id
                    job.model = slot.model
                    job.provider = slot.provider
                    job.started_at = now_iso()
                    slot.inflight += 1
                    if serialize_accounts and job.account:
                        # Opt-in: some apps kick other sessions on the same account.
                        inflight_accounts.add(job.account)
                    ping()
                    fut = pool.submit(_safe_run, run_case, job, slot)
                    inflight[fut] = (job, slot)
            if not inflight:
                break
            done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fut in done:
                job, slot = inflight.pop(fut)
                slot.inflight = max(0, slot.inflight - 1)
                if serialize_accounts and job.account:
                    inflight_accounts.discard(job.account)
                result = fut.result()
                status = normalize_status(result.get("status"))
                job.state = status
                job.reason = str(result.get("reason") or "")
                job.blocked_class = str(result.get("blocked_class") or "")
                job.defect_class = str(result.get("defect_class") or "")
                job.failure = (
                    result.get("failure")
                    if isinstance(result.get("failure"), dict)
                    else None
                )
                if isinstance(result.get("assertions"), list):
                    job.assertions = result["assertions"]
                if result.get("model"):
                    job.model = str(result.get("model"))
                if result.get("provider"):
                    job.provider = str(result.get("provider"))
                job.ended_at = now_iso()
                pid = slot.id
                if status == "blocked" and _retryable_block(job, retry_attempts):
                    # Environment-class failure on a case the host owns end to
                    # end: requeue it instead of recording a terminal block, so
                    # a transient model/env fault does not end the run (M2).
                    job.attempts += 1
                    job.state = "ready"
                    job.pool = None
                    job.model = None
                    job.provider = None
                    job.reason = ""
                    job.blocked_class = ""
                    job.ended_at = None
                    # Do not count a requeued failure toward the pool breaker.
                    if retry_backoff > 0:
                        time.sleep(retry_backoff)
                elif status == "blocked":
                    klass = env_block_class(job.reason, job.blocked_class)
                    if klass is None:
                        last_block_class[pid] = None
                    elif last_block_class.get(pid) == klass:
                        tripped[pid] = job.reason or "environment blocked"
                        if on_pool_trip is not None:
                            on_pool_trip(pid, tripped[pid])
                    else:
                        last_block_class[pid] = klass
                else:
                    last_block_class[pid] = None
                ping()
        if not pools_open() and tripped:
            stamp = now_iso()
            breaker_reason = next(iter(tripped.values()))
            for job in cases:
                if job.state in {"pending", "ready"}:
                    job.state = "blocked"
                    job.reason = breaker_reason
                    job.blocked_class = "env"
                    job.ended_at = stamp
            ping()
        elif cancelled:
            stamp = now_iso()
            for job in cases:
                if job.state in {"pending", "ready"}:
                    job.state = "blocked"
                    job.reason = "cancelled: run cancelled"
                    job.blocked_class = "cancelled"
                    job.ended_at = stamp
            ping()


def _safe_run(
    run_case: RunCase, job: CaseJob, slot: PoolSlot
) -> dict[str, Any]:
    try:
        result = run_case(job, slot)
    except JobCancelled as e:
        return {"status": "blocked", "reason": f"cancelled: {e}"}
    except Exception as e:  # noqa: BLE001 — worker must not kill the run
        return {"status": "blocked", "reason": f"worker exit: {e}"}
    if not isinstance(result, dict):
        return {"status": "blocked", "reason": "worker exit: bad result"}
    return result
