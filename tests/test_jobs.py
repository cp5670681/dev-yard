import threading
from pathlib import Path

import pytest

from dev_yard.runners import RunResult
from dev_yard.service import init_yard, repo_add, req_open
from dev_yard.web.jobs import JobRunner, default_execute


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
