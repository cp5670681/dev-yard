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


def test_cancel_waiting_grill_job_interrupts_wait():
    import time

    job = Job(id="abc", jira="AB-1", action="grill")
    job.set_waiting({"round": 1, "questions": [{"id": "Q1"}]})
    errors: list[Exception] = []

    def wait():
        try:
            job.wait_answers()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threading.Thread(target=wait, daemon=True).start()
    time.sleep(0.05)
    job.cancel()
    deadline = time.time() + 2
    while not errors and time.time() < deadline:
        time.sleep(0.01)
    assert len(errors) == 1
    from dev_yard.web.jobs import JobCancelled

    assert isinstance(errors[0], JobCancelled)
    assert "grill cancelled" in str(errors[0])


def test_cancel_is_noop_on_terminal_job():
    job = Job(id="abc", jira="AB-1", action="open")
    job.set_state("ok")
    job.cancel()
    job.cancel()
    assert job.state == "ok"


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


def test_runner_cancel_marks_job_cancelled(tmp_path: Path):
    import time

    from dev_yard.web.jobs import JobCancelled

    yard = tmp_path / "yard"
    init_yard(yard)

    def execute(root: Path, job) -> None:
        while not job.cancel_requested.is_set():
            time.sleep(0.01)
        raise JobCancelled("implement cancelled (pi exit -9)")

    runner = JobRunner(yard, execute=execute, sync=False)
    job = runner.submit("implement", "AB-1", ticket_ids=["T1"])
    deadline = time.time() + 2
    while job.state != "running" and time.time() < deadline:
        time.sleep(0.01)
    assert runner.cancel(job.id) is job
    assert job.done.wait(timeout=2)
    assert job.state == "cancelled"
    assert runner.running() == []
    assert runner.running_brief() == []
    assert "cancelled" in job.log


def test_runner_cancel_unknown_job(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    assert JobRunner(yard).cancel("nope") is None


def test_sse_done_frame_for_cancelled_job():
    job = Job(id="abc", jira="AB-1", action="implement")
    job.set_state("cancelled")
    frames, done, _seq = JobSse(job).poll()
    assert done is True
    assert _parse_sse("".join(frames))[-1][0] == "done"


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
    with pytest.raises(ValueError, match="already"):
        runner.submit("review", "AB-1", ticket_ids=["T1"])
    with pytest.raises(ValueError, match="already"):
        runner.submit("fix-contract", "AB-1", ticket_ids=["T1"])
    busy = runner.busy_tickets("AB-1", "review")
    assert busy is not None
    assert "T1" in busy
    assert "T2" in busy
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
        def start(self, prompt, cwd, extra_read_paths, repo=None):
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

        def start(self, prompt, cwd, extra_read_paths, repo=None):
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
    monkeypatch.setattr("dev_yard.web.jobs.service.run_stage", fake_launch)
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

    def fake_implement(
        root, jira, ids, dry_run=False, print_mode=False, runner=None, from_contract=False, from_test=False, **_kw
    ):
        captured.update(
            print_mode=print_mode,
            runner=runner,
            from_contract=from_contract,
            from_test=from_test,
        )
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

        def start(self, prompt, cwd, extra_read_paths, repo=None):
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
                                "why_ask": "范围会决定票的拆分，属产品意图级",
                                "evidence": "REQUIREMENT.md 未写范围，代码里也查不到",
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
    monkeypatch.setattr("dev_yard.web.jobs.service.run_stage", fake_launch)
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


def test_web_grill_drops_unjustified_questions(tmp_path: Path, monkeypatch):
    import json

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-52", source="none")
    calls = {"n": 0}

    class FakeLog:
        def __init__(self, job, root, bundle):
            self.job = job

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.job.append(f"pi-round-{calls['n']}")
            return RunResult(ok=True, summary="ok")

    def fake_launch(root, name, jira, dry_run=False, print_mode=False, runner=None, prompt_extra=""):
        calls["n"] += 1
        req = root / "reqs" / jira
        (req / ".grill-round.json").write_text(
            json.dumps(
                {
                    "done": False,
                    "round": 1,
                    "questions": [{"id": "Q1", "title": "明知故问"}],
                }
            )
        )
        return runner.start("p", root, [])

    monkeypatch.setattr("dev_yard.web.jobs.JobLogRunner", FakeLog)
    monkeypatch.setattr("dev_yard.web.jobs.service.run_stage", fake_launch)
    runner = JobRunner(yard, execute=default_execute, sync=False)
    job = runner.submit("grill", "AB-52")
    assert job.done.wait(timeout=5)
    assert job.state == "ok"
    assert calls["n"] == 1
    assert not (yard / "reqs" / "AB-52" / ".grill-round.json").exists()
    assert "dropped 1 unjustified question(s): Q1 明知故问" in job.log
    assert "grill finished" in job.log


def test_web_grill_caps_rounds_without_error(tmp_path: Path, monkeypatch):
    import json

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-54", source="none")
    (d / "GRILL.md").write_text("# Grill — AB-54\n\n## Round 3 — answers\n\n- **Q1**：选 A\n")
    (d / ".grill-round.json").write_text(
        json.dumps(
            {
                "done": False,
                "round": 4,
                "questions": [
                    {
                        "id": "Q1",
                        "title": "越界轮",
                        "options": [{"id": "A", "label": "x"}],
                        "why_ask": "产品意图级",
                        "evidence": "查过需求与源码，均无",
                    }
                ],
            }
        )
    )

    def fake_launch(*args, **kwargs):
        raise AssertionError("pi should not start when at the round cap")

    monkeypatch.setattr("dev_yard.web.jobs.service.run_stage", fake_launch)
    runner = JobRunner(yard, execute=default_execute, sync=False)
    job = runner.submit("grill", "AB-54")
    assert job.done.wait(timeout=5)
    assert job.state == "ok"
    assert "round cap reached" in job.log
    assert not (d / ".grill-round.json").exists()


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
                        "why_ask": "范围会决定票的拆分，属产品意图级",
                        "evidence": "REQUIREMENT.md 未写范围，代码里也查不到",
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

        def start(self, prompt, cwd, extra_read_paths, repo=None):
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
    monkeypatch.setattr("dev_yard.web.jobs.service.run_stage", fake_launch)
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

    monkeypatch.setattr("dev_yard.web.jobs.service.run_stage", fake_launch)
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


def test_snapshot_includes_recorded_pi_runs(tmp_path: Path):
    job = Job(id="abc", jira="AB-1", action="open")
    cwd = tmp_path / "wt"
    cwd.mkdir()
    run = job.record_pi_run(cwd)
    snap = job.snapshot()
    assert run["index"] == 0
    assert snap["pi_runs"][0]["cwd"] == str(cwd.resolve())
    assert snap["pi_runs"][0]["started_at"]
    assert "log" in snap


def test_job_log_runner_records_pi_run_before_popen(tmp_path: Path, monkeypatch):
    job = Job(id="abc", jira="AB-1", action="open")
    cwd = tmp_path / "work"
    cwd.mkdir()

    class FakeStdin:
        def write(self, data):
            self.data = data

        def close(self):
            return None

    class FakeProc:
        stdout = iter(["hello\n"])
        stdin = FakeStdin()

        def wait(self):
            return 0

    captured: dict = {}

    def fake_popen(*a, **k):
        captured["argv"] = a[0] if a else k.get("args")
        captured["stdin"] = k.get("stdin")
        return FakeProc()

    monkeypatch.setattr("dev_yard.web.jobs.shutil.which", lambda b: "/usr/bin/pi")
    monkeypatch.setattr("dev_yard.runners.subprocess.Popen", fake_popen)
    monkeypatch.setattr("dev_yard.web.jobs.pi_argv", lambda **k: ["pi", "-p"])
    from dev_yard.web.jobs import JobLogRunner

    result = JobLogRunner(job, tmp_path, "open").start("p", cwd, [])
    assert result.ok
    runs = job.snapshot()["pi_runs"]
    assert len(runs) == 1
    assert runs[0]["cwd"] == str(cwd.resolve())
    assert "cwd=" in job.log
    import subprocess

    assert captured["argv"] == ["pi", "-p"]
    assert captured["stdin"] is subprocess.PIPE


def test_run_pi_print_calls_on_spawn_with_proc(tmp_path: Path, monkeypatch):
    spawned: list = []

    class FakeStdin:
        def write(self, data):
            self.data = data

        def close(self):
            return None

    class FakeProc:
        stdout = iter(["line\n"])
        stdin = FakeStdin()

        def wait(self):
            return 0

    monkeypatch.setattr("dev_yard.runners.subprocess.Popen", lambda *a, **k: FakeProc())
    from dev_yard.runners import run_pi_print

    code, raw = run_pi_print(["pi"], tmp_path, "p", on_spawn=spawned.append)
    assert code == 0
    assert raw == "line\n"
    assert len(spawned) == 1
    assert isinstance(spawned[0], FakeProc)


def test_run_pi_print_tracked_reaps_even_on_error(tmp_path: Path, monkeypatch):
    from dev_yard.runners import run_pi_print_tracked

    class FakeStdin:
        def write(self, data):
            return None

        def close(self):
            return None

    class FakeProc:
        stdout = iter(["line\n"])
        stdin = FakeStdin()

        def wait(self):
            return 0

    spawned: list = []
    reaped: list = []
    monkeypatch.setattr("dev_yard.runners.subprocess.Popen", lambda *a, **k: FakeProc())
    code, raw = run_pi_print_tracked(
        ["pi"], tmp_path, "p", on_spawn=spawned.append, on_reap=reaped.append
    )
    assert (code, raw) == (0, "line\n")
    assert reaped == spawned

    # A raising run_pi_print must still reap the proc it spawned.
    def boom(argv, cwd, prompt, on_line=None, timeout=None, on_spawn=None):
        if on_spawn is not None:
            on_spawn(object())
        raise RuntimeError("kaboom")

    monkeypatch.setattr("dev_yard.runners.run_pi_print", boom)
    with pytest.raises(RuntimeError):
        run_pi_print_tracked(
            ["pi"], tmp_path, "p", on_spawn=spawned.append, on_reap=reaped.append
        )
    assert len(spawned) == 2
    assert reaped == spawned


def test_job_log_runner_registers_proc_on_job(tmp_path: Path, monkeypatch):
    job = Job(id="abc", jira="AB-1", action="open")
    cwd = tmp_path / "work"
    cwd.mkdir()

    class FakeStdin:
        def write(self, data):
            return None

        def close(self):
            return None

    class FakeProc:
        stdout = iter(["hello\n"])
        stdin = FakeStdin()

        def wait(self):
            return 0

    monkeypatch.setattr("dev_yard.web.jobs.shutil.which", lambda b: "/usr/bin/pi")
    monkeypatch.setattr("dev_yard.runners.subprocess.Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr("dev_yard.web.jobs.pi_argv", lambda **k: ["pi", "-p"])
    from dev_yard.web.jobs import JobLogRunner

    registered: list[object] = []
    original = job.track_proc

    def spy(proc):
        registered.append(proc)
        original(proc)

    job.track_proc = spy
    JobLogRunner(job, tmp_path, "open").start("p", cwd, [])
    assert len(registered) == 1
    assert isinstance(registered[0], FakeProc)
    # The finished proc must not linger for a later cancel to hit.
    with job._lock:
        assert job._procs == set()


def test_job_log_runner_raises_when_cancelled_before_start(tmp_path: Path):
    from dev_yard.web.jobs import JobCancelled, JobLogRunner

    job = Job(id="abc", jira="AB-1", action="open")
    job.cancel()
    with pytest.raises(JobCancelled):
        JobLogRunner(job, tmp_path, "open").start("p", tmp_path, [])


def test_job_log_runner_raises_jobcancelled_when_pi_killed(tmp_path: Path, monkeypatch):
    import time

    from dev_yard.web.jobs import JobCancelled, JobLogRunner

    script = tmp_path / "fakepi"
    script.write_text("#!/bin/sh\necho started\nsleep 30\n")
    script.chmod(0o755)
    monkeypatch.setattr("dev_yard.web.jobs.pi_argv", lambda **k: [str(script), "-p"])
    job = Job(id="abc", jira="AB-1", action="open")
    cwd = tmp_path / "work"
    cwd.mkdir()
    outcome: dict = {}

    def run_start():
        try:
            JobLogRunner(job, tmp_path, "open").start("p", cwd, [])
        except Exception as e:  # noqa: BLE001
            outcome["err"] = e

    t = threading.Thread(target=run_start, daemon=True)
    t.start()
    deadline = time.time() + 5
    while not job._procs and time.time() < deadline:
        time.sleep(0.01)
    assert job._procs, "pi subprocess was never registered"
    job.cancel()
    t.join(timeout=5)
    assert not t.is_alive(), "start() did not return after the subprocess was killed"
    assert isinstance(outcome.get("err"), JobCancelled)


def test_sse_poll_emits_pi_runs_on_state(tmp_path: Path):
    job = Job(id="abc", jira="AB-1", action="grill")
    job.set_state("running")
    sse = JobSse(job)
    frames, done, _seq = sse.poll()
    assert done is False
    cwd = tmp_path / "w"
    cwd.mkdir()
    job.record_pi_run(cwd)
    frames, done, _seq = sse.poll()
    events = _parse_sse("".join(frames))
    assert events[0][0] == "state"
    assert events[0][1]["pi_runs"][0]["index"] == 0
    assert "log" not in events[0][1]


def test_pi_chat_sse_emits_entries_then_done(tmp_path: Path):
    import json

    from dev_yard.web.jobs import PiChatSse

    job = Job(id="abc", jira="AB-1", action="spec")
    cwd = tmp_path / "yard"
    cwd.mkdir()
    job.record_pi_run(cwd)
    job.set_state("ok")
    sessions = tmp_path / "sessions"
    path = sessions / "dir" / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "type": "session",
                "version": 3,
                "id": "sid",
                "timestamp": "2099-01-01T00:00:00.000Z",
                "cwd": str(cwd.resolve()),
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                },
            }
        )
        + "\n"
    )
    sse = PiChatSse(job, 0, cwd, sessions_dir=sessions)
    frames, done, _seq = sse.poll()
    assert done is True
    events = _parse_sse("".join(frames))
    names = [n for n, _ in events]
    assert names[0] == "snapshot"
    assert names[-1] == "done"
    assert events[0][1]["found"] is True
    assert events[0][1]["session_id"] == "sid"
    assert ("entry", {"role": "user", "text": "hi"}) in [
        (n, {k: d[k] for k in ("role", "text") if k in d}) for n, d in events if n == "entry"
    ]


def test_pi_chat_sse_tails_new_lines_while_running(tmp_path: Path):
    import json

    from dev_yard.web.jobs import PiChatSse

    job = Job(id="abc", jira="AB-1", action="spec")
    cwd = tmp_path / "yard"
    cwd.mkdir()
    job.record_pi_run(cwd)
    job.set_state("running")
    sessions = tmp_path / "sessions"
    path = sessions / "dir" / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "type": "session",
                "version": 3,
                "id": "sid",
                "timestamp": "2099-01-01T00:00:00.000Z",
                "cwd": str(cwd.resolve()),
            }
        )
        + "\n"
    )
    sse = PiChatSse(job, 0, cwd, sessions_dir=sessions)
    frames, done, _seq = sse.poll()
    assert done is False
    assert [n for n, _ in _parse_sse("".join(frames))] == ["snapshot"]
    with path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "next"}],
                    },
                }
            )
            + "\n"
        )
    frames, done, _seq = sse.poll()
    assert done is False
    events = _parse_sse("".join(frames))
    assert events == [("entry", {"role": "assistant", "text": "next"})]
    job.set_state("ok")
    frames, done, _seq = sse.poll()
    assert done is True
    assert _parse_sse("".join(frames))[-1][0] == "done"


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

    monkeypatch.setattr("dev_yard.web.jobs.service.run_stage", fake_launch)
    runner = JobRunner(yard, execute=default_execute, sync=False)
    assert runner.resume_pending_grills() == []
    assert runner.running_brief() == []


def test_default_execute_plugin_stage(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    pdir = yard / "plugins" / "deploy"
    pdir.mkdir(parents=True)
    (pdir / "plugin.yaml").write_text("name: deploy\ntools: [read]\n", encoding="utf-8")
    (pdir / "SKILL.md").write_text("# deploy\n", encoding="utf-8")
    (yard / "yard.yaml").write_text("plugins: [plugins/deploy]\n", encoding="utf-8")
    d, _ = req_open(yard, "AB-30", source="none")

    class FakeLog:
        def __init__(self, job, root, bundle):
            self.job = job

        def start(self, prompt, cwd, extra_read_paths, repo=None):
            self.job.append("deploy-stream")
            return RunResult(ok=True, summary="deploy-stream")

    monkeypatch.setattr("dev_yard.web.jobs.JobLogRunner", FakeLog)
    job = JobRunner(yard, execute=default_execute, sync=True).submit("deploy", "AB-30")
    assert job.state == "ok"
    assert "deploy finished" in job.log
    import yaml

    data = yaml.safe_load((d / "STATUS.yaml").read_text(encoding="utf-8"))
    assert data["stage_runs"]["deploy"]["ok"] is True


def test_overridden_grill_still_uses_web_grill_job(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    pdir = yard / "plugins" / "review-skill"
    pdir.mkdir(parents=True)
    (pdir / "plugin.yaml").write_text("name: grill\ntools: [read]\n", encoding="utf-8")
    (pdir / "SKILL.md").write_text("# grill\n", encoding="utf-8")
    (yard / "yard.yaml").write_text("plugins: [plugins/review-skill]\n", encoding="utf-8")
    req_open(yard, "AB-31", source="none")
    called: list[str] = []

    def fake_grill(root, job):
        called.append(job.action)
        job.append("web-grill")

    monkeypatch.setattr("dev_yard.web.jobs._run_web_grill", fake_grill)
    job = JobRunner(yard, execute=default_execute, sync=True).submit("grill", "AB-31")
    assert job.state == "ok"
    assert called == ["grill"]
    assert "web-grill" in job.log


def test_default_execute_reset_phase(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-32", source="none")
    import yaml

    status_file = d / "STATUS.yaml"
    data = yaml.safe_load(status_file.read_text(encoding="utf-8"))
    data["phase"] = "testing"
    status_file.write_text(yaml.safe_dump(data), encoding="utf-8")

    job = JobRunner(yard, execute=default_execute, sync=True).submit(
        "reset-phase", "AB-32"
    )
    assert job.state == "ok"
    assert "AB-32 phase=open" in job.log
    reloaded = yaml.safe_load(status_file.read_text(encoding="utf-8"))
    assert reloaded["phase"] == "open"


def test_default_execute_reset_grill(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-33", source="none")
    (d / ".grill-round.json").write_text("{}")

    job = JobRunner(yard, execute=default_execute, sync=True).submit(
        "reset-grill", "AB-33"
    )
    assert job.state == "ok"
    assert "对齐已重置" in job.log
    assert not (d / ".grill-round.json").exists()


def test_job_cancel_kills_every_tracked_proc(monkeypatch):
    job = Job(id="abc", jira="AB-1", action="qa-run")
    killed: list[object] = []
    monkeypatch.setattr("dev_yard.web.jobs._kill_proc", killed.append)
    first, second = object(), object()
    job.track_proc(first)  # type: ignore[arg-type]
    job.track_proc(second)  # type: ignore[arg-type]
    job.untrack_proc(first)  # type: ignore[arg-type]
    job.cancel()
    assert killed == [second]
    # A proc that spawns after cancel was requested is killed on registration.
    late = object()
    job.track_proc(late)  # type: ignore[arg-type]
    assert killed == [second, late]


def test_default_execute_run_test_passes_cancel_hooks(tmp_path: Path, monkeypatch):
    seen: dict = {}

    def fake_req_test(root, jira, **kwargs):
        seen.update(kwargs)
        return {"run_id": "r", "summary": {}, "cases": 0}

    monkeypatch.setattr("dev_yard.qa.req_test", fake_req_test)
    job = Job(id="abc", jira="AB-1", action="qa-run")
    default_execute(tmp_path, job)
    assert seen["cancel_check"] == job.cancel_requested.is_set
    assert seen["on_spawn"] == job.track_proc
    assert seen["on_reap"] == job.untrack_proc


def test_run_test_job_cancel_marks_cancelled(tmp_path: Path, monkeypatch):
    import time

    from dev_yard.runners import JobCancelled

    def fake_req_test(root, jira, **kwargs):
        while not kwargs["cancel_check"]():
            time.sleep(0.01)
        raise JobCancelled("qa-run cancelled")

    monkeypatch.setattr("dev_yard.qa.req_test", fake_req_test)
    yard = tmp_path / "yard"
    init_yard(yard)
    runner = JobRunner(yard, execute=default_execute, sync=False)
    job = runner.submit("qa-run", "AB-9")
    deadline = time.time() + 2
    while job.state != "running" and time.time() < deadline:
        time.sleep(0.01)
    assert runner.cancel(job.id) is job
    assert job.done.wait(timeout=2)
    assert job.state == "cancelled"
    assert "cancelled" in job.log
