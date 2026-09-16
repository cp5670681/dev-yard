from __future__ import annotations

from typing import Any

import yaml

from dev_yard.test_report import Finding, InboundReport, ReportRejected


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

    by_id = {}
    for item in cases:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("case") or item.get("id") or "").strip()
        if cid:
            by_id[cid] = item

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
            findings.append(
                Finding(
                    id=cid or f"F{len(findings) + 1}",
                    title=str(item.get("title") or cid),
                    detail=detail,
                    repo=repo,
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
    return (
        f"passed={summary.get('passed') or 0} "
        f"failed={summary.get('failed') or 0} "
        f"blocked={summary.get('blocked') or 0} "
        f"skipped={summary.get('skipped') or 0}"
    )


def _body(run: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    failed = [
        c
        for c in cases
        if isinstance(c, dict) and str(c.get("status") or "") == "failed"
    ]
    if failed:
        lines = ["# yard-qa failures", ""]
        for c in failed:
            cid = c.get("case") or c.get("id")
            title = c.get("title") or ""
            reason = c.get("reason") or ""
            lines.append(f"- `{cid}` {title}: {reason}".rstrip())
        lines.append("")
        return "\n".join(lines)
    dumped = yaml.safe_dump(run, sort_keys=False, allow_unicode=True)
    if not dumped.endswith("\n"):
        dumped += "\n"
    return dumped
