from __future__ import annotations

from typing import Any

import yaml

from dev_yard.qa_schedule import blocked_kind, format_blocked_kind
from dev_yard.test_report import Finding, InboundReport, ReportRejected

DESIGN_BLOCKED_PREFIX = "design-blocked:"
DEFECT_CLASSES = ("product", "case", "unclassified")


def classify_defect(item: dict[str, Any]) -> str:
    """product opens a B ticket. case and unclassified do not.

    An explicit `defect_class` wins. A `case-defect:` reason is a case bug
    even when the worker marked the row failed. Anything else is unclassified:
    the run failed, but that is not evidence of a product defect.
    """
    explicit = str(item.get("defect_class") or "").strip().lower()
    if explicit in DEFECT_CLASSES:
        return explicit
    reason = str(item.get("reason") or "").strip().lower()
    if reason.startswith("case-defect:"):
        return "case"
    return "unclassified"


def has_design_blocked_skip(cases: list[dict[str, Any]]) -> bool:
    """True when a case was skipped because its data could not be verified.

    `--allow-unverified` waives the gate and skips those cases; that must not
    let the rest of the run read as a clean pass, so the caller refuses ingest.
    """
    for item in cases:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "skipped":
            continue
        if str(item.get("reason") or "").startswith(DESIGN_BLOCKED_PREFIX):
            return True
    return False


def map_qa_result(run: dict[str, Any], cases: list[dict[str, Any]]) -> InboundReport | None:
    """Map a yard-qa run into an inbound test report. None means do not ingest."""
    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    failed = int(summary.get("failed") or 0)
    blocked = int(summary.get("blocked") or 0)
    passed = int(summary.get("passed") or 0)
    skipped = int(summary.get("skipped") or 0)
    total = int(summary.get("total") or 0)
    if not cases or (total == 0 and failed == 0 and blocked == 0 and passed == 0 and skipped == 0):
        return None
    if failed == 0 and blocked > 0:
        return None
    # A skipped design-blocked case means unverified data was waived through;
    # the run is not a clean pass even if the rest passed.
    if failed == 0 and has_design_blocked_skip(cases):
        return None
    # A skipped-only run proves nothing: do not ingest it as a pass.
    if failed == 0 and passed == 0:
        return None

    if failed > 0:
        findings: list[Finding] = []
        for item in cases:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") != "failed":
                continue
            cid = str(item.get("case") or item.get("id") or "").strip()
            repo = str(item.get("repo") or "").strip()
            if not repo:
                raise ReportRejected(f"failed case {cid or '?'} is missing repo")
            failure = item.get("failure") if isinstance(item.get("failure"), dict) else {}
            step_desc = str(failure.get("step_desc") or "").strip()
            reason = str(item.get("reason") or "").strip()
            evidence = str(failure.get("evidence") or "").strip()
            detail = " ".join(p for p in (step_desc, reason, evidence) if p)
            klass = classify_defect(item)
            if klass != "product":
                detail = f"[{klass}] {detail}".strip()
            findings.append(
                Finding(
                    id=cid or f"F{len(findings) + 1}",
                    title=str(item.get("title") or cid),
                    detail=detail,
                    repo=repo,
                    defect_class=klass,
                )
            )
        if not findings:
            raise ReportRejected("summary.failed > 0 but no failed cases")
        body = _body(run, cases)
        line = _summary_line(summary)
        return InboundReport(
            verdict="failed",
            body=body,
            summary=line,
            source="yard",
            findings=findings,
        )

    body = _body(run, cases)
    return InboundReport(
        verdict="passed",
        body=body,
        summary=_summary_line(summary),
        source="yard",
        findings=[],
    )


def _summary_line(summary: dict[str, Any]) -> str:
    return summary_line(summary)


def md_cell(text: Any) -> str:
    """One-line, pipe-escaped text safe inside a Markdown table cell."""
    return " ".join(str(text or "").split()).replace("|", "\\|")


def summary_line(summary: dict[str, Any]) -> str:
    """`blocked` is not one number: break it out so a case-defect is visible."""
    line = (
        f"passed={summary.get('passed') or 0} "
        f"failed={summary.get('failed') or 0} "
        f"blocked={summary.get('blocked') or 0} "
        f"skipped={summary.get('skipped') or 0}"
    )
    parts = format_blocked_kind(summary.get("blocked_kind"))
    if parts:
        line += f" ({parts})"
    return line


def _blocked_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in cases if isinstance(c, dict) and str(c.get("status") or "") == "blocked"]


def _blocked_lines(cases: list[dict[str, Any]]) -> list[str]:
    rows = _blocked_rows(cases)
    if not rows:
        return []
    lines = ["", "## blocked", ""]
    for c in rows:
        cid = md_cell(c.get("case") or c.get("id"))
        kind = blocked_kind(str(c.get("reason") or ""), str(c.get("blocked_class") or ""))
        reason = md_cell(c.get("reason"))
        lines.append(f"- `{cid}` [{kind}] {reason}".rstrip())
    lines.append("")
    return lines


def _body(run: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    failed = [
        c
        for c in cases
        if isinstance(c, dict) and str(c.get("status") or "") == "failed"
    ]
    if failed:
        lines = ["# yard-qa failures", ""]
        for c in failed:
            cid = md_cell(c.get("case") or c.get("id"))
            title = md_cell(c.get("title"))
            reason = md_cell(c.get("reason"))
            klass = classify_defect(c)
            lines.append(f"- `{cid}` [{klass}] {title}: {reason}".rstrip())
        lines.extend(_blocked_lines(cases))
        return "\n".join(lines)
    dumped = yaml.safe_dump(run, sort_keys=False, allow_unicode=True)
    if not dumped.endswith("\n"):
        dumped += "\n"
    return dumped
