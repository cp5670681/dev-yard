from pathlib import Path

from dev_yard.doc_analyze import issues_for, stamp
from dev_yard.service import init_yard, req_open
from dev_yard.tickets import Ticket, parse_tickets


def _ticket(tid: str, covers: list[str], source: str = "") -> Ticket:
    return Ticket(id=tid, title=tid, repo="backend", covers=covers, source=source)


SPEC = """
# Spec

## 规范陈述

- 筛选仍按地块标题。

### US1 — 列表

- US1/AC1: Given 有地块, When 打开列表, Then 列显示资源标题

## Requirements

- FR-001: 列显示资源标题

## Implementation Decisions

- C-001: 列表字段用资源标题
"""


def test_legacy_spec_has_no_coverage_findings():
    assert issues_for("# Spec — REC-01\n\n## Goals\n", [_ticket("T1", [])]) == []


def test_missing_covers_and_uncited_requirement_are_critical():
    found = issues_for(SPEC, [_ticket("T1", [])])
    assert any(item.severity == "CRITICAL" and "T1" in item.summary for item in found)
    assert any("FR-001" in item.summary for item in found)


def test_story_id_covers_its_scenarios():
    grill = """
### 规范陈述

- 筛选仍按地块标题。

### 覆盖表

| 范围 | 状态 |
| --- | --- |
| 范围 | 已问 |
"""
    found = issues_for(
        SPEC,
        [_ticket("T1", ["FR-001", "US1", "C-001"])],
        grill,
    )
    assert not any(item.severity == "CRITICAL" for item in found)


def test_unknown_cover_and_dropped_statement_block():
    grill = """
### 规范陈述

- 筛选仍按地块标题。

### 覆盖表

| 范围 | 状态 |
| --- | --- |
| 外部契约 | 延后 |
"""
    found = issues_for(SPEC, [_ticket("T1", ["FR-999", "US1", "C-001"])], grill)
    assert any("FR-999" in item.summary for item in found)
    assert any("FR-001" in item.summary for item in found)
    assert any(item.severity == "MEDIUM" and "延后" in item.summary for item in found)


def test_import_tickets_do_not_block_on_uncovered_ids():
    found = issues_for(SPEC, [_ticket("T1", [], source="import")])
    assert not any(item.severity == "CRITICAL" for item in found)
    bad = issues_for(SPEC, [_ticket("T1", ["FR-999"], source="import")])
    assert any("FR-999" in item.summary for item in bad)


def test_coverage_row_named_range_is_not_a_header():
    grill = """
### 规范陈述

- 筛选仍按地块标题。

### 覆盖表

| 范围 | 状态 |
| --- | --- |
| 范围 | 延后 |
| 状态机 | 已问 |
"""
    found = issues_for(
        SPEC,
        [_ticket("T1", ["FR-001", "US1", "C-001"])],
        grill,
    )
    assert any(item.severity == "MEDIUM" and "范围" in item.summary for item in found)
    assert not any(item.severity == "CRITICAL" for item in found)


def test_statement_missing_from_spec_is_critical():
    spec = SPEC.replace("- 筛选仍按地块标题。\n", "")
    grill = """
### 规范陈述

- 筛选仍按地块标题。

### 覆盖表

| 范围 | 已问 |
"""
    found = issues_for(spec, [_ticket("T1", ["FR-001", "US1", "C-001"])], grill)
    assert any("筛选仍按地块标题" in item.summary for item in found)


def test_parse_covers_bullet():
    tickets = parse_tickets(
        "## T1: 列表\n- repo: backend\n- covers: FR-001 US1/AC1\n"
    )
    assert tickets[0].covers == ["FR-001", "US1/AC1"]


def test_stamp_keeps_qualitative_prose(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    req, _ = req_open(yard, "AB-1", source="none")
    (req / "SPEC.md").write_text("# Spec\n\n没有编号\n", encoding="utf-8")
    (req / "ANALYZE.md").write_text("## 定性\n\n没有发现\n", encoding="utf-8")
    stamp(yard, "AB-1")
    text = (req / "ANALYZE.md").read_text(encoding="utf-8")
    assert "## 定性" in text
    assert "没有发现" in text
    assert "host-coverage" in text
    stamp(yard, "AB-1")
    again = (req / "ANALYZE.md").read_text(encoding="utf-8")
    assert again.count("<!-- host-coverage -->") == 1
    assert "## 定性" in again
