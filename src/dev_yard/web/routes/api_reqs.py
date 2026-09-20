"""Requirement JSON API: list/detail, docs, diffs, reviews, actions, reports."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

from dev_yard import __version__, gitops, paths
from dev_yard import service as yard_service
from dev_yard.gitops import GitError
from dev_yard.service import extract_req_key
from dev_yard.web.board import DOC_FILES, list_requirements
from dev_yard.web.context import AppContext, render_markdown
from dev_yard.web.schemas import (
    ActionIn,
    ContractReviewIn,
    DocSaveIn,
    OpenIn,
    TestReportIn,
    TicketReviewIn,
)


def _accept_report(ctx: AppContext, jira: str, payload: TestReportIn, default_source: str):
    from dev_yard.test_report import ReportRejected, accept_test_report, parse_inbound

    try:
        report = parse_inbound(payload.model_dump(), default_source)
        data = accept_test_report(ctx.root, jira, report)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ReportRejected as e:
        msg = str(e)
        code = (
            409
            if any(
                s in msg
                for s in (
                    "phase=",
                    "only accepted",
                    "already",
                    "contract_review",
                    "still has open",
                )
            )
            else 422
        )
        raise HTTPException(code, msg) from e
    return {"jira": jira, "phase": data.get("phase"), "test": data.get("test")}


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/meta")
    def api_meta():
        return {"root": str(ctx.root), "root_name": ctx.root.name, "version": __version__}

    @router.get("/api/requirements")
    def api_list():
        return [
            {
                "jira": i.jira,
                "title": i.title,
                "phase": i.phase,
                "next": i.next_label,
                "tickets": i.ticket_total,
                "done": i.ticket_done,
                "contract": i.contract,
            }
            for i in list_requirements(ctx.root)
        ]

    @router.get("/api/requirements/{jira}")
    def api_detail(jira: str):
        return ctx.requirement_payload(ctx.detail_or_404(jira))

    @router.delete("/api/requirements/{jira}")
    def api_delete(jira: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        for job in ctx.jobs.running():
            if job.jira.upper() == jira.strip().upper():
                raise HTTPException(
                    409, f"{jira} 有进行中的任务（{job.action}），结束后再删"
                )
        try:
            yard_service.req_delete(ctx.root, jira)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except (ValueError, gitops.GitError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "jira": jira}

    @router.get("/api/requirements/{jira}/docs/{slug}")
    def api_doc(jira: str, slug: str):
        if slug not in DOC_FILES:
            raise HTTPException(404, f"unknown doc {slug}")
        return ctx.doc_payload(ctx.detail_or_404(jira), slug)

    @router.put("/api/requirements/{jira}/docs/{slug}")
    def api_doc_save(jira: str, slug: str, payload: DocSaveIn):
        try:
            ctx.save_doc(jira, slug, payload.body)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from e
        return ctx.doc_payload(ctx.detail_or_404(jira), slug)

    @router.get("/api/requirements/{jira}/qa")
    def api_qa(jira: str):
        from dev_yard.qa_board import qa_page_payload

        ctx.detail_or_404(jira)
        payload = qa_page_payload(ctx.root, jira)
        for case in payload.get("cases") or []:
            body = case.get("body") or ""
            case["html"] = render_markdown(body, jira) if body else ""
        return payload

    @router.get("/api/requirements/{jira}/qa/cases/{case_id}")
    def api_qa_case(jira: str, case_id: str):
        from dev_yard.qa_board import qa_case_detail

        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        if (
            not case_id
            or case_id in {".", ".."}
            or "/" in case_id
            or "\\" in case_id
        ):
            raise HTTPException(404, "unknown case")
        ctx.detail_or_404(jira)
        data = qa_case_detail(ctx.root, jira, case_id)
        if data is None:
            raise HTTPException(404, f"unknown case {case_id}")
        body = data.pop("body", "")
        data["jira"] = jira
        data["html"] = render_markdown(body, jira) if body else ""
        return data

    @router.get("/api/requirements/{jira}/tickets/{ticket_id}/diff")
    def api_ticket_diff(jira: str, ticket_id: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        try:
            return yard_service.ticket_diff(ctx.root, jira, ticket_id)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/api/requirements/{jira}/tickets/{ticket_id}/review")
    def api_ticket_review_override(jira: str, ticket_id: str, payload: TicketReviewIn):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        try:
            updated = yard_service.ticket_review_override(
                ctx.root,
                jira,
                ticket_id,
                verdict=payload.verdict,
                summary=payload.summary,
            )
        except (ValueError, GitError, KeyError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from e

        job_snapshots = []
        auto_error = None
        norm = payload.verdict.strip().lower()
        if norm in {"failed", "fail", "blocked"} and payload.auto_implement:
            try:
                submitted = ctx.submit_action("implement", jira, [ticket_id], extra={})
                job_snapshots = [j.snapshot() for j in submitted]
            except ValueError as e:
                auto_error = str(e)

        return {
            "jira": jira,
            "ticket_id": ticket_id,
            "ticket": updated,
            "jobs": job_snapshots,
            "error": auto_error,
        }

    @router.delete("/api/requirements/{jira}/tickets/{ticket_id}")
    def api_ticket_delete(jira: str, ticket_id: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        for job in ctx.jobs.running():
            if job.jira.upper() != jira.strip().upper():
                continue
            if not job.ticket_ids or ticket_id in job.ticket_ids:
                raise HTTPException(409, f"{ticket_id} 有进行中的任务，结束后再删")
        try:
            return yard_service.ticket_delete(ctx.root, jira, ticket_id)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except (ValueError, gitops.GitError, OSError) as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/api/requirements/{jira}/contract/review")
    def api_contract_review_override(jira: str, payload: ContractReviewIn):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        try:
            updated = yard_service.contract_review_override(
                ctx.root,
                jira,
                verdict=payload.verdict,
                summary=payload.summary,
                findings=payload.findings or None,
            )
        except (ValueError, GitError, KeyError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from e

        job_snapshots = []
        auto_error = None
        norm = payload.verdict.strip().lower()
        if norm in {"failed", "fail", "blocked"} and payload.auto_implement:
            try:
                submitted = ctx.submit_action("fix-contract", jira, None, extra={})
                job_snapshots = [j.snapshot() for j in submitted]
            except ValueError as e:
                auto_error = str(e)

        return {
            "jira": jira,
            "contract": updated,
            "jobs": job_snapshots,
            "error": auto_error,
        }

    @router.get("/api/requirements/{jira}/diff")
    def api_req_diff(jira: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        try:
            return yard_service.requirement_diff(ctx.root, jira)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/api/open")
    def api_open(payload: OpenIn):
        raw_target = payload.target or payload.jira or payload.key
        req_key = (payload.key or payload.jira).strip()
        if not req_key:
            req_key = extract_req_key(raw_target)
        if not req_key:
            raise HTTPException(400, "Requirement key or target URL is required")
        try:
            paths.req_dir(ctx.root, req_key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        try:
            job = ctx.jobs.submit(
                "open",
                req_key,
                extra={
                    "source": payload.source,
                    "target": raw_target,
                    "payload": payload.payload,
                    "force": payload.force,
                },
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return ctx.jobs_out([job])

    @router.post("/api/requirements/{jira}/actions/{action}")
    def api_run_action(jira: str, action: str, payload: ActionIn | None = None):
        if not ctx.known_action(action):
            raise HTTPException(400, f"unknown action {action}")
        body = payload or ActionIn()
        ids = [body.ticket_id] if body.ticket_id.strip() else None
        extra = {
            "force": body.force,
            "source": body.source,
            "remote": body.remote,
            "repos": body.repos,
            "strategy": body.strategy,
            "env": body.env,
            "resume": body.resume,
            "approve": body.approve,
            "redesign": body.redesign,
            "feedback": body.feedback,
            "resolve": body.resolve,
        }
        try:
            submitted = ctx.submit_action(action, jira, ids, extra)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return ctx.jobs_out(submitted)

    @router.post("/api/requirements/{jira}/test-report")
    def api_test_report_local(jira: str, payload: TestReportIn):
        if payload.source in ("", "api"):
            payload = payload.model_copy(update={"source": "web"})
        return _accept_report(ctx, jira, payload, "web")

    @router.post("/api/inbound/reqs/{jira}/test-report")
    def api_test_report_inbound(jira: str, request: Request, payload: TestReportIn):
        from dev_yard.web.security import bearer_ok

        token = os.environ.get("YARD_TEST_REPORT_TOKEN") or ""
        if not token.strip():
            raise HTTPException(503, "YARD_TEST_REPORT_TOKEN is not set")
        if not bearer_ok(request.headers.get("authorization"), token.strip()):
            raise HTTPException(401, "invalid or missing bearer token")
        if not payload.source:
            payload = payload.model_copy(update={"source": "api"})
        return _accept_report(ctx, jira, payload, "api")

    return router
