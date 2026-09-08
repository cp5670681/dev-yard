import json
import os
import time
from pathlib import Path

import pytest

from dev_yard.pi_session import cwd_is_under_root, find_session, read_entries


def _write_jsonl(path: Path, records: list[dict], mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _session(
    cwd: str,
    sid: str = "sess-1",
    ts: str = "2026-09-09T00:00:00.000Z",
) -> dict:
    return {"type": "session", "version": 3, "id": sid, "timestamp": ts, "cwd": cwd}


def test_find_session_matches_cwd_from_first_line_not_dirname(tmp_path: Path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    sessions = tmp_path / "sessions"
    wanted = sessions / "wrong-looking-name" / "b.jsonl"
    _write_jsonl(
        sessions / "looks-like-work" / "a.jsonl",
        [_session(str(other.resolve()), sid="nope")],
        mtime=2_000,
    )
    _write_jsonl(
        wanted,
        [_session(str(cwd.resolve()), sid="yes")],
        mtime=1_000,
    )
    found = find_session(cwd, sessions_dir=sessions)
    assert found == wanted


def test_find_session_picks_newest_mtime_for_same_cwd(tmp_path: Path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    sessions = tmp_path / "sessions"
    old = sessions / "d1" / "old.jsonl"
    new = sessions / "d2" / "new.jsonl"
    resolved = str(cwd.resolve())
    _write_jsonl(old, [_session(resolved, sid="old")], mtime=1_000)
    _write_jsonl(new, [_session(resolved, sid="new")], mtime=2_000)
    assert find_session(cwd, sessions_dir=sessions) == new


def test_find_session_respects_started_at_window(tmp_path: Path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    sessions = tmp_path / "sessions"
    resolved = str(cwd.resolve())
    first = sessions / "d1" / "first.jsonl"
    second = sessions / "d2" / "second.jsonl"
    _write_jsonl(
        first,
        [_session(resolved, sid="a", ts="2026-09-09T00:00:10.000Z")],
        mtime=1_000,
    )
    _write_jsonl(
        second,
        [_session(resolved, sid="b", ts="2026-09-09T00:00:20.000Z")],
        mtime=2_000,
    )
    got = find_session(
        cwd,
        started_at="2026-09-09T00:00:09.000Z",
        until="2026-09-09T00:00:19.000Z",
        sessions_dir=sessions,
    )
    assert got == first
    got = find_session(
        cwd,
        started_at="2026-09-09T00:00:19.000Z",
        sessions_dir=sessions,
    )
    assert got == second


def test_read_entries_parses_roles_and_tool_calls(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    _write_jsonl(
        path,
        [
            _session("/tmp/work"),
            {"type": "model_change", "modelId": "x"},
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "do it"}],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "plan"},
                        {
                            "type": "toolCall",
                            "id": "c1",
                            "name": "read",
                            "arguments": {"path": "/tmp/a.md"},
                        },
                        {"type": "text", "text": "ok"},
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "c1",
                    "toolName": "read",
                    "isError": False,
                    "content": [{"type": "text", "text": "file body"}],
                },
            },
        ],
    )
    entries, offset = read_entries(path, 0)
    assert offset == path.stat().st_size
    assert [e["role"] for e in entries] == ["user", "assistant", "toolResult"]
    assert entries[0]["text"] == "do it"
    assert entries[1]["text"] == "ok"
    assert entries[1]["thinking"] == "plan"
    assert entries[1]["tools"][0]["name"] == "read"
    assert "/tmp/a.md" in entries[1]["tools"][0]["args"]
    assert entries[2]["tool_name"] == "read"
    assert entries[2]["text"] == "file body"
    assert entries[2]["is_error"] is False


def test_read_entries_tails_from_byte_offset_and_holds_partial_line(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    first = _session("/tmp/work")
    msg = {
        "type": "message",
        "message": {"role": "user", "content": [{"type": "text", "text": "one"}]},
    }
    _write_jsonl(path, [first, msg])
    entries, offset = read_entries(path, 0)
    assert [e["text"] for e in entries] == ["one"]

    extra = {
        "type": "message",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
    }
    with path.open("ab") as f:
        f.write(json.dumps(extra).encode() + b"\n")
        f.write(b'{"type":"message","message":{"role":"user","content":[{"type":"text","text":"par')
    more, offset2 = read_entries(path, offset)
    assert [e["text"] for e in more] == ["two"]
    assert offset2 < path.stat().st_size

    with path.open("ab") as f:
        f.write(b'tial"}]}}\n')
    last, offset3 = read_entries(path, offset2)
    assert [e["text"] for e in last] == ["partial"]
    assert offset3 == path.stat().st_size


def test_cwd_is_under_root(tmp_path: Path):
    root = (tmp_path / "yard").resolve()
    root.mkdir()
    inside = root / "reqs" / "AB-1" / "worktrees" / "be"
    inside.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert cwd_is_under_root(inside, root)
    assert cwd_is_under_root(root, root)
    assert not cwd_is_under_root(outside, root)
    sneaky = root / ".." / "elsewhere"
    assert not cwd_is_under_root(sneaky, root)
