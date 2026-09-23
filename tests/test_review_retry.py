"""A review that forgets submit_review is re-asked once before being given up."""

from __future__ import annotations

import json
from pathlib import Path

from dev_yard import runners
from dev_yard.runners import PiRunner
from dev_yard.stages import BUILTIN_STAGES


def _event(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _prose_only() -> str:
    return _event(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "两轴报告：无阻断。"}],
            },
        }
    )


def _verdict(verdict: str) -> str:
    return "".join(
        [
            _event(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "c1",
                    "toolName": "submit_review",
                    "args": {"verdict": verdict},
                }
            ),
            _event(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "c1",
                    "toolName": "submit_review",
                    "isError": False,
                    "result": {"details": {"verdict": verdict, "findings": []}},
                }
            ),
        ]
    )


def _runner(tmp_path: Path, bundle: str = "review") -> PiRunner:
    # The binary only has to exist for the preflight check; the subprocess is
    # stubbed out below.
    binary = tmp_path / "pi"
    binary.write_text("")
    return PiRunner(
        root=tmp_path,
        bundle=bundle,
        print_mode=True,
        binary=str(binary),
        spec=BUILTIN_STAGES[bundle],
        provider="p",
        model="m",
    )


def _install(monkeypatch, outputs: list[str]) -> list[dict]:
    calls: list[dict] = []

    def fake(argv, cwd, prompt, **kwargs):
        calls.append({"argv": argv, "cwd": cwd, "prompt": prompt})
        return 0, outputs[min(len(calls), len(outputs)) - 1]

    monkeypatch.setattr(runners, "run_pi_print_tracked", fake)
    return calls


def test_retry_recovers_the_missing_verdict(tmp_path: Path, monkeypatch):
    calls = _install(monkeypatch, [_prose_only(), _verdict("passed")])
    result = _runner(tmp_path).start("review this", tmp_path, [])

    assert result.verdict == "passed"
    assert result.ok is True
    assert result.summary == "两轴报告：无阻断。"
    assert len(calls) == 2

    # The retry must resume the same session, not start a fresh review.
    sids = []
    for call in calls:
        assert "--session-id" in call["argv"]
        sids.append(call["argv"][call["argv"].index("--session-id") + 1])
    assert sids[0] == sids[1]
    assert "submit_review" in calls[1]["prompt"]


def test_retry_that_still_misses_stays_inconclusive(tmp_path: Path, monkeypatch):
    calls = _install(monkeypatch, [_prose_only(), _prose_only()])
    result = _runner(tmp_path).start("review this", tmp_path, [])

    assert len(calls) == 2
    assert result.ok is False
    assert result.verdict is None
    assert result.verdict_missing is True
    assert "自动重试" in result.summary


def test_empty_first_turn_keeps_retry_prose_not_the_missing_note(
    tmp_path: Path, monkeypatch
):
    warning = (
        "Warning: No project session found with id 'yardreviewabc'; "
        "creating a new session\n"
    )
    calls = _install(monkeypatch, [warning, _verdict("passed")])
    result = _runner(tmp_path).start("review this", tmp_path, [])

    assert len(calls) == 2
    assert result.verdict == "passed"
    assert result.summary == "结论已在重试时提交。"
    assert "creating a new session" not in result.summary


def test_session_create_warning_is_not_stored_as_the_report():
    from dev_yard.runners import finish_pi_run

    raw = (
        "Warning: No project session found with id 'yardreviewabc'; "
        "creating a new session\n"
        + _prose_only()
    )
    result = finish_pi_run("review", 0, raw)
    assert "两轴报告" in result.summary
    assert "creating a new session" not in result.summary


def test_no_retry_when_the_first_run_has_a_verdict(tmp_path: Path, monkeypatch):
    calls = _install(monkeypatch, [_verdict("failed")])
    result = _runner(tmp_path).start("review this", tmp_path, [])

    assert len(calls) == 1
    assert result.verdict == "failed"


def test_job_log_runner_retries_a_missing_verdict(tmp_path: Path, monkeypatch):
    from dev_yard.web.jobs import Job, JobLogRunner

    calls = _install(monkeypatch, [_prose_only(), _verdict("failed")])
    # JobLogRunner's first turn uses its own imported binding; the retry uses
    # the runners module. Both must see the stub.
    monkeypatch.setattr(
        "dev_yard.web.jobs.run_pi_print_tracked", runners.run_pi_print_tracked
    )
    monkeypatch.setattr("dev_yard.web.jobs.shutil.which", lambda _b: "/usr/bin/pi")
    job = Job(id="job-1", jira="PG-1", action="review")
    result = JobLogRunner(
        job,
        tmp_path,
        "review",
        spec=BUILTIN_STAGES["review"],
        provider="p",
        model="m",
    ).start("review this", tmp_path, [])

    assert result.verdict == "failed"
    assert result.ok is False
    assert len(calls) == 2
    sids = [c["argv"][c["argv"].index("--session-id") + 1] for c in calls]
    assert sids[0] == sids[1]
    assert "submit_review" in calls[1]["prompt"]


def test_job_log_runner_does_not_retry_after_cancel(tmp_path: Path, monkeypatch):
    from dev_yard.web.jobs import Job, JobCancelled, JobLogRunner

    calls: list[dict] = []

    def fake(argv, cwd, prompt, **kwargs):
        calls.append({"argv": argv, "prompt": prompt})
        job.cancel()
        return 0, _prose_only()

    job = Job(id="job-2", jira="PG-1", action="review")
    monkeypatch.setattr("dev_yard.runners.run_pi_print_tracked", fake)
    monkeypatch.setattr("dev_yard.web.jobs.run_pi_print_tracked", fake)
    monkeypatch.setattr("dev_yard.web.jobs.shutil.which", lambda _b: "/usr/bin/pi")
    runner = JobLogRunner(
        job,
        tmp_path,
        "review",
        spec=BUILTIN_STAGES["review"],
        provider="p",
        model="m",
    )
    try:
        runner.start("review this", tmp_path, [])
    except JobCancelled:
        pass
    else:
        raise AssertionError("cancel before the retry must not start a second pi")
    assert len(calls) == 1


def test_job_log_runner_keeps_a_verdict_recovered_while_cancel_lands(
    tmp_path: Path, monkeypatch
):
    from dev_yard.web.jobs import Job, JobLogRunner

    calls: list[dict] = []

    def fake(argv, cwd, prompt, **kwargs):
        calls.append({"prompt": prompt})
        if len(calls) == 2:
            job.cancel()
            return 0, _verdict("passed")
        return 0, _prose_only()

    job = Job(id="job-3", jira="PG-1", action="contract")
    monkeypatch.setattr("dev_yard.runners.run_pi_print_tracked", fake)
    monkeypatch.setattr("dev_yard.web.jobs.run_pi_print_tracked", fake)
    monkeypatch.setattr("dev_yard.web.jobs.shutil.which", lambda _b: "/usr/bin/pi")
    result = JobLogRunner(
        job,
        tmp_path,
        "contract",
        spec=BUILTIN_STAGES["contract"],
        provider="p",
        model="m",
    ).start("review contracts", tmp_path, [])

    assert result.verdict == "passed"
    assert result.ok is True
    assert len(calls) == 2


def test_other_stages_are_not_retried(tmp_path: Path, monkeypatch):
    calls = _install(monkeypatch, [_prose_only()])
    result = _runner(tmp_path, "implement").start("do work", tmp_path, [])

    assert len(calls) == 1
    assert result.ok is True
    assert not any("--session-id" in c["argv"] for c in calls)
