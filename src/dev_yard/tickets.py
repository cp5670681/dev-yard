from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from dev_yard.fsutil import atomic_write_text
from dev_yard.parse import as_bool


@dataclass
class Ticket:
    id: str
    title: str
    repo: str
    depends_on: list[str] = field(default_factory=list)
    parallel: bool = False
    source: str = ""
    finding: str = ""
    # Lightweight doc-change ticket: id of the STATUS.yaml `changes[]` entry.
    change: str = ""


HEADING = re.compile(r"^##\s+((?:T|B)\d+)(?:\s*:\s*(.*))?$")
_TICKET_NUM = re.compile(r"^T(\d+)$")


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
            current.parallel = bool(as_bool(val, default=False))
        elif key == "source":
            current.source = val
        elif key == "finding":
            current.finding = val
        elif key == "change":
            current.change = val
    if current:
        tickets.append(current)
    return tickets


def load_tickets(req_path: Path) -> list[Ticket]:
    p = req_path / "TICKETS.md"
    if not p.exists():
        return []
    return [t for t in parse_tickets(p.read_text(encoding="utf-8")) if t.repo]


def next_ticket_id(tickets: list[Ticket]) -> str:
    """Next free `T<n>` id, ignoring `B<n>` bug tickets."""
    nums = [int(m.group(1)) for t in tickets if (m := _TICKET_NUM.match(t.id))]
    return f"T{max(nums, default=0) + 1}"


def format_light_ticket(
    *, ticket_id: str, title: str, repo: str, note: str, change_id: str
) -> str:
    return "\n".join(
        [
            f"## {ticket_id}: {title}",
            f"- repo: {repo}",
            "- depends_on:",
            "- parallel: false",
            "- source: light",
            f"- change: {change_id}",
            "",
            f"来自轻量变更 {change_id}。变更说明：{note}。",
            f"详见 REQUIREMENT.md「变更记录 {change_id}」与 SPEC.md 对应段落。",
            "验收：（可留空，由实现阶段补齐）",
            "",
        ]
    )


def append_light_ticket(
    req_path: Path,
    *,
    ticket_id: str,
    title: str,
    repo: str,
    note: str,
    change_id: str,
) -> Path:
    """Append one `source: light` ticket to TICKETS.md without touching existing tickets."""
    path = req_path / "TICKETS.md"
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    if not existing.strip():
        existing = f"# Tickets — {req_path.name}\n"
    block = format_light_ticket(
        ticket_id=ticket_id, title=title, repo=repo, note=note, change_id=change_id
    )
    text = existing.rstrip() + "\n\n" + block
    if not text.endswith("\n"):
        text += "\n"
    atomic_write_text(path, text)
    return path


def format_import_ticket(
    *, ticket_id: str, repo: str, branch: str, base: str = ""
) -> str:
    """One `source: import` ticket. Satisfies per-repo gates; never implemented."""
    base_note = f"（diff 基线 {base}）" if base else ""
    return "\n".join(
        [
            f"## {ticket_id}: 外部导入 — {repo}",
            f"- repo: {repo}",
            "- depends_on:",
            "- parallel: false",
            "- source: import",
            "",
            f"来自外部导入：分支 {branch}{base_note}。",
            "代码已存在于该分支，本票仅用于满足下游按仓/按票的流程门控，不触发实现。",
            "",
        ]
    )


def write_import_tickets(req_path: Path, entries: list[dict[str, str]]) -> Path:
    """Write TICKETS.md with exactly one `source: import` ticket per entry.

    Import is the entry point (not an incremental change), so it owns the file
    and replaces it rather than appending — otherwise a re-import would stack
    duplicate tickets.
    """
    path = req_path / "TICKETS.md"
    lines = [f"# Tickets — {req_path.name}", ""]
    for entry in entries:
        lines.append(
            format_import_ticket(
                ticket_id=entry["id"],
                repo=entry["repo"],
                branch=entry["branch"],
                base=entry.get("base", ""),
            )
        )
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path
