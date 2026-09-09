from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths, status as st
from dev_yard.tickets import load_tickets

VERDICTS = ("passed", "failed", "blocked")
SOURCES = ("api", "web", "cli")


class ReportRejected(ValueError):
    """Ingest or submit-test rejected."""


@dataclass
class Finding:
    id: str = ""
    title: str = ""
    detail: str = ""
    repo: str = ""


@dataclass
class InboundReport:
    verdict: str
    body: str
    summary: str = ""
    source: str = "api"
    findings: list[Finding] = field(default_factory=list)


def _test_slot(data: dict[str, Any]) -> dict[str, Any]:
    slot = data.get("test")
    if not isinstance(slot, dict):
        slot = {}
        data["test"] = slot
    return slot


def report_dir(root: Path, jira: str) -> Path:
    return paths.req_dir(root, jira) / "test-reports"


def latest_report_path(root: Path, jira: str) -> Path:
    return paths.req_dir(root, jira) / "TEST-REPORT.md"


def _now() -> datetime:
    return datetime.now().astimezone()


def _report_id(when: datetime, archive: Path | None = None) -> str:
    base = when.strftime("%Y-%m-%d-%H%M%S-%f")
    if archive is None or not (archive / f"{base}.md").exists():
        return base
    n = 2
    while (archive / f"{base}-{n}.md").exists():
        n += 1
    return f"{base}-{n}"


def submit_test(root: Path, jira: str) -> dict[str, Any]:
    parsed = load_tickets(paths.req_dir(root, jira))
    with st.jira_lock(jira):
        data = st.sync_tickets(st.load(root, jira), parsed)
        st.refresh_ready(data)
        if not st.all_done(data):
            raise ReportRejected(f"{jira} tickets are not all done; cannot submit for test")
        if data.get("contract_review") != "passed":
            raise ReportRejected(
                f"{jira} contract_review is {data.get('contract_review')!r}; "
                "must be passed before submit-test"
            )
        if st.test_passed(data):
            raise ReportRejected(f"{jira} already has a passed test report")
        if data.get("phase") == "testing":
            raise ReportRejected(f"{jira} is already in testing")
        data["phase"] = "testing"
        slot = _test_slot(data)
        slot["status"] = "awaiting"
        st.save(root, jira, data)
        return dict(data)


def parse_inbound(payload: dict[str, Any], default_source: str) -> InboundReport:
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        raise ReportRejected(f"verdict must be one of {', '.join(VERDICTS)}")
    body = str(payload.get("body") or "").strip()
    if not body:
        raise ReportRejected("body is required")
    source = str(payload.get("source") or default_source).strip() or default_source
    if source not in SOURCES and default_source in SOURCES:
        source = default_source
    findings: list[Finding] = []
    raw = payload.get("findings") or []
    if raw and not isinstance(raw, list):
        raise ReportRejected("findings must be a list")
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict):
            raise ReportRejected(f"findings[{i}] must be an object")
        findings.append(
            Finding(
                id=str(item.get("id") or f"F{i + 1}"),
                title=str(item.get("title") or ""),
                detail=str(item.get("detail") or ""),
                repo=str(item.get("repo") or "").strip(),
            )
        )
    return InboundReport(
        verdict=verdict,
        body=body if body.endswith("\n") else body + "\n",
        summary=str(payload.get("summary") or "").strip(),
        source=source,
        findings=findings,
    )


def accept_test_report(
    root: Path,
    jira: str,
    report: InboundReport,
) -> dict[str, Any]:
    req = paths.req_dir(root, jira)
    if not req.is_dir():
        raise FileNotFoundError(f"missing {req}")
    when = _now()
    received_at = when.isoformat(timespec="seconds")
    with st.jira_lock(jira):
        data = st.load(root, jira)
        if data.get("phase") != "testing":
            raise ReportRejected(
                f"{jira} phase={data.get('phase')}; test reports only accepted in testing"
            )
        archive = report_dir(root, jira)
        archive.mkdir(parents=True, exist_ok=True)
        rid = _report_id(when, archive)
        header = {
            "id": rid,
            "verdict": report.verdict,
            "source": report.source,
            "received_at": received_at,
            "summary": report.summary,
            "findings": [
                {"id": f.id, "title": f.title, "detail": f.detail, "repo": f.repo}
                for f in report.findings
            ],
        }
        front = yaml.safe_dump(header, sort_keys=False).rstrip()
        text = f"---\n{front}\n---\n\n{report.body}"
        if not text.endswith("\n"):
            text += "\n"
        (archive / f"{rid}.md").write_text(text)
        latest_report_path(root, jira).write_text(text)
        slot = _test_slot(data)
        slot["status"] = "received" if report.verdict != "passed" else "passed"
        slot["latest_id"] = rid
        slot["latest_verdict"] = report.verdict
        slot["source"] = report.source
        slot["received_at"] = received_at
        slot["summary"] = report.summary
        slot["findings"] = header["findings"]
        if report.verdict == "passed":
            data["phase"] = "done"
            slot["status"] = "passed"
        st.save(root, jira, data)
        return dict(data)


def _last_per_repo(tickets: dict[str, object], ids: list[str] | None) -> list[str]:
    if ids:
        return [tid for tid in ids if tid in tickets]
    by_repo: dict[str, str] = {}
    for tid, t in tickets.items():
        repo = getattr(t, "repo", None)
        if repo:
            by_repo[repo] = tid
    return list(by_repo.values())


def from_test_ids(
    tickets: dict[str, object],
    ids: list[str] | None,
    findings: list[dict[str, Any]] | None,
) -> list[str]:
    if ids:
        return _last_per_repo(tickets, ids)
    repos = {str(f.get("repo")) for f in (findings or []) if f.get("repo")}
    if not repos:
        return _last_per_repo(tickets, None)
    filtered = {
        tid: t
        for tid, t in tickets.items()
        if getattr(t, "repo", None) in repos
    }
    return _last_per_repo(filtered, None)
