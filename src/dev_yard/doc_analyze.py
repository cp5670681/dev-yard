"""Host-owned spec/ticket coverage check.

The qualitative half of a cross-document review stays in the analyze skill.
This module checks what can be checked without a model: requirement ids in
SPEC.md, `covers` on tickets, grill decisions copied into the spec, and the
grill coverage table. Freeze refuses CRITICAL findings. Legacy specs that
never adopted FR-/US ids produce no findings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dev_yard import paths
from dev_yard.tickets import Ticket, load_tickets

# Boundaries keep a Jira key such as REC-01 from looking like contract id C-01.
_FR = re.compile(r"(?<![A-Za-z0-9])FR-\d+")
_AC = re.compile(r"(?<![A-Za-z0-9])US\d+/AC\d+")
_US = re.compile(r"(?<![A-Za-z0-9])US\d+")
_CONTRACT = re.compile(r"(?<![A-Za-z0-9])C-\d+")
# Tickets the host appended. They do not have to cite a story.
_EXEMPT_SOURCES = frozenset({"light", "import", "contract", "test", "review"})
_STATUSES = ("已问", "默认", "不适用", "延后")
_HOST_START = "<!-- host-coverage -->"
_HOST_END = "<!-- /host-coverage -->"


@dataclass(frozen=True)
class Issue:
    id: str
    severity: str
    location: str
    summary: str


@dataclass(frozen=True)
class SpecIds:
    fr: frozenset[str]
    us: frozenset[str]
    ac: frozenset[str]
    contracts: frozenset[str]

    @property
    def used(self) -> bool:
        return bool(self.fr or self.us or self.ac or self.contracts)

    @property
    def known(self) -> frozenset[str]:
        return self.fr | self.us | self.ac | self.contracts


def parse_spec_ids(text: str) -> SpecIds:
    ac = frozenset(_AC.findall(text or ""))
    us = frozenset(_US.findall(text or ""))
    return SpecIds(
        fr=frozenset(_FR.findall(text or "")),
        us=us,
        ac=ac,
        contracts=frozenset(_CONTRACT.findall(text or "")),
    )


def _section(text: str, heading: str) -> str:
    """Body under a heading, until the next heading of the same or higher level."""
    lines = (text or "").splitlines()
    start = None
    level = heading.split()[0].count("#")
    for i, line in enumerate(lines):
        if line.strip() == heading.strip():
            start = i + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            marks = len(stripped) - len(stripped.lstrip("#"))
            if marks <= level:
                break
        body.append(line)
    return "\n".join(body)


def canonical_statements(grill: str) -> list[str]:
    body = _section(grill or "", "### 规范陈述")
    out: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            text = stripped[2:].strip()
            if text:
                out.append(text)
    return out


def _feature_ticket(ticket: Ticket) -> bool:
    if ticket.source in _EXEMPT_SOURCES:
        return False
    return ticket.id.startswith("T")


def _parent_us(ref: str) -> str:
    return ref.split("/", 1)[0]


def _covered(ids: SpecIds, covers: set[str]) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return uncovered FR, contracts, acceptance scenarios, and bare stories."""
    missing_fr = {item for item in ids.fr if item not in covers}
    missing_c = {item for item in ids.contracts if item not in covers}
    missing_ac: set[str] = set()
    for ac in ids.ac:
        if ac in covers or _parent_us(ac) in covers:
            continue
        missing_ac.add(ac)
    missing_us: set[str] = set()
    parents = {_parent_us(ac) for ac in ids.ac}
    for us in ids.us:
        # A story that has scenarios is covered through those scenarios (or by
        # citing the story id, which covers every scenario above).
        if us in parents:
            continue
        if us not in covers:
            missing_us.add(us)
    return missing_fr, missing_c, missing_ac, missing_us


def issues_for(spec: str, tickets: list[Ticket], grill: str = "") -> list[Issue]:
    ids = parse_spec_ids(spec or "")
    found: list[Issue] = []
    n = 1

    def add(severity: str, location: str, summary: str) -> None:
        nonlocal n
        found.append(Issue(f"A{n}", severity, location, summary))
        n += 1

    if ids.used:
        covers: set[str] = set()
        # Import / light / bug tickets do not implement the spec. A requirement
        # that only has those tickets (req import) must still freeze.
        feature_present = any(_feature_ticket(ticket) for ticket in tickets)
        for ticket in tickets:
            cited = list(ticket.covers)
            if feature_present and _feature_ticket(ticket) and not cited:
                add(
                    "CRITICAL",
                    f"TICKETS.md {ticket.id}",
                    f"{ticket.id} 没有 covers。规格使用了编号，每张功能票都要写它覆盖的 FR 或 US。",
                )
            for ref in cited:
                covers.add(ref)
                if ref not in ids.known:
                    add(
                        "CRITICAL",
                        f"TICKETS.md {ticket.id}",
                        f"{ticket.id} 的 covers 引用了规格里没有的 {ref}。",
                    )
        if feature_present:
            missing_fr, missing_c, missing_ac, missing_us = _covered(ids, covers)
            for ref in sorted(missing_fr | missing_c | missing_ac | missing_us):
                add("CRITICAL", "SPEC.md", f"{ref} 没有任何票的 covers 覆盖。")

    statements = canonical_statements(grill or "")
    if statements:
        if not _section(grill or "", "### 覆盖表"):
            add("CRITICAL", "GRILL.md", "有规范陈述，但没有 ### 覆盖表。")
        for statement in statements:
            if statement not in (spec or ""):
                add(
                    "CRITICAL",
                    "SPEC.md",
                    f"规格没有原样写上已拍板的陈述：{statement}",
                )
        for line in _section(grill or "", "### 覆盖表").splitlines():
            stripped = line.strip()
            if not stripped.startswith("|") or "---" in stripped:
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not cells:
                continue
            # Header row is "| 范围 | 状态 |". A data row may also be named 范围.
            named_header = cells[0] in {"范围", "类别"} and "状态" in cells
            if named_header and not any(status in cells for status in _STATUSES):
                continue
            if not any(status in cells for status in _STATUSES):
                add("MEDIUM", "GRILL.md", f"覆盖表这一行没有状态：{stripped}")
            elif "延后" in cells:
                add("MEDIUM", "GRILL.md", f"覆盖表把「{cells[0]}」标成延后。")
    return found


def critical(items: list[Issue]) -> list[Issue]:
    return [item for item in items if item.severity == "CRITICAL"]


def freeze_gate(spec: str, tickets: list[Ticket], grill: str = "") -> tuple[bool, str]:
    """(blocked, first-CRITICAL reason) in one pass, for freeze and the board."""
    items = critical(issues_for(spec, tickets, grill))
    if not items:
        return False, ""
    item = items[0]
    return True, f"{item.location}：{item.summary}"


def load_issues(root: Path, jira: str) -> list[Issue]:
    req = paths.req_dir(root, jira)
    spec = (req / "SPEC.md").read_text(encoding="utf-8") if (req / "SPEC.md").is_file() else ""
    grill = (req / "GRILL.md").read_text(encoding="utf-8") if (req / "GRILL.md").is_file() else ""
    return issues_for(spec, load_tickets(req), grill)


def render_host_section(spec: str, items: list[Issue]) -> str:
    ids = parse_spec_ids(spec or "")
    lines = [
        _HOST_START,
        "## 宿主覆盖",
        "",
    ]
    if not ids.used and not items:
        lines.append("规格没有 FR- / US 编号，覆盖门禁跳过。")
    else:
        crit = len(critical(items))
        lines.append(f"发现 {len(items)} 条，其中 CRITICAL {crit} 条。冻结会拒绝 CRITICAL。")
        lines.append("")
        if items:
            lines.append("| ID | Severity | Location | Summary |")
            lines.append("| --- | --- | --- | --- |")
            for item in items:
                summary = item.summary.replace("|", "/")
                lines.append(f"| {item.id} | {item.severity} | {item.location} | {summary} |")
        else:
            lines.append("编号覆盖和已拍板陈述没有 CRITICAL。")
    lines += ["", _HOST_END, ""]
    return "\n".join(lines)


def stamp(root: Path, jira: str) -> list[Issue]:
    """Rewrite the host block in ANALYZE.md. Leave the skill's prose around it."""
    req = paths.req_dir(root, jira)
    spec_path = req / "SPEC.md"
    spec = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
    found = load_issues(root, jira)
    block = render_host_section(spec, found)
    path = req / "ANALYZE.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    start = existing.find(_HOST_START)
    end = existing.find(_HOST_END)
    if start >= 0 and end > start:
        end = end + len(_HOST_END)
        # Keep a trailing newline the renderer already includes.
        updated = existing[:start] + block + existing[end:].lstrip("\n")
    else:
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
    if not updated.endswith("\n"):
        updated += "\n"
    path.write_text(updated, encoding="utf-8")
    return found
