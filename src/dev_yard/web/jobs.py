from __future__ import annotations

import json
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dev_yard import grill_round, paths, service
from dev_yard.pi_session import load_conversation
from dev_yard.runners import RunResult, Runner, clip_summary, pi_argv

Execute = Callable[[Path, "Job"], None]
_TERMINAL = {"ok", "error"}
_TICKET_ACTIONS = {"implement", "review", "fix-contract", "fix-test"}


def format_sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@dataclass
class Job:
    id: str
    jira: str
    action: str
    state: str = "queued"
    log: str = ""
    ticket_ids: list[str] | None = None
    extra: dict = field(default_factory=dict)
    grill: dict | None = None
    pi_runs: list[dict[str, Any]] = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _cv: threading.Condition = field(init=False, repr=False, compare=False)
    _seq: int = field(default=0, repr=False, compare=False)
    _input: threading.Event = field(default_factory=threading.Event)
    _answers: list[dict[str, Any]] | None = None
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
            "grill": self.grill,
            "pi_runs": list(self.pi_runs),
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
            return {k: data[k] for k in ("id", "jira", "action", "state", "ticket_ids")}

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
                raise RuntimeError("grill interrupted")
            return answers

    def record_pi_run(self, cwd: Path) -> dict[str, Any]:
        run = {
            "cwd": str(cwd.resolve()),
            "started_at": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
        with self._cv:
            run = {"index": len(self.pi_runs), **run}
            self.pi_runs.append(run)
            self._bump()
            return dict(run)


class JobSse:
    def __init__(self, job: Job) -> None:
        self.job = job
        self._log_off = 0
        self._sent_snapshot = False
        self._last_state: str | None = None
        self._last_grill: Any = object()
        self._last_pi_runs: Any = object()

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
        else:
            if len(snap["log"]) > self._log_off:
                frames.append(format_sse("log", snap["log"][self._log_off :]))
                self._log_off = len(snap["log"])
            if (
                snap["state"] != self._last_state
                or snap["grill"] != self._last_grill
                or snap.get("pi_runs") != self._last_pi_runs
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
                        },
                    )
                )
                self._last_state = snap["state"]
                self._last_grill = snap["grill"]
                self._last_pi_runs = snap.get("pi_runs")
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
    def __init__(self, job: Job, root: Path, bundle: str) -> None:
        self.job = job
        self.root = root
        self.bundle = bundle

    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path]) -> RunResult:
        argv = pi_argv(root=self.root, bundle=self.bundle, prompt=prompt, print_mode=True)
        binary = argv[0]
        if not shutil.which(binary) and not Path(binary).exists():
            msg = f"pi not found (`{binary}`). Install pi or set YARD_PI to its path."
            self.job.append(msg)
            return RunResult(ok=False, summary=msg, exit_code=127)
        self.job.record_pi_run(cwd)
        self.job.append(f"$ {binary} -p …  cwd={cwd}")
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        chunks: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            self.job.append(line if line.endswith("\n") else line + "\n")
        code = proc.wait()
        raw = "".join(chunks)
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
            force=bool(extra.get("force")),
            on_progress=job.append if source != "pi" else None,
            runner=runner,
        )
        job.append(str(dest))
        if warning:
            job.append(warning)
        return
    if job.action == "freeze":
        created = service.req_freeze(root, job.jira)
        for p in created:
            job.append(str(p))
        return
    if job.action == "submit-test":
        from dev_yard.test_report import submit_test

        data = submit_test(root, job.jira)
        job.append(f"{job.jira} phase={data.get('phase')}")
        return
    bundle = {
        "grill": "grill",
        "spec": "spec",
        "tickets": "tickets",
        "implement": "implement",
        "review": "review",
        "contract": "review",
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
        result = service.launch_skill(
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
            result = service.launch_skill(
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
    def __init__(self, runner: "JobRunner") -> None:
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
        return self._jobs.get(job_id)

    def latest(self, jira: str) -> Job | None:
        matches = [j for j in self._jobs.values() if j.jira == jira]
        return matches[-1] if matches else None

    def for_page(self, jira: str, job_id: str | None) -> Job | None:
        jobs = self.page_jobs(jira, job_id)
        return jobs[0] if jobs else None

    def page_jobs(self, jira: str, job_id: str | None) -> list[Job]:
        running = [j for j in self.running() if j.jira == jira]
        if not job_id:
            return running
        picked = self.get(job_id)
        if picked is None:
            return running
        rest = [j for j in running if j.id != picked.id]
        return [picked] + rest

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
        return [j for j in self._jobs.values() if j.state in {"queued", "running", "waiting"}]

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
            for job in self._jobs.values():
                if job.state not in {"queued", "running", "waiting"}:
                    continue
                if _jobs_conflict(job, jira, action, ticket_ids):
                    raise ValueError(_conflict_message(job, action, ticket_ids))
            job = Job(
                id=uuid.uuid4().hex[:10],
                jira=jira,
                action=action,
                ticket_ids=ticket_ids,
                extra=extra or {},
                on_change=self._bump_board,
            )
            self._jobs[job.id] = job
        self._bump_board()
        if self.sync:
            self._run(job)
        else:
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        job.set_state("running")
        try:
            self._execute(self.root, job)
            job.set_state("ok")
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
    running: Job, jira: str, action: str, ticket_ids: list[str] | None
) -> bool:
    if running.jira != jira:
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
