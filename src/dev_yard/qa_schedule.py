from __future__ import annotations

import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from dev_yard.qa_config import QaWorker, TestRejected

TERMINAL = frozenset({"passed", "failed", "blocked", "skipped"})
_P_RANK = re.compile(r"^P(\d+)$", re.I)
_HTTP_5XX = re.compile(r"\b5\d\d\b")


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
    def from_worker(cls, w: QaWorker) -> "PoolSlot":
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
    state: str = "pending"
    pool: str | None = None
    model: str | None = None
    provider: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    reason: str = ""
    failure: dict[str, Any] | None = None


RunCase = Callable[[CaseJob, PoolSlot], dict[str, Any]]
ProgressCb = Callable[[], None]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def env_block_class(reason: str) -> str:
    r = (reason or "").lower()
    if "login" in r or "auth" in r:
        return "login"
    if "5xx" in r or _HTTP_5XX.search(r):
        return "http-5xx"
    if "db" in r or "usql" in r or "database" in r:
        return "db"
    if "worker exit" in r:
        return "worker-exit"
    return "env"


def pick_pool(pools: list[PoolSlot]) -> PoolSlot | None:
    free = [p for p in pools if p.inflight < p.concurrency]
    if not free:
        return None
    return min(free, key=lambda p: (p.priority, p.id))


def pick_case(cases: list[CaseJob]) -> CaseJob | None:
    ready = [c for c in cases if c.state == "ready"]
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
) -> dict[str, Any]:
    return {
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
            }
            for c in cases
        ],
    }


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
) -> None:
    validate_dag(cases)
    refresh_ready(cases)
    max_workers = max(1, sum(p.concurrency for p in pools))
    breaker = False
    breaker_reason = ""
    last_block_class: str | None = None

    def ping() -> None:
        if on_progress is not None:
            on_progress()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        inflight: dict[Any, tuple[CaseJob, PoolSlot]] = {}
        while True:
            refresh_ready(cases)
            if not breaker:
                while True:
                    slot = pick_pool(pools)
                    job = pick_case(cases)
                    if slot is None or job is None:
                        break
                    job.state = "running"
                    job.pool = slot.id
                    job.model = slot.model
                    job.provider = slot.provider
                    job.started_at = now_iso()
                    slot.inflight += 1
                    ping()
                    fut = pool.submit(_safe_run, run_case, job, slot)
                    inflight[fut] = (job, slot)
            if not inflight:
                break
            done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fut in done:
                job, slot = inflight.pop(fut)
                slot.inflight = max(0, slot.inflight - 1)
                result = fut.result()
                status = normalize_status(result.get("status"))
                job.state = status
                job.reason = str(result.get("reason") or "")
                job.failure = (
                    result.get("failure")
                    if isinstance(result.get("failure"), dict)
                    else None
                )
                if result.get("model"):
                    job.model = str(result.get("model"))
                if result.get("provider"):
                    job.provider = str(result.get("provider"))
                job.ended_at = now_iso()
                if status == "blocked":
                    klass = env_block_class(job.reason)
                    if last_block_class == klass:
                        breaker = True
                        breaker_reason = job.reason or "environment blocked"
                    else:
                        last_block_class = klass
                else:
                    last_block_class = None
                ping()
        if breaker:
            stamp = now_iso()
            for job in cases:
                if job.state in {"pending", "ready"}:
                    job.state = "blocked"
                    job.reason = breaker_reason
                    job.ended_at = stamp
            ping()


def _safe_run(
    run_case: RunCase, job: CaseJob, slot: PoolSlot
) -> dict[str, Any]:
    try:
        result = run_case(job, slot)
    except Exception as e:  # noqa: BLE001 — worker must not kill the run
        return {"status": "blocked", "reason": f"worker exit: {e}"}
    if not isinstance(result, dict):
        return {"status": "blocked", "reason": "worker exit: bad result"}
    return result
