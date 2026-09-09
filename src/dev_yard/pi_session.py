from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

_TEXT_LIMIT = 32_000
_ARGS_LIMIT = 400


def default_sessions_dir() -> Path:
    override = os.environ.get("YARD_PI_SESSIONS")
    if override:
        return Path(override)
    return Path.home() / ".pi" / "agent" / "sessions"


def cwd_is_under_root(cwd: Path, root: Path) -> bool:
    try:
        resolved = cwd.resolve()
        base = root.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def _parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _norm_cwd(value: str | Path) -> str:
    path = Path(value)
    try:
        if path.exists():
            return str(path.resolve())
    except OSError:
        pass
    return str(path)


def _header(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            line = f.readline()
    except OSError:
        return None
    if not line.strip():
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(rec, dict):
        return None
    return rec


def find_session(
    cwd: Path,
    *,
    started_at: str | None = None,
    until: str | None = None,
    sessions_dir: Path | None = None,
) -> Path | None:
    root = sessions_dir or default_sessions_dir()
    if not root.is_dir():
        return None
    want = _norm_cwd(cwd)
    start_ts = _parse_iso(started_at)
    until_ts = _parse_iso(until)
    best: tuple[float, Path] | None = None
    for path in root.glob("*/*.jsonl"):
        rec = _header(path)
        if rec is None:
            continue
        header_cwd = rec.get("cwd")
        if not isinstance(header_cwd, str) or _norm_cwd(header_cwd) != want:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        ts = _parse_iso(rec.get("timestamp") if isinstance(rec.get("timestamp"), str) else None)
        created = ts if ts is not None else mtime
        if start_ts is not None and created < start_ts - 2:
            continue
        if until_ts is not None and created >= until_ts:
            continue
        if best is None or mtime >= best[0]:
            best = (mtime, path)
    return best[1] if best else None


def _join_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _thinking(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "thinking":
            continue
        if isinstance(block.get("thinking"), str):
            parts.append(block["thinking"])
        elif isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _args_preview(value: Any) -> str:
    if value is None:
        return ""
    try:
        raw = json.dumps(value, ensure_ascii=False)
    except TypeError:
        raw = str(value)
    return _clip(raw, _ARGS_LIMIT)


def _tools(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    tools: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "toolCall":
            continue
        tools.append(
            {
                "id": block.get("id") or "",
                "name": block.get("name") or "tool",
                "args": _args_preview(block.get("arguments")),
            }
        )
    return tools


def _entry_from_message(rec: dict[str, Any]) -> dict[str, Any] | None:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    role = msg.get("role")
    if not isinstance(role, str):
        return None
    content = msg.get("content")
    entry: dict[str, Any] = {
        "role": role,
        "text": _clip(_join_text(content), _TEXT_LIMIT),
    }
    if role == "assistant":
        thinking = _thinking(content)
        if thinking:
            entry["thinking"] = _clip(thinking, _TEXT_LIMIT)
        tools = _tools(content)
        if tools:
            entry["tools"] = tools
    elif role == "toolResult":
        entry["tool_name"] = msg.get("toolName") or ""
        entry["tool_call_id"] = msg.get("toolCallId") or ""
        entry["is_error"] = bool(msg.get("isError"))
    return entry


def load_conversation(
    cwd: Path,
    *,
    root: Path,
    started_at: str | None = None,
    until: str | None = None,
    offset: int = 0,
    sessions_dir: Path | None = None,
) -> dict[str, Any]:
    if not cwd_is_under_root(cwd, root):
        raise PermissionError("cwd outside yard root")
    path = find_session(
        cwd,
        started_at=started_at,
        until=until,
        sessions_dir=sessions_dir,
    )
    if path is None:
        return {
            "found": False,
            "cwd": str(cwd),
            "session_id": None,
            "entries": [],
            "next_offset": offset,
        }
    header = _header(path)
    entries, next_offset = read_entries(path, offset)
    sid = header.get("id") if isinstance(header, dict) else None
    return {
        "found": True,
        "cwd": str(cwd),
        "session_id": sid if isinstance(sid, str) else None,
        "entries": entries,
        "next_offset": next_offset,
    }


def read_entries(path: Path, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    if offset < 0:
        offset = 0
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if offset > size:
        offset = 0
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read()
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        if cut < 0:
            return [], offset
        data = data[: cut + 1]
    entries: list[dict[str, Any]] = []
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(rec, dict) or rec.get("type") != "message":
            continue
        entry = _entry_from_message(rec)
        if entry is not None:
            entries.append(entry)
    return entries, offset + len(data)
