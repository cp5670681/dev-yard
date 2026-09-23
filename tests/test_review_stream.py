"""The review verdict comes from submit_review, not from report text."""

from __future__ import annotations

import json

from dev_yard.runners import ReviewStream, finish_pi_run


def _event(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _stream() -> str:
    return "".join(
        [
            _event({"type": "session", "version": 3, "id": "s"}),
            _event(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "汇总结论：通过（不写 REVIEW_FAILED）。",
                            }
                        ],
                    },
                }
            ),
            _event(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "c1",
                    "toolName": "submit_review",
                    "args": {"verdict": "passed", "findings": []},
                }
            ),
            _event(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "c1",
                    "toolName": "submit_review",
                    "isError": False,
                    "result": {"details": {"verdict": "passed", "findings": []}},
                }
            ),
        ]
    )


def test_passed_verdict_survives_marker_in_prose():
    result = finish_pi_run("review", 0, _stream())
    assert result.ok is True
    assert result.verdict == "passed"
    assert "不写 REVIEW_FAILED" in result.summary
    assert result.summary.strip().startswith("汇总结论")


def test_failed_tool_verdict_blocks_even_when_prose_says_pass():
    raw = _stream().replace('"verdict": "passed"', '"verdict": "failed"')
    result = finish_pi_run("contract", 0, raw)
    assert result.ok is False
    assert result.verdict == "failed"


def test_findings_come_from_the_tool_call():
    raw = "".join(
        [
            _event(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "见下"}],
                    },
                }
            ),
            _event(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "c1",
                    "toolName": "submit_review",
                    "isError": False,
                    "result": {
                        "details": {
                            "verdict": "failed",
                            "findings": [
                                {
                                    "id": "F1",
                                    "title": "缺字段",
                                    "repo": "research",
                                    "detail": "没有返回 reason",
                                }
                            ],
                        }
                    },
                }
            ),
        ]
    )
    result = finish_pi_run("contract", 0, raw)
    assert result.findings == [
        {
            "id": "F1",
            "title": "缺字段",
            "repo": "research",
            "detail": "没有返回 reason",
        }
    ]


def test_missing_tool_on_clean_exit_is_a_failed_review():
    raw = _event(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "通过"}],
            },
        }
    )
    result = finish_pi_run("review", 0, raw)
    assert result.ok is False
    assert result.verdict == "failed"
    assert "submit_review" in result.summary


def test_message_tool_call_is_not_a_verdict_until_it_executes():
    raw = "".join(
        [
            _event(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "stopReason": "length",
                        "content": [
                            {"type": "text", "text": "通过"},
                            {
                                "type": "toolCall",
                                "name": "submit_review",
                                "arguments": {"verdict": "passed"},
                            },
                        ],
                    },
                }
            ),
            _event(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "c1",
                    "toolName": "submit_review",
                    "isError": True,
                    "result": {},
                }
            ),
        ]
    )
    result = finish_pi_run("review", 0, raw)
    assert result.verdict != "passed"
    assert result.ok is False


def test_provider_error_is_not_a_failed_review():
    raw = _event(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": "rate limit",
                "content": [{"type": "text", "text": "通过"}],
            },
        }
    )
    result = finish_pi_run("review", 0, raw)
    assert result.ok is False
    assert result.verdict is None
    assert "rate limit" in result.summary
    assert "submit_review" not in result.summary


def test_process_failure_without_verdict_stays_unstructured():
    result = finish_pi_run("review", 1, "pi blew up\n")
    assert result.ok is False
    assert result.verdict is None
    assert "pi blew up" in result.summary


def test_other_stages_ignore_the_review_tool():
    result = finish_pi_run("implement", 0, _stream())
    assert result.ok is True
    assert result.verdict is None


def test_live_log_shows_prose_not_json():
    raw = _event(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": "汇总结论：通过（不写 REVIEW_FAILED）。\n",
            },
        }
    )
    stream = ReviewStream()
    shown = stream.feed(raw)
    assert "汇总结论" in shown
    assert '"type"' not in shown
    assert stream.verdict is None
