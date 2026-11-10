import json
from pathlib import Path

import pytest

from dev_yard.config import AgentChoice
from dev_yard.pi_session import load_conversation
from dev_yard.runners import RunResult, get_runner
from dev_yard.runners.antigravity_runner import AntigravityRunner
from dev_yard.service import init_yard
from dev_yard.stages import load_registry


def test_get_runner_dispatches_to_antigravity(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text(
        """
agent:
  engine: antigravity
  model: gemini-2.5-pro
""",
        encoding="utf-8",
    )
    runner = get_runner(yard, "grill")
    assert isinstance(runner, AntigravityRunner)
    assert runner.model == "gemini-2.5-pro"


def test_get_runner_dry_run_antigravity(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text(
        """
agent:
  engine: antigravity
  model: gemini-2.5-flash
""",
        encoding="utf-8",
    )
    runner = get_runner(yard, "spec", dry_run=True)
    res = runner.start("hello", yard, [])
    assert res.ok is True
    assert "antigravity" in res.summary
    assert "--model=gemini-2.5-flash" in res.summary


def test_antigravity_runner_missing_runtime_returns_127(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.delenv("YARD_ANTIGRAVITY_BIN", raising=False)
    monkeypatch.delenv("YARD_TEST_MOCK_AGENT", raising=False)
    monkeypatch.setattr("dev_yard.pi_catalog.resolve_agy_binary", lambda: None)
    runner = AntigravityRunner(root=yard, bundle="spec", model="gemini-2.5-pro")
    res = runner.start("hi", yard, [])
    assert res.ok is False
    assert res.exit_code == 127
    assert "neither `google-antigravity` Python SDK nor `agy` CLI binary" in res.summary


def test_antigravity_runner_session_persistence_and_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    sessions_dir = yard / ".yard-sessions"
    monkeypatch.setenv("YARD_ANTIGRAVITY_SESSIONS", str(sessions_dir))
    monkeypatch.setenv("YARD_TEST_MOCK_AGENT", "1")

    runner = AntigravityRunner(
        root=yard,
        bundle="spec",
        model="gemini-2.5-pro",
    )
    res = runner.start("Write SPEC.md", yard, [])
    assert res.ok is True

    # Verify session file was written
    session_files = list(sessions_dir.glob("*.jsonl"))
    assert len(session_files) == 1
    session_file = session_files[0]
    lines = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["engine"] == "antigravity"
    assert lines[1]["message"]["role"] == "user"
    assert lines[2]["message"]["role"] == "assistant"

    # Verify load_conversation reads it seamlessly
    conv = load_conversation(yard, root=yard)
    assert conv["found"] is True
    assert len(conv["entries"]) == 2
    assert conv["entries"][0]["role"] == "user"
    assert conv["entries"][0]["text"] == "Write SPEC.md"
    assert conv["entries"][1]["role"] == "assistant"


def test_antigravity_runner_structured_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.setenv("YARD_TEST_MOCK_AGENT", "1")

    runner = AntigravityRunner(
        root=yard,
        bundle="review",
        model="gemini-2.5-pro",
    )
    res = runner.start("Review code", yard, [])
    # Review without explicit tool call marks verdict_missing
    assert res.verdict_missing is True
    assert res.verdict is None


def test_parse_review_verdict_from_text():
    from dev_yard.guards.review_tool import parse_review_verdict_from_text

    # 1. Function-style call in markdown code block
    text1 = """
## Spec
All good.
```json
submit_review({
  "verdict": "passed",
  "findings": []
})
```
"""
    v1, f1 = parse_review_verdict_from_text(text1)
    assert v1 == "passed"
    assert f1 == []

    # 2. Function-style call with findings
    text2 = """
```json
submit_review({
  "verdict": "failed",
  "findings": [{"id": "F1", "title": "Missing check", "repo": "research", "detail": "detail"}]
})
```
"""
    v2, f2 = parse_review_verdict_from_text(text2)
    assert v2 == "failed"
    assert len(f2) == 1
    assert f2[0]["id"] == "F1"

    # 3. Direct JSON code block
    text3 = """
```json
{
  "verdict": "passed",
  "findings": []
}
```
"""
    v3, f3 = parse_review_verdict_from_text(text3)
    assert v3 == "passed"
    assert f3 == []

    # 4. Standalone JSON object
    text4 = 'Some report\n{"verdict": "passed", "findings": []}'
    v4, f4 = parse_review_verdict_from_text(text4)
    assert v4 == "passed"

    # 5. Nested parentheses inside findings detail
    text5 = """
```json
submit_review({
  "verdict": "failed",
  "findings": [{"id": "F1", "title": "Check", "repo": "research", "detail": "calling can_access?() returned false"}]
})
```
"""
    v5, f5 = parse_review_verdict_from_text(text5)
    assert v5 == "failed"
    assert len(f5) == 1
    assert "can_access?()" in f5[0]["detail"]

    # 6. Empty or no verdict
    assert parse_review_verdict_from_text("no verdict here")[0] is None


def test_antigravity_runner_review_with_stream_and_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yard = tmp_path / "yard"
    init_yard(yard)

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            self.returncode = 0
            self.stdout = iter([
                json.dumps({
                    "event": "step_update",
                    "step_update": {
                        "state": "ACTIVE",
                        "step_type": "agent_response",
                        "text_delta": "Review report text.\n\n```json\nsubmit_review({\n  \"verdict\": \"passed\",\n  \"findings\": []\n})\n```",
                    },
                }) + "\n",
                json.dumps({
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "response": "Review report text.",
                    },
                }) + "\n",
            ])
            self.stderr = None

        def wait(self):
            return 0

    monkeypatch.setattr("dev_yard.pi_catalog.resolve_agy_binary", lambda: "/custom/bin/agy")
    monkeypatch.setattr("dev_yard.runners.antigravity_runner.subprocess.Popen", FakeProc)

    runner = AntigravityRunner(root=yard, bundle="review", model="gemini-3.8-flash-high")
    res = runner.start("Review ticket", yard, [])
    assert res.ok is True
    assert res.verdict == "passed"
    assert res.findings == []
    assert res.verdict_missing is False

