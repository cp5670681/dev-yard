from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dev_yard import grill_round, paths, service
from dev_yard.actions import HOST_JOB_ACTIONS, TICKET_ACTIONS
from dev_yard.pi_session import load_conversation
from dev_yard.qa_schedule import format_blocked_kind
from dev_yard.runners import (
    JobCancelled,
    Runner,
    RunResult,
    clip_summary,
    kill_proc_group,
    pi_argv,
    run_pi_print_tracked,
)

__all__ = [
    "BoardSse",
    "Job",
    "JobCancelled",
    "JobLogRunner",
    "JobRunner",
    "JobSse",
    "PiChatSse",
    "default_execute",
    "format_sse",
]

Execute = Callable[[Path, "Job"], None]
_TERMINAL = {"ok", "error", "cancelled"}
# Completed jobs keep their full log/pi_runs; cap how many we retain so a
# long-lived console does not grow without bound.
_MAX_JOBS = 200
_TICKET_ACTIONS = TICKET_ACTIONS
# Names with dedicated execute branches (kept aligned with actions.JOB_ACTIONS).
_HOST_JOB_ACTIONS = HOST_JOB_ACTIONS


def format_sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _questions_suffix(result: dict[str, Any]) -> str:
    """OPEN-QUESTIONS count, distinguishing an empty file from a missing one."""
    q = result.get("questions") or 0
    if q:
        return f" OPEN-QUESTIONS={q}"
    oq = result.get("open_questions") or {}
    if isinstance(oq, dict):
        if oq.get("error"):
            return " OPEN-QUESTIONS=(读取失败)"
        if oq.get("exists"):
            return " OPEN-QUESTIONS=0(空文件)"
    return " OPEN-QUESTIONS=(缺失)"


@dataclass
class Job:
    id: str
    jira: str
    action: str
    state: str = "queued"
    log: str = ""
    ticket_ids: list[str] | None = None
    extra: dict = field(default_factory=dict)
    label: str = ""
    grill: dict | None = None
    pi_runs: list[dict[str, Any]] = field(default_factory=list)
    qa_progress: dict[str, Any] | None = None
    done: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _cv: threading.Condition = field(init=False, repr=False, compare=False)
    _seq: int = field(default=0, repr=False, compare=False)
    _input: threading.Event = field(default_factory=threading.Event)
    _answers: list[dict[str, Any]] | None = None
    # Live pi subprocesses. One for serial stages, several for a parallel qa run;
    # cancel() kills them all.
    _procs: set[subprocess.Popen[str]] = field(
        default_factory=set, repr=False, compare=False
    )
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    on_change: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._cv = threading.Condition(self._lock)

    def _bump(self) -> None:
        self._seq += 1
        self._cv.notify_all()
        cb = self.on_change
        if cb is not None:
            cb()

    def _public(self) -> dict:
        return {
            "id": self.id,
            "jira": self.jira,
            "action": self.action,
            "state": self.state,
            "log": self.log,
            "ticket_ids": self.ticket_ids,
            "label": self.label,
            "grill": self.grill,
            "pi_runs": list(self.pi_runs),
            "qa_progress": self.qa_progress,
        }

    def append(self, text: str) -> None:
        if not text:
            return
        with self._cv:
            self.log += text if text.endswith("\n") else text + "\n"
            self._bump()

    def snapshot(self) -> dict:
        with self._cv:
            return self._public()

    def brief(self) -> dict:
        with self._cv:
            data = self._public()
            return {k: data[k] for k in ("id", "jira", "action", "state", "ticket_ids", "label")}

    def capture(self) -> tuple[dict, int]:
        with self._cv:
            return self._public(), self._seq

    def current_seq(self) -> int:
        with self._cv:
            return self._seq

    def wait_seq(self, seq: int, timeout: float | None = None) -> int:
        with self._cv:
            if self._seq > seq:
                return self._seq
            self._cv.wait(timeout=timeout)
            return self._seq

    def set_state(self, state: str) -> None:
        with self._cv:
            self.state = state
            self._bump()

    def set_waiting(self, payload: dict) -> None:
        with self._cv:
            self._input.clear()
            self._answers = None
            self.state = "waiting"
            self.grill = payload
            self._bump()

    def submit_answers(self, answers: list[dict[str, Any]]) -> None:
        if not isinstance(answers, list):
            raise ValueError("answers must be a list")
        with self._cv:
            if self.state != "waiting":
                raise ValueError("job is not waiting for answers")
            self._answers = answers
            self.state = "running"
            self.grill = None
            self._input.set()
            self._bump()

    def wait_answers(self) -> list[dict[str, Any]]:
        self._input.wait()
        with self._cv:
            answers = self._answers
            self._answers = None
            if answers is None:
                raise JobCancelled("grill cancelled")
            return answers

    def record_pi_run(self, cwd: Path) -> dict[str, Any]:
        run = {
            "cwd": str(cwd.resolve()),
            "started_at": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
        with self._cv:
            run = {"index": len(self.pi_runs), **run}
            self.pi_runs.append(run)
            self._bump()
            return dict(run)

    def set_qa_progress(self, payload: dict[str, Any] | None) -> None:
        with self._cv:
            self.qa_progress = payload
            self._bump()

    def track_proc(self, proc: subprocess.Popen[str]) -> None:
        """Track a live pi subprocess so cancel() can kill it immediately."""
        with self._cv:
            self._procs.add(proc)
            cancelled = self.cancel_requested.is_set()
        if cancelled:
            _kill_proc(proc)

    def untrack_proc(self, proc: subprocess.Popen[str]) -> None:
        """Drop a finished subprocess so a later cancel cannot target its pid."""
        with self._cv:
            self._procs.discard(proc)

    def cancel(self) -> None:
        """Idempotent cancel request: kill the live pi subprocesses and interrupt waits.

        The job thread does not stop here — it reacts by raising JobCancelled at the
        next checkpoint (wait_answers, or a runner around each pi run).
        """
        self.cancel_requested.set()
        with self._cv:
            procs = list(self._procs)
            if self.state == "waiting":
                self._answers = None
                self._input.set()
        for proc in procs:
            _kill_proc(proc)


def _kill_proc(proc: subprocess.Popen[str] | None) -> None:
    if proc is not None:
        kill_proc_group(proc)


class JobSse:
    def __init__(self, job: Job) -> None:
        self.job = job
        self._log_off = 0
        self._sent_snapshot = False
        self._last_state: str | None = None
        self._last_grill: Any = object()
        self._last_pi_runs: Any = object()
        self._last_qa_progress: Any = object()

    def poll(self) -> tuple[list[str], bool, int]:
        snap, seq = self.job.capture()
        frames: list[str] = []
        if not self._sent_snapshot:
            frames.append(format_sse("snapshot", snap))
            self._sent_snapshot = True
            self._log_off = len(snap["log"])
            self._last_state = snap["state"]
            self._last_grill = snap["grill"]
            self._last_pi_runs = snap.get("pi_runs")
            self._last_qa_progress = snap.get("qa_progress")
        else:
            if len(snap["log"]) > self._log_off:
                frames.append(format_sse("log", snap["log"][self._log_off :]))
                self._log_off = len(snap["log"])
            if (
                snap["state"] != self._last_state
                or snap["grill"] != self._last_grill
                or snap.get("pi_runs") != self._last_pi_runs
                or snap.get("qa_progress") != self._last_qa_progress
            ):
                frames.append(
                    format_sse(
                        "state",
                        {
                            "id": snap["id"],
                            "jira": snap["jira"],
                            "action": snap["action"],
                            "state": snap["state"],
                            "grill": snap["grill"],
                            "ticket_ids": snap["ticket_ids"],
                            "pi_runs": snap.get("pi_runs") or [],
                            "qa_progress": snap.get("qa_progress"),
                        },
                    )
                )
                self._last_state = snap["state"]
                self._last_grill = snap["grill"]
                self._last_pi_runs = snap.get("pi_runs")
                self._last_qa_progress = snap.get("qa_progress")
        done = snap["state"] in _TERMINAL
        if done:
            frames.append(format_sse("done", snap))
        return frames, done, seq


def _pi_run_until(runs: list[dict[str, Any]], index: int) -> str | None:
    cwd = runs[index]["cwd"]
    for later in runs[index + 1 :]:
        if later.get("cwd") == cwd:
            return later.get("started_at")
    return None


class PiChatSse:
    def __init__(
        self,
        job: Job,
        run_index: int,
        root: Path,
        sessions_dir: Path | None = None,
    ) -> None:
        self.job = job
        self.run_index = run_index
        self.root = root
        self.sessions_dir = sessions_dir
        self._sent_snapshot = False
        self._offset = 0
        self._found = False

    def poll(self) -> tuple[list[str], bool, int]:
        snap, seq = self.job.capture()
        runs = snap.get("pi_runs") or []
        if self.run_index < 0 or self.run_index >= len(runs):
            return [format_sse("done", {"error": "unknown run"})], True, seq
        run = runs[self.run_index]
        try:
            data = load_conversation(
                Path(run["cwd"]),
                root=self.root,
                started_at=run.get("started_at"),
                until=_pi_run_until(runs, self.run_index),
                offset=self._offset,
                sessions_dir=self.sessions_dir,
            )
        except PermissionError:
            return [format_sse("done", {"error": "forbidden"})], True, seq
        frames: list[str] = []
        meta = {
            "job_id": snap["id"],
            "run": self.run_index,
            "cwd": data["cwd"],
            "started_at": run.get("started_at"),
            "found": data["found"],
            "session_id": data["session_id"],
            "job_state": snap["state"],
        }
        if not self._sent_snapshot or (data["found"] and not self._found):
            frames.append(format_sse("snapshot", meta))
            self._sent_snapshot = True
        for entry in data["entries"]:
            frames.append(format_sse("entry", entry))
        self._offset = data["next_offset"]
        self._found = data["found"]
        later = self.run_index < len(runs) - 1
        done = snap["state"] in _TERMINAL or later
        if done:
            frames.append(format_sse("done", {"found": data["found"], "job_state": snap["state"]}))
        return frames, done, seq


class JobLogRunner(Runner):
    def __init__(
        self,
        job: Job,
        root: Path,
        bundle: str,
        *,
        spec: Any = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self.job = job
        self.root = root
        self.bundle = bundle
        self.spec = spec
        self.provider = provider
        self.model = model

    def start(
        self,
        prompt: str,
        cwd: Path,
        extra_read_paths: list[Path],
        repo: str | None = None,
    ) -> RunResult:
        if self.job.cancel_requested.is_set():
            raise JobCancelled(f"{self.bundle} cancelled before pi run")
        argv = pi_argv(
            root=self.root,
            bundle=self.bundle,
            prompt=None,
            print_mode=True,
            repo=repo,
            spec=self.spec,
            provider=self.provider,
            model=self.model,
        )
        binary = argv[0]
        if not shutil.which(binary) and not Path(binary).exists():
            msg = f"pi not found (`{binary}`). Install pi or set YARD_PI to its path."
            self.job.append(msg)
            return RunResult(ok=False, summary=msg, exit_code=127)
        self.job.record_pi_run(cwd)
        self.job.append(f"$ {binary} -p …  cwd={cwd}")

        def _log(line: str) -> None:
            self.job.append(line if line.endswith("\n") else line + "\n")

        code, raw = run_pi_print_tracked(
            argv,
            cwd,
            prompt,
            on_line=_log,
            on_spawn=self.job.track_proc,
            on_reap=self.job.untrack_proc,
        )
        if self.job.cancel_requested.is_set():
            raise JobCancelled(f"{self.bundle} cancelled (pi exit {code})")
        blocked = code != 0 or "REVIEW_FAILED" in raw
        summary = clip_summary(raw, self.bundle) or f"pi exit {code}"
        return RunResult(
            ok=not blocked,
            summary=summary,
            exit_code=code if code else (1 if blocked else 0),
        )


def default_execute(root: Path, job: Job) -> None:
    extra = job.extra or {}
    if job.action == "repo_add":
        repo = service.repo_add(
            root,
            str(extra.get("alias") or ""),
            str(extra.get("url") or ""),
            str(extra.get("default_base") or "main"),
            str(extra.get("role") or "svc"),
            extra.get("path") or None,
            on_progress=job.append,
            provider=extra.get("provider") or None,
            model=extra.get("model") or None,
            test_branch=extra.get("test_branch") or None,
        )
        job.append(f"added {repo.alias} -> {repo.source_path(root)}")
        return
    if job.action == "open":
        source = str(extra.get("source") or "pi")
        runner = JobLogRunner(job, root, "open") if source == "pi" else None
        dest, warning = service.req_open(
            root,
            job.jira,
            source=source,
            target=extra.get("target"),
            payload=extra.get("payload"),
            force=bool(extra.get("force")),
            on_progress=job.append if source != "pi" else None,
            runner=runner,
        )
        job.append(str(dest))
        if warning:
            job.append(warning)
        return
    if job.action == "reset-phase":
        data = service.req_reset_phase(root, job.jira)
        job.append(f"{job.jira} phase={data.get('phase')}")
        return
    if job.action == "freeze":
        created = service.req_freeze(
            root, job.jira, force=bool((job.extra or {}).get("force"))
        )
        for p in created:
            job.append(str(p))
        return
    if job.action == "push":
        extra = job.extra or {}
        remote = str(extra.get("remote") or "origin")
        force = bool(extra.get("force", False))
        repos_filter = extra.get("repos")
        if isinstance(repos_filter, str) and repos_filter.strip():
            repos_filter = [r.strip() for r in repos_filter.split(",") if r.strip()]
        elif not isinstance(repos_filter, list):
            repos_filter = None
        results = service.req_push(
            root,
            job.jira,
            repos=repos_filter,
            remote=remote,
            force=force,
            on_progress=job.append,
        )
        pushed_summary = ", ".join(f"{r['repo']} ({r['branch']})" for r in results)
        job.append(f"pushed {job.jira} to remote: {pushed_summary}")
        return
    if job.action == "sync":
        extra = job.extra or {}
        strategy = str(extra.get("strategy") or "ff-only")
        repos_filter = extra.get("repos")
        if isinstance(repos_filter, str) and repos_filter.strip():
            repos_filter = [r.strip() for r in repos_filter.split(",") if r.strip()]
        elif not isinstance(repos_filter, list):
            repos_filter = None
        results = service.req_sync(
            root,
            job.jira,
            repos=repos_filter,
            strategy=strategy,
            on_progress=job.append,
        )
        summary = ", ".join(f"{r['repo']}:{r['status']}" for r in results)
        job.append(f"synced {job.jira}: {summary}")
        return
    if job.action == "submit-test":
        from dev_yard import test_integrate
        from dev_yard.config import resolve_pi_choice
        from dev_yard.stages import RESOLVE_MERGE_SPEC
        from dev_yard.test_report import ReportRejected, submit_test

        extra = job.extra or {}
        resolve = extra.get("resolve")
        ai_resolve = None if resolve is None else bool(resolve)
        force_all = bool(extra.get("force_all") or extra.get("force"))

        def _runner_factory(root: Path, jira: str, alias: str) -> JobLogRunner:
            provider, model = resolve_pi_choice(root, "implement", repo=alias)
            return JobLogRunner(
                job,
                root,
                "resolve-merge",
                spec=RESOLVE_MERGE_SPEC,
                provider=provider,
                model=model,
            )

        try:
            data = submit_test(
                root,
                job.jira,
                remote=str(extra.get("remote") or "origin"),
                ai_resolve=ai_resolve,
                force_all=force_all,
                on_progress=job.append,
                runner_factory=_runner_factory,
            )
        except ReportRejected as e:
            raise RuntimeError(str(e)) from e
        for line in test_integrate.integration_report(data):
            job.append(line)
        job.append(f"{job.jira} phase={data.get('phase')}")
        return
    if job.action in {"run-test", "qa-review"}:
        from dev_yard.qa import req_test
        from dev_yard.qa_config import TestRejected

        extra = job.extra or {}
        try:
            result = req_test(
                root,
                job.jira,
                env=(extra.get("env") or "").strip() or None,
                print_mode=True,
                design_only=bool(extra.get("design_only")),
                run_only=bool(extra.get("run_only")),
                redesign=bool(extra.get("redesign")),
                approve=bool(extra.get("approve")),
                feedback=(extra.get("feedback") or "").strip() or None,
                ingest=not bool(extra.get("no_ingest")),
                resume=extra.get("resume"),
                rerun_cases=extra.get("rerun_cases"),
                on_log=job.append,
                on_progress=job.set_qa_progress,
                cancel_check=job.cancel_requested.is_set,
                on_spawn=job.track_proc,
                on_reap=job.untrack_proc,
            )
        except TestRejected as e:
            raise RuntimeError(str(e)) from e
        if result.get("awaiting_review"):
            review = result.get("review") or {}
            job.append(
                f"{job.jira} 用例待审核 cases={result.get('cases')} "
                f"review={review.get('status') or '?'} "
                f"({result.get('reason') or ''})" + _questions_suffix(result)
            )
            return
        if result.get("design_only"):
            review = result.get("review") or {}
            job.append(
                f"{job.jira} design-only cases={result.get('cases')} "
                f"review={review.get('status') or '?'}" + _questions_suffix(result)
            )
            return
        summary = result.get("summary") or {}
        job.append(
            f"{job.jira} run={result.get('run_id')} "
            f"passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} "
            f"blocked={summary.get('blocked', 0)} skipped={summary.get('skipped', 0)}"
        )
        kind = summary.get("blocked_kind")
        shown = format_blocked_kind(kind)
        if shown:
            job.append(f"blocked 分类：{shown}")
            if kind.get("case-defect"):
                job.append(
                    f"{kind['case-defect']} 条为用例种子缺口（case-defect），"
                    f"需 --redesign 补种子后重跑"
                )
        if result.get("run_id"):
            job.append(f"报告：dev-yard qa report {job.jira}")
        if result.get("ingest_skipped"):
            job.append(f"ingest skipped ({result['ingest_skipped']})")
        return
    from dev_yard.stages import load_registry

    spec = load_registry(root).get(job.action)
    if spec is not None and job.action not in _HOST_JOB_ACTIONS:
        runner = JobLogRunner(job, root, spec.name)
        result = service.run_stage(root, spec, job.jira, print_mode=True, runner=runner)
        if not result.ok:
            raise RuntimeError(f"{spec.name} failed")
        job.append(f"{spec.name} finished")
        return
    bundle = {
        "grill": "grill",
        "spec": "spec",
        "tickets": "tickets",
        "implement": "implement",
        "review": "review",
        "contract": "contract",
        "fix-contract": "implement",
        "fix-test": "implement",
    }.get(job.action)
    if bundle is None:
        raise ValueError(f"unknown action {job.action}")
    if job.action == "grill":
        _run_web_grill(root, job)
        return
    runner = JobLogRunner(job, root, bundle)
    if job.action in {"spec", "tickets"}:
        result = service.run_stage(
            root, job.action, job.jira, print_mode=True, runner=runner
        )
        if not result.ok:
            raise RuntimeError(f"{job.action} failed")
        job.append(f"{job.action} finished")
        return
    if job.action in {"implement", "fix-contract", "fix-test"}:
        ran = service.implement(
            root,
            job.jira,
            job.ticket_ids,
            print_mode=True,
            runner=runner,
            from_contract=job.action == "fix-contract",
            from_test=job.action == "fix-test",
        )
        job.append("ran: " + (", ".join(ran) if ran else "(none)"))
        return
    if job.action == "review":
        ran = service.review(
            root, job.jira, job.ticket_ids, print_mode=True, runner=runner
        )
        job.append("reviewed: " + (", ".join(ran) if ran else "(none)"))
        return
    ran = service.review(
        root, job.jira, None, contract=True, print_mode=True, runner=runner
    )
    job.append("contract: " + (", ".join(ran) if ran else "(none)"))


def _run_web_grill(root: Path, job: Job) -> None:
    extra = grill_round.web_grill_extra(job.jira)
    req = paths.req_dir(root, job.jira)
    for _ in range(grill_round.MAX_ROUNDS):
        rnd = grill_round.load_round(req)
        if rnd is None or not rnd.awaiting():
            runner = JobLogRunner(job, root, "grill")
            result = service.run_stage(
                root,
                "grill",
                job.jira,
                print_mode=True,
                runner=runner,
                prompt_extra=extra,
            )
            if not result.ok:
                raise RuntimeError("grill failed")
            rnd = grill_round.load_round(req)
            if rnd is None or not rnd.awaiting():
                leftover = grill_round.round_path(req)
                if leftover.exists():
                    leftover.unlink()
                job.append("grill finished")
                return
        job.append(f"round {rnd.round}: {len(rnd.questions)} questions")
        job.set_waiting(rnd.to_dict())
        answers = job.wait_answers()
        grill_round.apply_answers(req, rnd, answers)
        job.append(f"round {rnd.round} answers recorded")
        extra = (
            grill_round.web_grill_extra(job.jira)
            + "\nPrevious round answers are in GRILL.md. Continue the frontier."
        )
    raise RuntimeError("too many grill rounds")


class BoardSse:
    def __init__(self, runner: JobRunner) -> None:
        self.runner = runner
        self._last: list[dict] | None = None

    def poll(self) -> tuple[list[str], int]:
        seq = self.runner.board_seq()
        items = self.runner.running_brief()
        frames: list[str] = []
        if items != self._last:
            frames.append(format_sse("jobs", items))
            self._last = items
        return frames, seq


class JobRunner:
    def __init__(
        self,
        root: Path,
        execute: Execute | None = None,
        sync: bool = False,
    ) -> None:
        self.root = root
        self._execute = execute or default_execute
        self.sync = sync
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._board_cv = threading.Condition()
        self._board_seq = 0

    def _bump_board(self) -> None:
        with self._board_cv:
            self._board_seq += 1
            self._board_cv.notify_all()

    def board_seq(self) -> int:
        with self._board_cv:
            return self._board_seq

    def wait_board(self, seq: int, timeout: float | None = None) -> int:
        with self._board_cv:
            if self._board_seq > seq:
                return self._board_seq
            self._board_cv.wait(timeout=timeout)
            return self._board_seq

    def running_brief(self) -> list[dict]:
        return [j.brief() for j in self.running()]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        """Signal a running job to stop; None when the job id is unknown."""
        job = self.get(job_id)
        if job is not None:
            job.cancel()
        return job

    def latest(self, jira: str) -> Job | None:
        with self._lock:
            matches = [j for j in self._jobs.values() if j.jira == jira]
        return matches[-1] if matches else None

    def busy_tickets(self, jira: str, action: str) -> set[str] | None:
        """Ticket ids occupied by any running ticket job. None means the whole Jira is busy."""
        occupied: set[str] = set()
        for job in self.running():
            if job.jira != jira:
                continue
            scope = _ticket_scope(job)
            if scope is None:
                if action is None or job.action == action or job.action not in _TICKET_ACTIONS:
                    return None
                continue
            occupied.update(scope)
        return occupied

    def running(self) -> list[Job]:
        with self._lock:
            return [
                j
                for j in self._jobs.values()
                if j.state in {"queued", "running", "waiting"}
            ]

    def resume_pending_grills(self) -> list[Job]:
        if self.sync:
            return []
        restored: list[Job] = []
        for req in paths.iter_req_dirs(self.root):
            rnd = grill_round.load_round_file(req)
            if rnd is None or not rnd.awaiting():
                continue
            try:
                job = self.submit("grill", req.name)
            except ValueError:
                continue
            restored.append(job)
        return restored

    def submit(
        self,
        action: str,
        jira: str,
        ticket_ids: list[str] | None = None,
        extra: dict | None = None,
    ) -> Job:
        with self._lock:
            extra = extra or {}
            # A re-run may queue behind an active run: `req_test` serialises on
            # its own run lock, so the second one simply waits its turn instead
            # of being rejected. Identical re-runs are deduped so a double click
            # cannot stack two jobs that would each reset and run the same case.
            if action == "run-test":
                wanted = {c for c in extra.get("rerun_cases") or [] if c}
                if wanted:
                    for existing in self._jobs.values():
                        if existing.state not in {"queued", "running", "waiting"}:
                            continue
                        if existing.jira != jira or existing.action != "run-test":
                            continue
                        pending = {
                            c for c in (existing.extra or {}).get("rerun_cases") or [] if c
                        }
                        if pending == wanted:
                            raise ValueError("同一批用例已有重测在排队")
            for existing in self._jobs.values():
                if existing.state not in {"queued", "running", "waiting"}:
                    continue
                if _jobs_conflict(existing, jira, action, ticket_ids, extra):
                    raise ValueError(_conflict_message(existing, action, ticket_ids))
            job = Job(
                id=uuid.uuid4().hex[:10],
                jira=jira,
                action=action,
                ticket_ids=ticket_ids,
                extra=extra,
                label=str(extra.get("label") or ""),
                on_change=self._bump_board,
            )
            self._jobs[job.id] = job
            self._prune_locked()
        self._bump_board()
        if self.sync:
            self._run(job)
        else:
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _prune_locked(self) -> None:
        """Drop the oldest finished jobs once the cap is exceeded.

        Callers hold `self._lock`. Insertion order is oldest-first, so pruning
        the first terminal jobs keeps the most recent history for the UI.
        """
        try:
            cap = int(os.environ.get("YARD_JOBS_MAX", "") or _MAX_JOBS)
        except ValueError:
            cap = _MAX_JOBS
        overflow = len(self._jobs) - cap
        if overflow <= 0:
            return
        for job in list(self._jobs.values()):
            if overflow <= 0:
                break
            if job.state in _TERMINAL:
                self._jobs.pop(job.id, None)
                overflow -= 1

    def _run(self, job: Job) -> None:
        job.set_state("running")
        try:
            self._execute(self.root, job)
            job.set_state("ok")
        except JobCancelled as e:
            job.append(str(e) or "cancelled")
            job.set_state("cancelled")
        except Exception as e:
            job.append(str(e))
            job.set_state("error")
        finally:
            job.done.set()


def _ticket_scope(job: Job) -> set[str] | None:
    if job.action not in _TICKET_ACTIONS:
        return None
    if not job.ticket_ids:
        return None
    return set(job.ticket_ids)


def _jobs_conflict(
    running: Job,
    jira: str,
    action: str,
    ticket_ids: list[str] | None,
    extra: dict | None = None,
) -> bool:
    if running.jira != jira:
        return False
    # A re-run never serialises at the job layer: `req_test` holds a run lock, so
    # the job just queues behind whatever run is active.
    if action == "run-test" and extra is not None and extra.get("rerun_cases"):
        if running.action == "run-test":
            return False
    running_scope = _ticket_scope(running)
    incoming_scope = (
        None
        if action not in _TICKET_ACTIONS or not ticket_ids
        else set(ticket_ids)
    )
    if running_scope is None or incoming_scope is None:
        return True
    return bool(running_scope & incoming_scope)


def _conflict_message(running: Job, action: str, ticket_ids: list[str] | None) -> str:
    overlap = ""
    running_scope = _ticket_scope(running)
    incoming_scope = (
        None
        if action not in _TICKET_ACTIONS or not ticket_ids
        else set(ticket_ids)
    )
    if running_scope and incoming_scope:
        shared = running_scope & incoming_scope
        if shared:
            overlap = " " + ", ".join(sorted(shared))
    return f"{running.jira}{overlap} already has a running job ({running.action})"
