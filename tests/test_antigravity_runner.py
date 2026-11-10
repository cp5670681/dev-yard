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
