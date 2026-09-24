from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard import status as st
from dev_yard.parse import as_bool, as_list
from dev_yard.tickets import load_tickets

VERDICTS = ("passed", "failed", "blocked")
SOURCES = ("api", "web", "cli", "yard")


class ReportRejected(ValueError):
    """Ingest or submit-test rejected."""


@dataclass
class Finding:
    id: str = ""
    title: str = ""
    detail: str = ""
    repo: str = ""
    depends_on: list[str] = field(default_factory=list)
    parallel: bool | None = None
    # product spawns a fix ticket. case / unclassified are recorded only.
    # Empty means a hand-submitted finding, which still spawns.
    defect_class: str = ""


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


def _assert_submittable(data: dict[str, Any], jira: str, *, recheck: bool = False) -> None:
    """Pre/post-integration gate. `recheck=True` is the TOCTOU pass on latest STATUS."""
    if not st.all_done(data):
        raise ReportRejected(
            f"{jira} tickets changed during submit-test; re-run"
            if recheck
            else f"{jira} tickets are not all done; cannot submit for test"
        )
    if data.get("contract_review") != "passed":
        raise ReportRejected(
            f"{jira} contract_review changed during submit-test; re-run"
            if recheck
            else (
                f"{jira} contract_review is {data.get('contract_review')!r}; "
                "must be passed before submit-test"
            )
        )
    if st.test_passed(data):
        raise ReportRejected(f"{jira} already has a passed test report")


def submit_test(
    root: Path,
    jira: str,
    *,
    remote: str = "origin",
    ai_resolve: bool | None = None,
    force_all: bool = False,
    on_progress: Callable[[str], None] | None = None,
    runner_factory: Any = None,
) -> dict[str, Any]:
    """Submit for testing: merge each repo's freeze branch into its test branch.

    Validates under `jira_lock`, then runs the (long, possibly AI-backed) git
    integration *outside* the lock so the board stays responsive. STATUS is
    reloaded and re-validated before the phase flip, so concurrent edits made
    during the run are never clobbered.
    """
    from dev_yard import test_integrate

    parsed = load_tickets(paths.req_dir(root, jira))
    with st.jira_lock(jira):
        data = st.sync_tickets(st.load(root, jira), parsed)
        st.refresh_ready(data)
        _assert_submittable(data, jira)
        eligible = test_integrate.eligible_repos(root, data)
        if data.get("phase") == "testing":
            if not eligible:
                raise ReportRejected(f"{jira} is already in testing")
            if not force_all and not test_integrate.has_new_changes(root, jira, data):
                return dict(data)

    outcome = test_integrate.integrate_test_branches(
        root,
        jira,
        remote=remote,
        ai_resolve=ai_resolve,
        force_all=force_all,
        on_progress=on_progress,
        runner_factory=runner_factory,
    )

    with st.jira_lock(jira):
        latest = st.load(root, jira)
        latest = st.sync_tickets(latest, load_tickets(paths.req_dir(root, jira)))
        st.refresh_ready(latest)
        _assert_submittable(latest, jira, recheck=True)
        if latest.get("phase") not in {"frozen", "testing"}:
            raise ReportRejected(
                f"{jira} phase changed to {latest.get('phase')!r} during submit-test; aborting"
            )
        slot = _test_slot(latest)
        integration = slot.get("integration")
        if not isinstance(integration, dict):
            integration = {}
        integration.update(outcome.status_map())
        slot["integration"] = integration
        if not outcome.ok:
            st.save(root, jira, latest)
            raise ReportRejected(outcome.error or "submit-test integration failed")
        slot["status"] = "awaiting"
        latest["phase"] = "testing"
        st.save(root, jira, latest)
        return dict(latest)


def namespace_findings(batch_id: str, findings: list[Finding]) -> list[dict[str, Any]]:
    ids = [f.id or f"F{i + 1}" for i, f in enumerate(findings)]
    raw_ids = set(ids)

    def ns(dep: str) -> str:
        if dep in raw_ids:
            return f"{batch_id}:{dep}"
        return dep

    out: list[dict[str, Any]] = []
    for f, fid in zip(findings, ids, strict=True):
        item: dict[str, Any] = {
            "id": f"{batch_id}:{fid}",
            "title": f.title or fid,
            "detail": f.detail,
            "repo": f.repo,
            "depends_on": [ns(d) for d in f.depends_on],
        }
        if f.parallel is not None:
            item["parallel"] = f.parallel
        if f.defect_class:
            item["defect_class"] = f.defect_class
        out.append(item)
    return out


def parse_inbound(payload: dict[str, Any], default_source: str) -> InboundReport:
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        raise ReportRejected(f"verdict must be one of {', '.join(VERDICTS)}")
    body = str(payload.get("body") or "").strip()
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
        deps = item.get("depends_on") or []
        if not isinstance(deps, (str, list, tuple)):
            raise ReportRejected(f"findings[{i}].depends_on must be a list or string")
        parallel_raw = item.get("parallel")
        # Absent → None; present but unrecognised → False (unchanged semantics).
        parallel = as_bool(parallel_raw, default=False) if parallel_raw is not None else None
        repo = str(item.get("repo") or "").strip()
        if verdict in {"failed", "blocked"} and not repo:
            raise ReportRejected(f"findings[{i}].repo is required")
        findings.append(
            Finding(
                id=str(item.get("id") or f"F{i + 1}"),
                title=str(item.get("title") or ""),
                detail=str(item.get("detail") or ""),
                repo=repo,
                depends_on=as_list(deps),
                parallel=parallel,
            )
        )
    if verdict in {"failed", "blocked"} and not findings:
        raise ReportRejected("findings are required when submitting bugs")
    if not body:
        body = str(payload.get("summary") or "").strip()
    if body and not body.endswith("\n"):
        body += "\n"
    return InboundReport(
        verdict=verdict,
        body=body,
        summary=str(payload.get("summary") or "").strip(),
        source=source,
        findings=findings,
    )


def accept_test_report(
    root: Path,
    jira: str,
    report: InboundReport,
    *,
    spawn: bool = True,
) -> dict[str, Any]:
    req = paths.req_dir(root, jira)
    if not req.is_dir():
        raise FileNotFoundError(f"missing {req}")
    when = _now()
    received_at = when.isoformat(timespec="seconds")
    with st.jira_lock(jira):
        data = st.load(root, jira)
        if st.test_passed(data):
            raise ReportRejected(f"{jira} already has a passed test round")
        if data.get("contract_review") != "passed":
            raise ReportRejected(
                f"{jira} contract_review is {data.get('contract_review')!r}; "
                "must be passed before submitting bugs"
            )
        if data.get("phase") not in {"testing", "frozen"}:
            raise ReportRejected(
                f"{jira} phase={data.get('phase')}; submit bugs after freeze "
                "(and preferably after submit-test)"
            )
        if report.verdict in {"failed", "blocked"} and not report.findings:
            raise ReportRejected("findings are required when submitting bugs")
        if report.verdict == "passed":
            if data.get("phase") != "testing":
                raise ReportRejected(
                    f"{jira} phase={data.get('phase')}; run `dev-yard req submit-test` "
                    "before passing"
                )
            parsed = load_tickets(req)
            data = st.sync_tickets(data, parsed)
            if not st.all_done(data):
                raise ReportRejected(
                    f"{jira} still has open tickets; finish bug tickets before passing"
                )
        archive = report_dir(root, jira)
        archive.mkdir(parents=True, exist_ok=True)
        rid = _report_id(when, archive)
        stored_findings = (
            []
            if report.verdict == "passed"
            else namespace_findings(rid, report.findings)
        )
        header = {
            "id": rid,
            "verdict": report.verdict,
            "source": report.source,
            "received_at": received_at,
            "summary": report.summary,
            "findings": stored_findings,
        }
        front = yaml.safe_dump(header, sort_keys=False, allow_unicode=True).rstrip()
        text = f"---\n{front}\n---\n\n{report.body}"
        if not text.endswith("\n"):
            text += "\n"
        (archive / f"{rid}.md").write_text(text, encoding="utf-8")
        slot = _test_slot(data)
        slot["status"] = "received" if report.verdict != "passed" else "passed"
        slot["latest_id"] = rid
        slot["latest_verdict"] = report.verdict
        slot["source"] = report.source
        slot["received_at"] = received_at
        slot["summary"] = report.summary
        slot["findings"] = stored_findings
        if report.verdict == "passed":
            data["phase"] = "done"
            slot["status"] = "passed"
        st.save(root, jira, data)
        if spawn and report.verdict in {"failed", "blocked"}:
            # Manual submissions (web 提 bug form / CLI / inbound API) still open
            # tickets. A `req test` run passes spawn=False: it only records the
            # report, and the human clicks 下 bug on the cases they judge real.
            from dev_yard.bug_tickets import spawn_fix_tickets

            spawn_fix_tickets(root, jira, "test")
            data = st.load(root, jira)
        return dict(data)


