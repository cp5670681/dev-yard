"""Human-facing QA run report (markdown).

Deterministic counterpart of qa-powers' `report` skill: reads the run's
`result.yaml` where present, falls back to `progress.yaml` for a cancelled or
interrupted run, joins case frontmatter (`covers` / `account`) and `meta.yaml`
(`changes`), then writes `reqs/<JIRA>/qa/reports/<run_id>.md`. No agent.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard.qa_config import TestRejected
from dev_yard.qa_report import md_cell
from dev_yard.qa_schedule import BLOCKED_KINDS, blocked_kind, format_blocked_kind

_ICON = {"passed": "✅", "failed": "❌", "blocked": "⛔", "skipped": "⏭"}


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def _case_frontmatter(qa: Path) -> dict[str, dict[str, Any]]:
    from dev_yard.qa import discover_cases

    try:
        cases = discover_cases(qa)
    except (TestRejected, OSError, UnicodeDecodeError):
        return {}
    return {
        c.id: {
            "title": c.title,
            "account": c.account,
            "covers": list(c.covers),
            "repo": c.repo,
        }
        for c in cases
    }


def pick_run(runs: list[dict[str, Any]], run_id: str | None) -> dict[str, Any]:
    if not runs:
        raise TestRejected("no qa run yet; run `dev-yard req test <JIRA>` first")
    if run_id:
        for run in runs:
            if str(run.get("run_id")) == run_id:
                return run
        known = ", ".join(str(r.get("run_id")) for r in runs[:5])
        raise TestRejected(f"run {run_id!r} not found; known: {known}")
    return runs[0]


def _normalize_cases(run: dict[str, Any], fm: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in run.get("cases") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("case") or item.get("id") or "").strip()
        if not cid:
            continue
        rows.append(
            {
                "case": cid,
                "title": str(item.get("title") or ""),
                "status": str(item.get("status") or ""),
                "reason": str(item.get("reason") or ""),
                "repo": str(item.get("repo") or ""),
                "covers": [],
                "failure": item.get("failure"),
                "assertions": item.get("assertions") or [],
            }
        )
    if not rows:
        # Cancelled/interrupted run: no result.yaml, states live in progress.
        progress = run.get("progress") if isinstance(run.get("progress"), dict) else {}
        for item in progress.get("cases") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "").strip()
            if not cid:
                continue
            rows.append(
                {
                    "case": cid,
                    "title": str(item.get("title") or ""),
                    "status": str(item.get("state") or ""),
                    "reason": str(item.get("reason") or ""),
                    "repo": str(item.get("repo") or ""),
                    "covers": [],
                    "failure": None,
                    "assertions": [],
                }
            )
    for row in rows:
        meta = fm.get(row["case"]) or {}
        if not row["title"]:
            row["title"] = str(meta.get("title") or "")
        if not row["repo"]:
            row["repo"] = str(meta.get("repo") or "")
        row["account"] = str(meta.get("account") or "")
        row["covers"] = list(meta.get("covers") or [])
    return rows


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Any] = {"passed": 0, "failed": 0, "blocked": 0, "skipped": 0}
    for row in rows:
        state = row.get("status")
        if state in counts:
            counts[state] += 1
    counts["total"] = len(rows)
    breakdown = dict.fromkeys(BLOCKED_KINDS, 0)
    for row in rows:
        if row.get("status") == "blocked":
            breakdown[blocked_kind(str(row.get("reason") or ""))] += 1
    counts["blocked_kind"] = breakdown
    return counts


def _coverage_rows(
    meta: Any, rows: list[dict[str, Any]]
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    changes = meta.get("changes") if isinstance(meta, dict) else None
    if not isinstance(changes, list):
        return []
    out: list[tuple[str, str, list[dict[str, Any]]]] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        cid = str(change.get("id") or "").strip()
        if not cid:
            continue
        desc = str(change.get("desc") or change.get("ref") or "").strip()
        hits = [r for r in rows if cid in (r.get("covers") or [])]
        out.append((cid, desc, hits))
    return out


def _account_rows(rows: list[dict[str, Any]]) -> list[tuple[str, int, int, int]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(str(row.get("account") or "(default)"), []).append(row)
    out: list[tuple[str, int, int, int]] = []
    for name, items in sorted(buckets.items()):
        passed = sum(1 for r in items if r.get("status") == "passed")
        out.append((name, len(items), passed, len(items) - passed))
    return out


def _blocked_detail(rows: list[dict[str, Any]]) -> list[str]:
    blocked = [r for r in rows if r.get("status") == "blocked"]
    if not blocked:
        return []
    lines = ["## BLOCKED 说明", ""]
    for row in blocked:
        kind = blocked_kind(str(row.get("reason") or ""))
        reason = md_cell(row.get("reason"))
        lines += [
            f"### {md_cell(row['case'])} ⛔ [{kind}]",
            "",
            f"- **原因**: {reason or '(未填写)'}",
            "",
        ]
    return lines


def _failed_detail(rows: list[dict[str, Any]]) -> list[str]:
    failed = [r for r in rows if r.get("status") == "failed"]
    if not failed:
        return []
    lines = ["## 失败详情", ""]
    for row in failed:
        lines += [f"### {md_cell(row['case'])} {md_cell(row.get('title'))} ❌", ""]
        failure = row.get("failure") if isinstance(row.get("failure"), dict) else {}
        if failure.get("step_desc"):
            lines.append(f"- **步骤**: step {failure.get('step', '?')}（{failure['step_desc']}）")
        if failure.get("evidence"):
            lines.append(f"- **证据**: `{failure['evidence']}`")
        bad = [
            a
            for a in row.get("assertions") or []
            if isinstance(a, dict) and str(a.get("status") or "") == "failed"
        ]
        for i, a in enumerate(bad, 1):
            carrier = f" ({a['carrier']})" if a.get("carrier") else ""
            lines.append(
                f"- **断言 {i}**{carrier}: 预期 {a.get('expected')!r}｜实际 {a.get('actual')!r}"
            )
        if not bad and row.get("reason"):
            lines.append(f"- **原因**: {md_cell(row.get('reason'))}")
        lines.append("")
    return lines


def _conclusion(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "未覆盖"
    states = {str(h.get("status") or "") for h in hits}
    if states <= {"passed"}:
        return "全部通过"
    return "存在失败/阻塞"


def render_qa_report(
    root: Path, jira: str, run_id: str | None = None
) -> tuple[Path, dict[str, Any]]:
    """Write the markdown report; returns (path, summary)."""
    from dev_yard.qa_board import list_runs

    qa = paths.qa_dir(root, jira)
    if not qa.is_dir():
        raise TestRejected(f"{jira} has no qa dir; run `dev-yard req test {jira}` first")
    run = pick_run(list_runs(qa), run_id)
    rid = str(run.get("run_id"))
    rows = _normalize_cases(run, _case_frontmatter(qa))
    stored = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    if stored.get("total"):
        summary = stored
        if "blocked_kind" not in summary:
            summary = {**summary, "blocked_kind": summarize_rows(rows)["blocked_kind"]}
    else:
        summary = summarize_rows(rows)
    meta = _load_yaml(qa / "meta.yaml")
    module = str(meta.get("module") or "") if isinstance(meta, dict) else ""
    kind = summary.get("blocked_kind") or {}
    env = str(run.get("env") or "")
    if not env:
        progress = run.get("progress") if isinstance(run.get("progress"), dict) else {}
        env = str(progress.get("env") or "")

    lines = [
        f"# 测试报告 {rid}",
        "",
        f"模块：{module or '(未知)'}　环境：{env or '?'}　"
        f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "## 总览",
        "",
        "| 状态 | 数量 |",
        "|---|---|",
        f"| 总数 | {summary.get('total', 0)} |",
        f"| PASS | {summary.get('passed', 0)} |",
        f"| FAIL | {summary.get('failed', 0)} |",
        f"| BLOCKED | {summary.get('blocked', 0)} |",
        f"| SKIPPED | {summary.get('skipped', 0)} |",
    ]
    if summary.get("blocked"):
        lines += ["", f"blocked 分类：{format_blocked_kind(kind)}"]

    lines += [
        "",
        "## 用例明细",
        "",
        "| 用例 | 账号 | 状态 | 覆盖 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        icon = _ICON.get(str(row.get("status")), "?")
        covers = md_cell(",".join(row.get("covers") or []) or "-")
        reason = md_cell(row.get("reason"))
        if len(reason) > 120:
            reason = reason[:120] + "…"
        lines.append(
            f"| {md_cell(row['case'])} {md_cell(row.get('title'))} "
            f"| {md_cell(row.get('account') or '(default)')} "
            f"| {icon} {md_cell(row.get('status'))} | {covers} | {reason} |"
        )

    coverage = _coverage_rows(meta, rows)
    if coverage:
        lines += [
            "",
            "## 覆盖改动点与验证结论",
            "",
            "| 改动点 | 覆盖用例 | 验证结论 |",
            "|---|---|---|",
        ]
        for cid, desc, hits in coverage:
            covered = ", ".join(
                f"{md_cell(h['case'])} {_ICON.get(str(h.get('status')), '?')}" for h in hits
            )
            lines.append(
                f"| {md_cell(cid)} {md_cell(desc)} | {covered or '-'} | {_conclusion(hits)} |"
            )
        uncovered = [cid for cid, _, hits in coverage if not hits]
        if uncovered:
            lines += ["", f"未覆盖改动点：{', '.join(md_cell(c) for c in uncovered)}"]

    accounts = _account_rows(rows)
    if accounts:
        lines += [
            "",
            "## 账号覆盖",
            "",
            "| 账号 | 用例数 | 通过 | 未通过 |",
            "|---|---|---|---|",
        ]
        for name, total, passed, failed in accounts:
            lines.append(f"| {md_cell(name)} | {total} | {passed} | {failed} |")

    lines += _failed_detail(rows)
    lines += _blocked_detail(rows)

    path = paths.qa_reports_dir(root, jira) / f"{rid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    return path, summary
