import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dev_yard.assistant import (
    AssistantHub,
    compose_prompt,
    page_context,
    parse_suggested_actions,
)
from dev_yard.pi_rpc import PiRpc
from dev_yard.runners import ASSISTANT_TOOLS, assistant_pi_argv
from dev_yard.service import init_yard, req_open
from dev_yard.web.app import create_app


def test_parse_suggested_actions_strips_fence_and_unknown():
    text = (
        "把前后端拉一下。\n\n"
        "```suggested-actions\n"
        '[{"action":"sync","jira":"AB-1","repos":["frontend","backend"],"reason":"主干往前了"},'
        '{"action":"rm","jira":"AB-1"},'
        '{"action":"freeze","jira":"AB-1"}]\n'
        "```\n"
    )
    display, actions = parse_suggested_actions(text)
    assert "suggested-actions" not in display
    assert "把前后端拉一下。" in display
    assert [a["action"] for a in actions] == ["sync", "freeze"]
    assert actions[0]["repos"] == ["frontend", "backend"]
    assert parse_suggested_actions("just talk") == ("just talk", [])


def test_page_context_includes_requirement(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-1", source="none")
    ctx = page_context(yard, route="/r/AB-1", jira="AB-1")
    assert ctx["jira"] == "AB-1"
    assert ctx["requirement"]["phase"] == "open"
    assert any(a["id"] == "sync" for a in ctx["requirement"]["actions"])
    prompt = compose_prompt(ctx, "下一步是什么")
    assert "[yard context]" in prompt
    assert "AB-1" in prompt
    assert "sync" in prompt


def test_assistant_snapshot_renders_markdown(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    rpc = FakeRpc(
        [
            "## 下一步\n\n- 写 **SPEC**\n- 再 `tickets`\n\n"
            "```python\nprint(1)\n```\n"
            '<script>alert(1)</script>',
        ]
    )
    hub = AssistantHub(yard, rpc_factory=lambda root, session: rpc, sync=True)
    client = TestClient(create_app(yard, sync_jobs=True, assistant_hub=hub))
    created = client.post("/api/assistant/sessions", json={"route": "/", "jira": ""})
    sid = created.json()["id"]
    out = client.post(
        f"/api/assistant/sessions/{sid}/messages",
        json={"text": "怎么走"},
    )
    assert out.status_code == 200
    html = out.json()["entries"][1]["html"]
    assert "<h2>" in html
    assert "<li>" in html
    assert "<strong>" in html
    assert "<code>" in html
    assert "<pre>" in html
    assert "<script" not in html.lower()
    assert "alert(1)" not in html


def test_assistant_pi_argv_is_rpc_readonly(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    root = Path(__file__).resolve().parents[1]
    argv = assistant_pi_argv(
        root=root,
        session_id="abc",
        session_dir=tmp_path / "sess",
        binary="pi",
    )
    assert argv[0] == "pi"
    assert "--mode" in argv and "rpc" in argv
    assert "-p" not in argv
    tools = argv[argv.index("--tools") + 1]
    assert tools == ",".join(ASSISTANT_TOOLS)
    assert "bash" not in tools
    assert "edit" not in tools
    assert "--skill" in argv


class FakeRpc:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def prompt(self, message: str) -> None:
        self.prompts.append(message)

    def iter_until_settled(self):
        text = self.replies.pop(0) if self.replies else "ok"
        yield {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }
        yield {"type": "agent_settled"}

    def abort(self) -> None:
        return None

    def close(self) -> None:
        return None


class BlockingRpc:
    def __init__(self) -> None:
        self.gate = threading.Event()
        self.started = threading.Event()
        self.closed = False
        self.aborted = False
        self.prompts: list[str] = []

    def prompt(self, message: str) -> None:
        self.prompts.append(message)

    def iter_until_settled(self):
        self.started.set()
        self.gate.wait(timeout=5)
        if self.closed or self.aborted:
            return
        yield {"type": "agent_settled"}

    def abort(self) -> None:
        self.aborted = True
        self.gate.set()

    def close(self) -> None:
        self.closed = True
        self.gate.set()


def test_assistant_hub_multiturn_and_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-7", source="none")
    rpc = FakeRpc(
        [
            "对齐之后写 SPEC。",
            '拉最新代码。\n```suggested-actions\n[{"action":"sync","jira":"AB-7","repos":["backend"]}]\n```',
        ]
    )
    hub = AssistantHub(
        yard, rpc_factory=lambda root, session: rpc, sync=True
    )
    client = TestClient(create_app(yard, sync_jobs=True, assistant_hub=hub))
    ctx = client.get("/api/assistant/context", params={"route": "/r/AB-7", "jira": "AB-7"})
    assert ctx.status_code == 200
    assert ctx.json()["requirement"]["jira"] == "AB-7"

    created = client.post(
        "/api/assistant/sessions", json={"route": "/r/AB-7", "jira": "AB-7"}
    )
    assert created.status_code == 200
    sid = created.json()["id"]

    first = client.post(
        f"/api/assistant/sessions/{sid}/messages",
        json={"text": "下一步做什么"},
    )
    assert first.status_code == 200
    assert first.json()["state"] == "idle"
    assert first.json()["entries"][0]["role"] == "user"
    assert "SPEC" in first.json()["entries"][1]["text"]
    assert "<p>" in first.json()["entries"][1]["html"]
    assert "[yard context]" in rpc.prompts[0]

    second = client.post(
        f"/api/assistant/sessions/{sid}/messages",
        json={"text": "把后端拉一下最新代码"},
    )
    assert second.status_code == 200
    last = second.json()["entries"][-1]
    assert last["suggested_actions"][0]["action"] == "sync"
    assert "suggested-actions" not in last["text"]
    assert len(rpc.prompts) == 2

    events = client.get(f"/api/assistant/sessions/{sid}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "snapshot" in events.text
    assert "done" in events.text

    missing = client.get("/api/assistant/sessions/nope")
    assert missing.status_code == 404

    dropped = client.delete(f"/api/assistant/sessions/{sid}")
    assert dropped.status_code == 200
    assert dropped.json() == {"ok": True, "id": sid}
    assert client.get(f"/api/assistant/sessions/{sid}").status_code == 404
    stale = client.post(
        f"/api/assistant/sessions/{sid}/messages",
        json={"text": "还在吗"},
    )
    assert stale.status_code == 404
    assert client.delete(f"/api/assistant/sessions/{sid}").status_code == 404
    again = client.post(
        "/api/assistant/sessions", json={"route": "/r/AB-7", "jira": "AB-7"}
    )
    assert again.status_code == 200
    assert again.json()["id"] != sid
    assert again.json()["entries"] == []


def test_abort_closes_rpc_and_allows_next_send(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    rpcs: list[object] = []

    def factory(root: Path, session):
        rpc = FakeRpc(["after abort"]) if rpcs else BlockingRpc()
        rpcs.append(rpc)
        return rpc

    hub = AssistantHub(yard, rpc_factory=factory, sync=False)
    session = hub.create(route="/", jira="")
    hub.send(session.id, "first")
    deadline = time.time() + 2
    while not rpcs and time.time() < deadline:
        time.sleep(0.01)
    assert rpcs
    blocker = rpcs[0]
    assert isinstance(blocker, BlockingRpc)
    assert blocker.started.wait(timeout=2)
    deadline = time.time() + 2
    while session.state != "streaming" and time.time() < deadline:
        time.sleep(0.01)
    assert session.state == "streaming"
    hub.abort(session.id)
    assert session.state == "idle"
    assert session.error == "aborted"
    assert session._rpc is None
    assert blocker.closed
    if session._thread is not None:
        session._thread.join(timeout=2)
        assert not session._thread.is_alive()
    hub.send(session.id, "second")
    if session._thread is not None:
        session._thread.join(timeout=2)
    assert session.state == "idle"
    assert session.entries[-1]["text"] == "after abort"
    assert not any(e.get("is_error") for e in session.entries)


def test_drop_stops_rpc_and_removes_session(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    rpcs: list[object] = []

    def factory(root: Path, session):
        rpc = BlockingRpc()
        rpcs.append(rpc)
        return rpc

    hub = AssistantHub(yard, rpc_factory=factory, sync=False)
    session = hub.create(route="/", jira="")
    hub.send(session.id, "first")
    deadline = time.time() + 2
    while not rpcs and time.time() < deadline:
        time.sleep(0.01)
    assert rpcs
    blocker = rpcs[0]
    assert isinstance(blocker, BlockingRpc)
    assert blocker.started.wait(timeout=2)
    snap = hub.drop(session.id)
    assert snap.id == session.id
    assert hub.get(session.id) is None
    assert blocker.closed
    if session._thread is not None:
        session._thread.join(timeout=2)
        assert not session._thread.is_alive()
    next_session = hub.create(route="/", jira="")
    assert next_session.id != session.id
    assert next_session.entries == []
    assert session.dropped is True
    with pytest.raises(KeyError):
        hub.send(session.id, "again")
    assert len(rpcs) == 1


def test_pi_rpc_jsonl_roundtrip(tmp_path: Path):
    script = tmp_path / "fake_pi.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if msg.get('type') == 'prompt':\n"
        "        print(json.dumps({'id': msg.get('id'), 'type': 'response', 'command': 'prompt', 'success': True}), flush=True)\n"
        "        print(json.dumps({'type': 'agent_start'}), flush=True)\n"
        "        print(json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'hello'}]}}), flush=True)\n"
        "        print(json.dumps({'type': 'agent_settled'}), flush=True)\n",
        encoding="utf-8",
    )
    rpc = PiRpc.start([sys.executable, "-u", str(script)], cwd=tmp_path)
    try:
        rpc.prompt("hi")
        events = list(rpc.iter_until_settled(timeout=5))
    finally:
        rpc.close()
    assert events[0]["type"] == "agent_start"
    assert events[-1]["type"] == "agent_settled"
    assert events[1]["message"]["content"][0]["text"] == "hello"
    assert rpc.proc.stderr is None


def test_pi_rpc_command_timeout_clears_pending(tmp_path: Path):
    from dev_yard.pi_rpc import PiRpcError

    script = tmp_path / "hang.py"
    script.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    rpc = PiRpc.start([sys.executable, "-u", str(script)], cwd=tmp_path)
    try:
        with pytest.raises(PiRpcError, match="timed out"):
            rpc.command("prompt", timeout=0.3, message="hi")
        assert rpc._pending == {}
    finally:
        rpc.close()
