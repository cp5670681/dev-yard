from __future__ import annotations

import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dev_yard import grill_round, paths, service
from dev_yard.runners import RunResult, Runner, pi_argv

Execute = Callable[[Path, "Job"], None]


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
    done: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _input: threading.Event = field(default_factory=threading.Event)
    _answers: list[dict[str, Any]] | None = None

    def append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self.log += text if text.endswith("\n") else text + "\n"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "jira": self.jira,
                "action": self.action,
                "state": self.state,
                "log": self.log,
                "ticket_ids": self.ticket_ids,
                "grill": self.grill,
            }

    def set_waiting(self, payload: dict) -> None:
        with self._lock:
            self._input.clear()
            self._answers = None
            self.state = "waiting"
            self.grill = payload

    def submit_answers(self, answers: list[dict[str, Any]]) -> None:
        if not isinstance(answers, list):
            raise ValueError("answers must be a list")
        with self._lock:
            if self.state != "waiting":
                raise ValueError("job is not waiting for answers")
            self._answers = answers
            self.state = "running"
            self.grill = None
            self._input.set()

    def wait_answers(self) -> list[dict[str, Any]]:
        self._input.wait()
        with self._lock:
            answers = self._answers
            self._answers = None
            if answers is None:
                raise RuntimeError("grill interrupted")
            return answers


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
        summary = raw.strip()[:4000] or f"pi exit {code}"
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
    bundle = {
        "grill": "grill",
        "spec": "spec",
        "tickets": "tickets",
        "implement": "implement",
        "review": "review",
        "contract": "review",
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
    if job.action == "implement":
        ran = service.implement(
            root, job.jira, job.ticket_ids, print_mode=True, runner=runner
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

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def latest(self, jira: str) -> Job | None:
        matches = [j for j in self._jobs.values() if j.jira == jira]
        return matches[-1] if matches else None

    def for_page(self, jira: str, job_id: str | None) -> Job | None:
        if job_id:
            return self.get(job_id)
        latest = self.latest(jira)
        if latest and latest.state in {"queued", "running", "waiting"}:
            return latest
        return None

    def running(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.state in {"queued", "running", "waiting"}]

    def submit(
        self,
        action: str,
        jira: str,
        ticket_ids: list[str] | None = None,
        extra: dict | None = None,
    ) -> Job:
        with self._lock:
            for job in self._jobs.values():
                if job.jira == jira and job.state in {"queued", "running", "waiting"}:
                    raise ValueError(f"{jira} already has a running job ({job.action})")
            job = Job(
                id=uuid.uuid4().hex[:10],
                jira=jira,
                action=action,
                ticket_ids=ticket_ids,
                extra=extra or {},
            )
            self._jobs[job.id] = job
        if self.sync:
            self._run(job)
        else:
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        job.state = "running"
        try:
            self._execute(self.root, job)
            job.state = "ok"
        except Exception as e:
            job.state = "error"
            job.append(str(e))
        finally:
            job.done.set()
