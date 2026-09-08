from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dev_yard.paths import status_path
from dev_yard.tickets import Ticket

STATES = (
    "pending",
    "ready",
    "implementing",
    "implemented",
    "reviewing",
    "done",
    "blocked",
)


def load(root: Path, jira: str) -> dict[str, Any]:
    p = status_path(root, jira)
    if not p.exists():
        return {"jira": jira, "phase": "open", "tickets": {}, "repos": []}
    return yaml.safe_load(p.read_text()) or {}


def save(root: Path, jira: str, data: dict[str, Any]) -> None:
    p = status_path(root, jira)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False))


def sync_tickets(data: dict[str, Any], tickets: list[Ticket]) -> dict[str, Any]:
    existing = data.setdefault("tickets", {})
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
    tickets = data.get("tickets") or {}
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
    return [tid for tid, s in (data.get("tickets") or {}).items() if s.get("state") == "ready"]


def all_done(data: dict[str, Any]) -> bool:
    tickets = data.get("tickets") or {}
    return bool(tickets) and all(s.get("state") == "done" for s in tickets.values())
