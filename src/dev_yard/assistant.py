from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dev_yard import paths
from dev_yard.actions import JOB_ACTIONS
from dev_yard.config import load_repos
from dev_yard.reqboard import list_requirements, requirement_detail

HOST_ACTIONS = JOB_ACTIONS
_FENCE = re.compile(r"```suggested-actions\s*(\[.*?\])\s*```", re.S | re.I)
_MD_ROLES = frozenset({"assistant", "user"})


def assistant_dir(root: Path) -> Path:
    return root / ".yard-assistant"


def pi_sessions_dir(root: Path) -> Path:
    return assistant_dir(root) / "pi-sessions"


def now_iso() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def page_context(root: Path, route: str = "", jira: str = "") -> dict[str, Any]:
    repos = [
        {"alias": a, "role": r.role, "default_base": r.default_base, "path": bool(r.path)}
        for a, r in load_repos(root).items()
    ]
    ctx: dict[str, Any] = {
        "route": route or "/",
        "jira": (jira or "").strip() or None,
        "repos": repos,
        "requirements": [
            {"jira": i.jira, "phase": i.phase, "next": i.next_label, "title": i.title}
            for i in list_requirements(root)
        ],
        "docs": {
            "context": str(paths.context_md(root)),
            "adr": str(paths.adr_dir(root)),
        },
    }
    key = (jira or "").strip()
    if key:
        detail = requirement_detail(root, key)
        if detail is not None:
            ctx["requirement"] = {
                "jira": detail.jira,
                "title": detail.title,
                "phase": detail.phase,
                "next": detail.next_label,
                "contract": detail.contract,
                "docs": [
                    {
                        "slug": d.slug,
                        "filename": d.filename,
                        "filled": d.filled,
                    }
                    for d in detail.docs
                ],
                "tickets": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "repo": t.repo,
                        "state": t.state,
                        "can_implement": t.can_implement,
                        "can_review": t.can_review,
                    }
                    for t in detail.tickets
                ],
                "actions": [
                    {
                        "id": a.id,
                        "label": a.label,
                        "enabled": a.enabled,
                        "reason": a.reason,
                    }
                    for a in detail.actions
                    if a.id in HOST_ACTIONS
                ],
                "worktrees": detail.worktrees,
            }
    return ctx


def format_context(ctx: dict[str, Any]) -> str:
    return (
        "[yard context]\n"
        + json.dumps(ctx, ensure_ascii=False, indent=2)
        + "\n[/yard context]"
    )


def compose_prompt(ctx: dict[str, Any], user_text: str) -> str:
    actions = HOST_ACTIONS
    req = ctx.get("requirement") if isinstance(ctx.get("requirement"), dict) else {}
    listed = req.get("actions") if isinstance(req, dict) else None
    if listed:
        names = ", ".join(
            f"{a['id']}{'*' if a.get('enabled') else ''}"
            for a in listed
            if isinstance(a, dict) and a.get("id")
        )
    else:
        names = ", ".join(sorted(actions))
    return (
        "Follow skill `assistant` (loaded via --skill). Read-only tools only.\n"
        "Host actions the user can confirm (star = currently enabled): "
        f"{names}.\n"
        "Pulling latest default_base into clones/worktrees is host action `sync`.\n"
        f"{format_context(ctx)}\n\n"
        f"{user_text.strip()}"
    )


def parse_suggested_actions(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Return (display_text, actions). Unknown actions are dropped."""
    if not text:
        return "", []
    match = _FENCE.search(text)
    if not match:
        return text.strip(), []
    display = _FENCE.sub("", text).strip()
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return display, []
    if not isinstance(raw, list):
        return display, []
    out: list[dict[str, Any]] = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if action not in HOST_ACTIONS:
            continue
        row: dict[str, Any] = {"action": action}
        if isinstance(item.get("jira"), str) and item["jira"].strip():
            row["jira"] = item["jira"].strip()
        if isinstance(item.get("ticket_id"), str) and item["ticket_id"].strip():
            row["ticket_id"] = item["ticket_id"].strip()
        if isinstance(item.get("reason"), str) and item["reason"].strip():
            row["reason"] = item["reason"].strip()
        if isinstance(item.get("strategy"), str) and item["strategy"].strip():
            row["strategy"] = item["strategy"].strip()
        if item.get("force") is True:
            row["force"] = True
        repos = item.get("repos")
        if isinstance(repos, list):
            aliases = [str(r).strip() for r in repos if str(r).strip()]
            if aliases:
                row["repos"] = aliases
        out.append(row)
    return display, out


def _entry_from_rpc_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    from dev_yard.pi_session import entry_from_message

    return entry_from_message(msg)


RpcFactory = Callable[[Path, "AssistantSession"], Any]


@dataclass
class AssistantSession:
    id: str
    route: str
    jira: str
    created_at: str
    state: str = "idle"
    entries: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cv: threading.Condition = field(init=False, repr=False)
    _seq: int = 0
    _rpc: Any = field(default=None, repr=False)
    _gen: int = 0
    _thread: threading.Thread | None = field(default=None, repr=False)
    _start_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    dropped: bool = False

    def __post_init__(self) -> None:
        self._cv = threading.Condition(self._lock)

    def snapshot(self) -> dict[str, Any]:
        with self._cv:
            return self.snapshot_unlocked()

    def capture(self) -> tuple[dict[str, Any], int]:
        with self._cv:
            return self.snapshot_unlocked(), self._seq

    def snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "route": self.route,
            "jira": self.jira or None,
            "created_at": self.created_at,
            "state": self.state,
            "entries": [_public_entry(e, self.jira) for e in self.entries],
            "error": self.error,
        }

    def wait_seq(self, seq: int, timeout: float | None = None) -> int:
        with self._cv:
            if self._seq > seq:
                return self._seq
            self._cv.wait(timeout=timeout)
            return self._seq

    def bump(self) -> None:
        self._seq += 1
        self._cv.notify_all()

    def append_entry(self, entry: dict[str, Any]) -> None:
        with self._cv:
            self.entries.append(entry)
            self.bump()

    def set_state(self, state: str, error: str | None = None) -> None:
        with self._cv:
            self.state = state
            if error is not None:
                self.error = error
            self.bump()


class AssistantSse:
    def __init__(self, session: AssistantSession) -> None:
        self.session = session
        self._sent = False
        self._n = 0
        self._last_state: str | None = None
        self._stream_text: str | None = None

    def poll(self) -> tuple[list[str], bool, int]:
        from dev_yard.web.jobs import format_sse

        snap, seq = self.session.capture()
        frames: list[str] = []
        if not self._sent:
            frames.append(format_sse("snapshot", snap))
            self._sent = True
            self._n = len(snap["entries"])
            self._last_state = snap["state"]
        else:
            for entry in snap["entries"][self._n :]:
                frames.append(format_sse("entry", entry))
            self._n = len(snap["entries"])
            if snap["entries"]:
                last = snap["entries"][-1]
                text = last.get("text") if last.get("streaming") else None
                if isinstance(text, str) and text != self._stream_text:
                    frames.append(format_sse("delta", last))
                    self._stream_text = text
            if snap["state"] != self._last_state:
                frames.append(
                    format_sse(
                        "state",
                        {"id": snap["id"], "state": snap["state"], "error": snap["error"]},
                    )
                )
                self._last_state = snap["state"]
        done = snap["state"] == "idle" and self._sent
        if done:
            frames.append(format_sse("done", snap))
        return frames, done, seq


class AssistantHub:
    def __init__(
        self,
        root: Path,
        rpc_factory: RpcFactory | None = None,
        sync: bool = False,
    ) -> None:
        self.root = root
        self.sync = sync
        self._rpc_factory = rpc_factory
        self._sessions: dict[str, AssistantSession] = {}
        self._lock = threading.Lock()

    def create(self, route: str = "/", jira: str = "") -> AssistantSession:
        session = AssistantSession(
            id=uuid.uuid4().hex[:12],
            route=route or "/",
            jira=(jira or "").strip(),
            created_at=now_iso(),
        )
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> AssistantSession | None:
        return self._sessions.get(session_id)

    def context(self, route: str = "", jira: str = "") -> dict[str, Any]:
        return page_context(self.root, route=route, jira=jira)

    def send(self, session_id: str, text: str, route: str | None = None, jira: str | None = None) -> AssistantSession:
        session = self.get(session_id)
        if session is None or session.dropped:
            raise KeyError(session_id)
        body = (text or "").strip()
        if not body:
            raise ValueError("empty message")
        with session._start_lock:
            if session.dropped or self.get(session_id) is not session:
                raise KeyError(session_id)
            prev = session._thread
            with session._cv:
                if session.dropped:
                    raise KeyError(session_id)
                if session.state == "streaming":
                    raise ValueError("assistant is already answering")
            if prev is not None and prev.is_alive():
                prev.join(timeout=5)
                if prev.is_alive():
                    raise ValueError("assistant is still stopping")
            with session._cv:
                if session.dropped or self.get(session_id) is not session:
                    raise KeyError(session_id)
                if session.state == "streaming":
                    raise ValueError("assistant is already answering")
                if route:
                    session.route = route
                if jira is not None:
                    session.jira = jira.strip()
                session.error = None
                session._gen += 1
                gen = session._gen
                session.state = "streaming"
                session.entries.append({"role": "user", "text": body})
                session.bump()
            if self.sync:
                self._run(session, body, gen)
            else:
                thread = threading.Thread(
                    target=self._run, args=(session, body, gen), daemon=True
                )
                session._thread = thread
                thread.start()
        return session

    def abort(self, session_id: str) -> AssistantSession:
        session = self.get(session_id)
        if session is None:
            raise KeyError(session_id)
        with session._cv:
            session._gen += 1
            session.state = "idle"
            session.error = "aborted"
            rpc = session._rpc
            session._rpc = None
            session.bump()
        self._shutdown_rpc(rpc)
        return session

    def drop(self, session_id: str) -> AssistantSession:
        session = self.get(session_id)
        if session is None or session.dropped:
            raise KeyError(session_id)
        with session._cv:
            if session.dropped:
                raise KeyError(session_id)
            session.dropped = True
        self.abort(session_id)
        with session._start_lock:
            with self._lock:
                self._sessions.pop(session_id, None)
        return session

    def _shutdown_rpc(self, rpc: Any) -> None:
        if rpc is None:
            return
        if hasattr(rpc, "abort"):
            try:
                rpc.abort()
            except Exception:
                pass
        if hasattr(rpc, "close"):
            try:
                rpc.close()
            except Exception:
                pass

    def _current(self, session: AssistantSession, gen: int) -> bool:
        with session._cv:
            return session._gen == gen

    def _run(self, session: AssistantSession, user_text: str, gen: int) -> None:
        if not self._current(session, gen):
            return
        ctx = page_context(self.root, route=session.route, jira=session.jira)
        prompt = compose_prompt(ctx, user_text)
        try:
            rpc = session._rpc
            if rpc is None:
                factory = self._rpc_factory or default_rpc_factory
                rpc = factory(self.root, session)
                if not self._current(session, gen):
                    self._shutdown_rpc(rpc)
                    return
                session._rpc = rpc
            if hasattr(rpc, "prompt"):
                rpc.prompt(prompt)
            assistant_text = ""
            tools: list[dict[str, Any]] = []
            thinking = ""
            iterator = rpc.iter_until_settled() if hasattr(rpc, "iter_until_settled") else []
            for event in iterator:
                if not self._current(session, gen):
                    return
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "message_end" and isinstance(event.get("message"), dict):
                    entry = _entry_from_rpc_message(event["message"])
                    if entry and entry.get("role") == "assistant":
                        assistant_text = entry.get("text") or assistant_text
                        thinking = entry.get("thinking") or thinking
                        tools = entry.get("tools") or tools
                    elif entry and entry.get("role") == "toolResult":
                        session.append_entry(entry)
                elif kind == "message_update":
                    delta = _delta_text(event)
                    if delta:
                        assistant_text += delta
                        with session._cv:
                            if session._gen != gen:
                                return
                            if (
                                session.entries
                                and session.entries[-1].get("role") == "assistant"
                                and session.entries[-1].get("streaming")
                            ):
                                session.entries[-1]["text"] = assistant_text
                            else:
                                session.entries.append(
                                    {
                                        "role": "assistant",
                                        "text": assistant_text,
                                        "streaming": True,
                                    }
                                )
                            session.bump()
            if not self._current(session, gen):
                return
            display, actions = parse_suggested_actions(assistant_text)
            final = {
                "role": "assistant",
                "text": display or assistant_text,
            }
            if thinking:
                final["thinking"] = thinking
            if tools:
                final["tools"] = tools
            if actions:
                final["suggested_actions"] = actions
            with session._cv:
                if session._gen != gen:
                    return
                if (
                    session.entries
                    and session.entries[-1].get("role") == "assistant"
                    and session.entries[-1].get("streaming")
                ):
                    session.entries[-1] = final
                else:
                    session.entries.append(final)
                session.state = "idle"
                session.bump()
        except Exception as e:
            if not self._current(session, gen):
                return
            session.append_entry(
                {"role": "assistant", "text": str(e), "is_error": True}
            )
            session.set_state("idle", error=str(e))


def _public_entry(entry: dict[str, Any], jira: str) -> dict[str, Any]:
    out = dict(entry)
    text = out.get("text")
    if (
        isinstance(text, str)
        and text
        and out.get("role") in _MD_ROLES
        and not out.get("is_error")
    ):
        from dev_yard.web.context import render_markdown

        out["html"] = render_markdown(text, jira or "")
    return out


def _delta_text(event: dict[str, Any]) -> str:
    ame = event.get("assistantMessageEvent")
    if not isinstance(ame, dict):
        return ""
    if ame.get("type") == "text_delta" and isinstance(ame.get("delta"), str):
        return ame["delta"]
    return ""


def default_rpc_factory(root: Path, session: AssistantSession) -> Any:
    from dev_yard.pi_rpc import PiRpc
    from dev_yard.runners import assistant_pi_argv

    session_dir = pi_sessions_dir(root)
    session_dir.mkdir(parents=True, exist_ok=True)
    argv = assistant_pi_argv(
        root=root, session_id=session.id, session_dir=session_dir
    )
    return PiRpc.start(argv, cwd=root)
