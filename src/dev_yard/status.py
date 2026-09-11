from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from dev_yard.paths import status_path
from dev_yard.tickets import Ticket

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def jira_lock(jira: str) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(jira)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[jira] = lock
        return lock


STATES = (
    "pending",
    "ready",
    "implementing",
    "implemented",
    "reviewing",
    "done",
    "blocked",
)


def _slot(val: Any) -> dict[str, Any]:
    if val is None:
        return {}
    if isinstance(val, dict):
        return dict(val)
    return {"repo": val}


def tickets_map(raw: Any) -> dict[str, dict[str, Any]]:
    """Canonical shape is {ticket_id: {state, repo, ...}}. Agents sometimes write a list."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(tid): _slot(slot) for tid, slot in raw.items()}
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, str):
            out[item] = {}
        elif isinstance(item, dict):
            if "id" in item:
                tid = str(item["id"])
                out[tid] = {k: v for k, v in item.items() if k != "id"}
            else:
                for tid, val in item.items():
                    out[str(tid)] = _slot(val)
    return out


def load(root: Path, jira: str) -> dict[str, Any]:
    p = status_path(root, jira)
    if not p.exists():
        return {"jira": jira, "phase": "open", "tickets": {}, "repos": []}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data["tickets"] = tickets_map(data.get("tickets"))
    return data


def save(root: Path, jira: str, data: dict[str, Any]) -> None:
    p = status_path(root, jira)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["tickets"] = tickets_map(data.get("tickets"))
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def sync_tickets(data: dict[str, Any], tickets: list[Ticket]) -> dict[str, Any]:
    existing = tickets_map(data.get("tickets"))
    keep = {t.id for t in tickets}
    existing = {tid: slot for tid, slot in existing.items() if tid in keep}
    data["tickets"] = existing
    for t in tickets:
        slot = existing.setdefault(t.id, {})
        slot.setdefault("state", "pending")
        slot.setdefault("worktree", None)
        slot.setdefault("child_worktree", None)
        slot["repo"] = t.repo
        slot["parallel"] = t.parallel
        slot["depends_on"] = t.depends_on
    data["repos"] = sorted({t.repo for t in tickets if t.repo})
    return data


def refresh_ready(data: dict[str, Any]) -> dict[str, Any]:
    tickets = tickets_map(data.get("tickets"))
    data["tickets"] = tickets
    for _tid, slot in tickets.items():
        deps = slot.get("depends_on") or []
        if slot.get("state") in {"done", "implementing", "implemented", "reviewing", "blocked"}:
            continue
        if all((tickets.get(d) or {}).get("state") == "done" for d in deps):
            slot["state"] = "ready"
        else:
            slot["state"] = "pending"
    return data


def ready_ids(data: dict[str, Any]) -> list[str]:
    tickets = tickets_map(data.get("tickets"))
    return [tid for tid, s in tickets.items() if s.get("state") in {"ready", "implementing"}]


def all_done(data: dict[str, Any]) -> bool:
    tickets = tickets_map(data.get("tickets"))
    return bool(tickets) and all(s.get("state") == "done" for s in tickets.values())


def test_passed(data: dict[str, Any]) -> bool:
    test = data.get("test")
    return isinstance(test, dict) and test.get("latest_verdict") == "passed"


def pipeline_complete(data: dict[str, Any]) -> bool:
    """True only after a passed test report. Legacy phase=done (contract only) is not complete."""
    return data.get("phase") == "done" and test_passed(data)
