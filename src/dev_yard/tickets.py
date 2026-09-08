from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Ticket:
    id: str
    title: str
    repo: str
    depends_on: list[str] = field(default_factory=list)
    parallel: bool = False


HEADING = re.compile(r"^##\s+(T\d+)(?:\s*:\s*(.*))?$")


def parse_tickets(text: str) -> list[Ticket]:
    tickets: list[Ticket] = []
    current: Ticket | None = None
    for line in text.splitlines():
        m = HEADING.match(line.strip())
        if m:
            if current:
                tickets.append(current)
            current = Ticket(id=m.group(1), title=(m.group(2) or "").strip(), repo="")
            continue
        if not current or not line.strip().startswith("-"):
            continue
        body = line.strip()[1:].strip()
        if ":" not in body:
            continue
        key, val = body.split(":", 1)
        key, val = key.strip().lower(), val.strip()
        if key == "repo":
            current.repo = val
        elif key == "depends_on":
            current.depends_on = [x.strip() for x in val.replace(",", " ").split() if x.strip()]
        elif key == "parallel":
            current.parallel = val.lower() in {"true", "yes", "1"}
    if current:
        tickets.append(current)
    return tickets


def load_tickets(req_path: Path) -> list[Ticket]:
    p = req_path / "TICKETS.md"
    if not p.exists():
        return []
    return [t for t in parse_tickets(p.read_text()) if t.repo]


def by_id(tickets: list[Ticket]) -> dict[str, Ticket]:
    return {t.id: t for t in tickets}
