import threading
from pathlib import Path

import pytest

from dev_yard.runners import RunResult
from dev_yard.service import init_yard, repo_add, req_open
from dev_yard.web.jobs import BoardSse, Job, JobRunner, JobSse, default_execute


def _parse_sse(body: str) -> list[tuple[str, object]]:
    import json

    events: list[tuple[str, object]] = []
    for block in body.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event = "message"
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            events.append((event, json.loads("\n".join(data))))
    return events


def test_wait_seq_unblocks_on_append():
    job = Job(id="abc", jira="AB-1", action="open")
    seq = job.current_seq()
    hit = threading.Event()

    def wait():
        job.wait_seq(seq, timeout=2)
        hit.set()

    threading.Thread(target=wait, daemon=True).start()
    assert not hit.wait(0.05)
    job.append("x")
    assert hit.wait(1)


def test_wait_board_unblocks_when_job_waits(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        job.set_waiting({"round": 1, "questions": [{"id": "Q1"}]})
        job.wait_answers()
        gate.set()

    runner = JobRunner(yard, execute=execute, sync=False)
    seq = runner.board_seq()
    hit = threading.Event()

    def wait():
        runner.wait_board(seq, timeout=2)
        hit.set()

    threading.Thread(target=wait, daemon=True).start()
    job = runner.submit("grill", "AB-1")
    assert hit.wait(1)
    deadline = __import__("time").time() + 2
    while job.state != "waiting" and __import__("time").time() < deadline:
        __import__("time").sleep(0.01)
    briefs = runner.running_brief()
    assert briefs[0]["jira"] == "AB-1"
    assert briefs[0]["state"] == "waiting"
    assert "log" not in briefs[0]
    job.submit_answers([{"id": "Q1", "option": "A", "text": ""}])
    assert job.done.wait(timeout=2)
    assert gate.is_set()
    assert runner.running_brief() == []


def test_board_sse_poll_captures_seq_before_items():
    class Fake:
        def __init__(self) -> None:
            self.seq = 1
            self.items = [{"id": "a", "jira": "AB-1", "action": "grill", "state": "running"}]

        def board_seq(self) -> int:
            return self.seq

        def running_brief(self) -> list[dict]:
            self.seq += 1
            return self.items

    frames, seq = BoardSse(Fake()).poll()
    assert seq == 1
    events = _parse_sse("".join(frames))
    assert events[0][0] == "jobs"
    assert events[0][1][0]["id"] == "a"


def test_board_sse_poll_emits_waiting_then_empty(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        job.set_waiting({"round": 1, "questions": [{"id": "Q1"}]})
        job.wait_answers()
        gate.set()

    runner = JobRunner(yard, execute=execute, sync=False)
    sse = BoardSse(runner)
    frames, _seq = sse.poll()
    assert _parse_sse("".join(frames)) == [("jobs", [])]
    assert sse.poll()[0] == []
    job = runner.submit("grill", "AB-1")
    deadline = __import__("time").time() + 2
    while job.state != "waiting" and __import__("time").time() < deadline:
        __import__("time").sleep(0.01)
    frames, _seq = sse.poll()
    events = _parse_sse("".join(frames))
    assert events[0][0] == "jobs"
    assert events[0][1][0]["state"] == "waiting"
    assert events[0][1][0]["jira"] == "AB-1"
    job.submit_answers([{"id": "Q1", "option": "A", "text": ""}])
    assert job.done.wait(timeout=2)
    frames, _seq = sse.poll()
    assert _parse_sse("".join(frames)) == [("jobs", [])]
    assert gate.is_set()


def test_sse_poll_finished_job_snapshot_and_done():
    job = Job(id="abc", jira="AB-1", action="grill")
    job.append("hello")
    job.set_state("ok")
    frames, done, _seq = JobSse(job).poll()
    assert done is True
    events = _parse_sse("".join(frames))
    assert [name for name, _ in events] == ["snapshot", "done"]
    assert events[0][1]["log"] == "hello\n"
    assert events[0][1]["state"] == "ok"
    assert "seq" not in events[0][1]


def test_sse_poll_emits_log_delta_then_state():
    job = Job(id="abc", jira="AB-1", action="grill")
    job.set_state("running")
    sse = JobSse(job)
    frames, done, _seq = sse.poll()
    assert done is False
    events = _parse_sse("".join(frames))
    assert events[0][0] == "snapshot"
    assert events[0][1]["state"] == "running"

    job.append("line-a")
    frames, done, _seq = sse.poll()
    assert done is False
    assert _parse_sse("".join(frames)) == [("log", "line-a\n")]

    job.set_waiting({"round": 1, "questions": [{"id": "Q1"}]})
    frames, done, _seq = sse.poll()
    assert done is False
    events = _parse_sse("".join(frames))
    assert events[0][0] == "state"
    assert events[0][1]["state"] == "waiting"
    assert events[0][1]["grill"]["round"] == 1
    assert "log" not in events[0][1]


def test_submit_runs_sync_and_logs(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)

    def execute(root: Path, job) -> None:
        job.append("hello from " + job.action)

    runner = JobRunner(yard, execute=execute, sync=True)
    job = runner.submit("grill", "AB-1")
    assert job.state == "ok"
    assert "hello from grill" in job.log
    assert runner.get(job.id) is job
    assert runner.latest("AB-1") is job


def test_execute_error_marks_job(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)

    def execute(root: Path, job) -> None:
        raise ValueError("no tickets")

    runner = JobRunner(yard, execute=execute, sync=True)
    job = runner.submit("freeze", "AB-1")
    assert job.state == "error"
    assert "no tickets" in job.log


def test_rejects_second_job_for_same_jira(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        gate.wait(timeout=5)

    runner = JobRunner(yard, execute=execute, sync=False)
    first = runner.submit("grill", "AB-1")
    with pytest.raises(ValueError, match="already"):
        runner.submit("spec", "AB-1")
    gate.set()
    first.done.wait(timeout=5)
    assert first.state == "ok"


def test_allows_parallel_implement_jobs_for_different_tickets(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    gate = threading.Event()
    started = threading.Event()

    def execute(root: Path, job) -> None:
        started.set()
        gate.wait(timeout=5)

    runner = JobRunner(yard, execute=execute, sync=False)
    first = runner.submit("implement", "AB-1", ticket_ids=["T1"])
    second = runner.submit("implement", "AB-1", ticket_ids=["T2"])
    assert started.wait(timeout=5)
    assert {first.id, second.id} == {j.id for j in runner.running()}
    with pytest.raises(ValueError, match="already"):
        runner.submit("implement", "AB-1", ticket_ids=["T1"])
    with pytest.raises(ValueError, match="already"):
        runner.submit("grill", "AB-1")
    gate.set()
    assert first.done.wait(timeout=5)
    assert second.done.wait(timeout=5)


def test_open_job_errors_when_pi_skips_requirement(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)

    class Result:
        ok = True
        summary = "pi exit 0"
        exit_code = 0

    class Fake:
        def start(self, prompt, cwd, extra_read_paths):
            return Result()

    monkeypatch.setattr("dev_yard.web.jobs.JobLogRunner", lambda *a, **k: Fake())
    job = JobRunner(yard, execute=default_execute, sync=True).submit(
        "open", "AB-23", extra={"source": "pi"}
    )
    assert job.state == "error"
    assert "did not write REQUIREMENT.md" in job.log


def test_default_execute_open_and_freeze(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    runner = JobRunner(yard, execute=default_execute, sync=True)
    opened = runner.submit("open", "AB-20", extra={"source": "none"})
    assert opened.state == "ok"
    d = yard / "reqs" / "AB-20"
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    frozen = runner.submit("freeze", "AB-20")
    assert frozen.state == "ok"
    assert (d / "worktrees" / "backend").exists()


def test_default_execute_grill_uses_print_mode(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-21", source="none")
    seen: dict = {}

    class FakeLog:
        def __init__(self, job, root, bundle):
            self.job = job

        def start(self, prompt, cwd, extra_read_paths):
            self.job.append("grilled-stream")
            return RunResult(ok=True, summary="grilled-stream")

    def fake_launch(root, name, jira, dry_run=False, print_mode=False, runner=None, prompt_extra=""):
        seen.update(
            name=name,
            print_mode=print_mode,
            runner_type=type(runner).__name__,
            prompt_extra=prompt_extra,
        )
        return runner.start("p", root, [])

    monkeypatch.setattr("dev_yard.web.jobs.JobLogRunner", FakeLog)
    monkeypatch.setattr("dev_yard.web.jobs.service.launch_skill", fake_launch)
    job = JobRunner(yard, execute=default_execute, sync=True).submit("grill", "AB-21")
    assert job.state == "ok"
    assert seen["name"] == "grill"
    assert seen["print_mode"] is True
    assert seen["runner_type"] == "FakeLog"
    assert "WEB_GRILL_ROUND" in seen["prompt_extra"]
    assert job.log.count("grilled-stream") == 1
    assert "grill finished" in job.log


def test_default_execute_implement_injects_runner(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-22", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    from dev_yard.service import req_freeze

    req_freeze(yard, "AB-22")

    captured: dict = {}

    def fake_implement(root, jira, ids, dry_run=False, print_mode=False, runner=None):
        captured.update(print_mode=print_mode, runner=runner)
        return ["T1"]

    monkeypatch.setattr("dev_yard.web.jobs.service.implement", fake_implement)
    job = JobRunner(yard, execute=default_execute, sync=True).submit("implement", "AB-22")
    assert job.state == "ok"
    assert captured["print_mode"] is True
    assert captured["runner"] is not None
    assert "T1" in job.log


def test_web_grill_waits_then_records_answers(tmp_path: Path, monkeypatch):
    import json
    import time

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-50", source="none")
    calls = {"n": 0}

    class FakeLog:
        def __init__(self, job, root, bundle):
            self.job = job

        def start(self, prompt, cwd, extra_read_paths):
            self.job.append(f"pi-round-{calls['n']}")
            return RunResult(ok=True, summary="ok")

    def fake_launch(root, name, jira, dry_run=False, print_mode=False, runner=None, prompt_extra=""):
        calls["n"] += 1
        req = root / "reqs" / jira
        if calls["n"] == 1:
            (req / ".grill-round.json").write_text(
                json.dumps(
                    {
                        "done": False,
                        "round": 1,
                        "intro": "范围",
                        "questions": [
                            {
                                "id": "Q1",
                                "title": "范围",
                                "body": "只做这张票？",
                                "options": [
                                    {"id": "A", "label": "只这张票"},
                                    {"id": "B", "label": "整条史诗"},
                                ],
                                "suggested": "A",
                                "suggested_text": "只这张票",
                            }
                        ],
                    }
                )
            )
        else:
            (req / ".grill-round.json").write_text(
                json.dumps({"done": True, "round": 2, "questions": []})
            )
        return runner.start("p", root, [])

    monkeypatch.setattr("dev_yard.web.jobs.JobLogRunner", FakeLog)
    monkeypatch.setattr("dev_yard.web.jobs.service.launch_skill", fake_launch)
    runner = JobRunner(yard, execute=default_execute, sync=False)
    job = runner.submit("grill", "AB-50")
    deadline = time.time() + 5
    while job.state != "waiting" and time.time() < deadline:
        time.sleep(0.05)
    assert job.state == "waiting"
    snap = job.snapshot()
    assert snap["grill"]["questions"][0]["suggested"] == "A"
    with pytest.raises(ValueError, match="already"):
        runner.submit("spec", "AB-50")
    job.submit_answers([{"id": "Q1", "option": "A", "text": ""}])
    assert job.done.wait(timeout=5)
    assert job.state == "ok"
    assert calls["n"] == 2
    grill = (yard / "reqs" / "AB-50" / "GRILL.md").read_text()
    assert "选 A — 只这张票" in grill
    assert not (yard / "reqs" / "AB-50" / ".grill-round.json").exists()
    assert "grill finished" in job.log


def _write_pending_round(req: Path, round_n: int = 2) -> None:
    import json

    (req / "GRILL.md").write_text("# Grill\n\n## Round 1 — answers\n\n- **Q1**：选 A\n")
    (req / ".grill-round.json").write_text(
        json.dumps(
            {
                "done": False,
                "round": round_n,
                "intro": "继续",
                "questions": [
                    {
                        "id": "Q1",
                        "title": "范围",
                        "options": [{"id": "A", "label": "只这张票"}],
                        "suggested": "A",
                        "suggested_text": "只这张票",
                    }
                ],
            }
        )
    )


def test_web_grill_resumes_pending_round_before_pi(tmp_path: Path, monkeypatch):
    import time

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-51", source="none")
    _write_pending_round(d)
    calls = {"n": 0}

    class FakeLog:
        def __init__(self, job, root, bundle):
            self.job = job

        def start(self, prompt, cwd, extra_read_paths):
            self.job.append("pi-after-resume")
            return RunResult(ok=True, summary="ok")

    def fake_launch(root, name, jira, dry_run=False, print_mode=False, runner=None, prompt_extra=""):
        calls["n"] += 1
        req = root / "reqs" / jira
        leftover = req / ".grill-round.json"
        if leftover.exists():
            leftover.unlink()
        (req / ".grill-round.json").write_text('{"done": true, "round": 3, "questions": []}')
        return runner.start("p", root, [])

    monkeypatch.setattr("dev_yard.web.jobs.JobLogRunner", FakeLog)
    monkeypatch.setattr("dev_yard.web.jobs.service.launch_skill", fake_launch)
    runner = JobRunner(yard, execute=default_execute, sync=False)
    job = runner.submit("grill", "AB-51")
    deadline = time.time() + 5
    while job.state != "waiting" and time.time() < deadline:
        time.sleep(0.05)
    assert job.state == "waiting"
    assert calls["n"] == 0
    assert job.snapshot()["grill"]["round"] == 2
    job.submit_answers([{"id": "Q1", "option": "A", "text": ""}])
    assert job.done.wait(timeout=5)
    assert job.state == "ok"
    assert calls["n"] == 1
    assert "选 A — 只这张票" in (d / "GRILL.md").read_text()


def test_resume_pending_grills_restores_waiting_jobs(tmp_path: Path, monkeypatch):
    import time

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-52", source="none")
    _write_pending_round(d)

    def fake_launch(*args, **kwargs):
        raise AssertionError("pi should not start until answers are submitted")

    monkeypatch.setattr("dev_yard.web.jobs.service.launch_skill", fake_launch)
    runner = JobRunner(yard, execute=default_execute, sync=False)
    restored = runner.resume_pending_grills()
    assert len(restored) == 1
    job = restored[0]
    deadline = time.time() + 5
    while job.state != "waiting" and time.time() < deadline:
        time.sleep(0.05)
    assert job.state == "waiting"
    assert job.jira == "AB-52"
    assert job.action == "grill"
    assert job.snapshot()["grill"]["questions"][0]["id"] == "Q1"
    briefs = runner.running_brief()
    assert briefs[0]["jira"] == "AB-52"
    assert runner.resume_pending_grills() == []


def test_resume_pending_grills_skips_markdown_frontier(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-54", source="none")
    (d / "GRILL.md").write_text(
        "# Grill\n\n## Round 1 — frontier\n\n❓ **Q1** - **范围**：只做这张票？\n"
        "- 选 A：只这张票\n\n➡️ 选 A。\n"
    )

    def fake_launch(*args, **kwargs):
        raise AssertionError("CLI markdown frontier must not auto-start a web job")

    monkeypatch.setattr("dev_yard.web.jobs.service.launch_skill", fake_launch)
    runner = JobRunner(yard, execute=default_execute, sync=False)
    assert runner.resume_pending_grills() == []
    assert runner.running_brief() == []
