from __future__ import annotations

import asyncio
import hmac
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import markdown
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from dev_yard import __version__, gitops, paths, service as yard_service
from dev_yard.gitops import GitError
from dev_yard.service import extract_req_key
from dev_yard.config import PI_STAGES, PiSettings, StageModel, load_pi_settings, save_pi_settings
from dev_yard.pi_catalog import list_pi_catalog
from dev_yard.web.board import (
    DOC_FILES,
    PIPELINE,
    asset_file,
    list_repos,
    list_requirements,
    requirement_detail,
    save_doc,
)
from dev_yard.pi_session import cwd_is_under_root, load_conversation
from dev_yard.web.jobs import BoardSse, JobRunner, JobSse, PiChatSse, _pi_run_until
from dev_yard.web.sanitize import sanitize_html

HERE = Path(__file__).parent
SPA = HERE / "spa"
ACTIONS = {
    "open",
    "grill",
    "spec",
    "tickets",
    "freeze",
    "implement",
    "review",
    "contract",
    "fix-contract",
    "submit-test",
    "fix-test",
}
STEP_LABELS = {
    "open": "抽取",
    "grill": "对齐",
    "spec": "规约",
    "tickets": "拆票",
    "freeze": "冻结",
    "implement": "实现",
    "review": "审查",
    "testing": "提测",
    "done": "完成",
}
ACTION_LABELS = {
    "open": "抽取需求",
    "grill": "对齐",
    "spec": "写规约",
    "tickets": "拆票",
    "freeze": "冻结 worktree",
    "implement": "实现",
    "review": "审查",
    "contract": "契约审查",
    "fix-contract": "按契约修",
    "submit-test": "提测",
    "fill-test-report": "填写测试报告",
    "fix-test": "按测试报告修",
}

_ASSET_SRC = re.compile(r'src=(["\'])(?:\./)?assets/([^"\']+)\1')
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class GrillAnswersIn(BaseModel):
    answers: list[dict[str, Any]] = Field(default_factory=list)


class OpenIn(BaseModel):
    jira: str = ""
    key: str = ""
    source: str = "pi"
    target: str = ""
    payload: str = ""
    force: bool = False


class RepoAddIn(BaseModel):
    alias: str = ""
    url: str
    default_base: str = "main"
    role: str = "svc"
    path: str = ""
    provider: str = ""
    model: str = ""


class RepoPiIn(BaseModel):
    provider: str = ""
    model: str = ""


class ActionIn(BaseModel):
    ticket_id: str = ""
    force: bool = False
    source: str = "pi"


class DocSaveIn(BaseModel):
    body: str = ""


class TicketReviewIn(BaseModel):
    verdict: str
    summary: str = ""
    auto_implement: bool = False


class TestReportIn(BaseModel):
    verdict: str
    body: str
    summary: str = ""
    source: str = ""
    findings: list[dict[str, Any]] = Field(default_factory=list)


class StageModelIn(BaseModel):
    provider: str = ""
    model: str = ""


class PiSettingsIn(BaseModel):
    provider: str = ""
    model: str = ""
    stages: dict[str, StageModelIn] = Field(default_factory=dict)


def _bearer_ok(authorization: str | None, token: str) -> bool:
    raw = (authorization or "").strip()
    scheme, _, rest = raw.partition(" ")
    if scheme.lower() != "bearer" or not rest:
        return False
    got = rest.strip().encode("utf-8")
    want = token.encode("utf-8")
    if len(got) != len(want):
        return False
    return hmac.compare_digest(got, want)


def check_bind_host(host: str, allow_remote: bool = False) -> None:
    h = (host or "").strip().lower()
    if h in _LOOPBACK or allow_remote:
        return
    raise ValueError(
        f"refusing to bind {host!r}: this console has no auth and can run `pi --approve`. "
        "Use 127.0.0.1, or pass --allow-remote."
    )


def render_markdown(text: str, jira: str) -> str:
    html = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br"],
    )
    html = sanitize_html(html)
    quoted = quote(jira, safe="")

    def repl(m: re.Match[str]) -> str:
        q, name = m.group(1), Path(m.group(2)).name
        return f"src={q}/r/{quoted}/assets/{quote(name)}{q}"

    return _ASSET_SRC.sub(repl, html)


def create_app(root: Path, job_runner: JobRunner | None = None, sync_jobs: bool = False) -> FastAPI:
    root = root.resolve()
    jobs = job_runner or JobRunner(root, sync=sync_jobs)
    if not jobs.sync:
        jobs.resume_pending_grills()
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals["step_labels"] = STEP_LABELS
    templates.env.globals["action_labels"] = ACTION_LABELS
    templates.env.globals["doc_files"] = DOC_FILES
    templates.env.globals["pipeline"] = PIPELINE

    app = FastAPI(title="dev-yard", version=__version__, docs_url="/api/docs", redoc_url=None)

    def spa_index():
        index = SPA / "index.html"
        if not index.is_file():
            raise HTTPException(503, "frontend not built; run pnpm --dir web build")
        return FileResponse(index)

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(HERE / "static" / "favicon.svg")

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    spa_assets = SPA / "assets"
    if spa_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(spa_assets)), name="spa-assets")

    def base(request: Request, **extra):
        ctx = {
            "request": request,
            "root": str(root),
            "root_name": root.name,
            "running_jobs": jobs.running_brief(),
        }
        ctx.update(extra)
        return ctx

    def detail_or_404(jira: str):
        detail = requirement_detail(root, jira)
        if detail is None:
            raise HTTPException(404, f"no requirement {jira}")
        return detail

    def _submit_action(
        action: str,
        jira: str,
        ids: list[str] | None,
        extra: dict,
    ):
        if action in {"implement", "review", "fix-contract", "fix-test"}:
            busy = jobs.busy_tickets(jira, action)
            if busy is None:
                raise ValueError(f"{jira} already has a running job")
            if not ids:
                detail = detail_or_404(jira)
                if action == "fix-contract":
                    tickets = {t.id: t for t in detail.tickets}
                    wanted = yard_service.from_contract_ids(tickets, None)
                elif action == "fix-test":
                    from dev_yard.test_report import from_test_ids

                    tickets = {t.id: t for t in detail.tickets}
                    findings = (detail.test or {}).get("findings") or []
                    wanted = from_test_ids(tickets, None, findings)
                else:
                    wanted = [
                        t.id
                        for t in detail.tickets
                        if (t.can_implement if action == "implement" else t.can_review)
                    ]
                wanted = [tid for tid in wanted if tid not in busy]
                if not wanted:
                    raise ValueError(
                        f"{jira} already has a running job ({action})"
                        if busy
                        else (
                            "没有处于 implemented 的票"
                            if action == "review"
                            else "没有可运行的票"
                        )
                    )
                ids = wanted
            else:
                ids = [tid for tid in ids if tid not in busy]
                if not ids:
                    raise ValueError(f"{jira} already has a running job ({action})")
            claimed = yard_service.claim_run(root, jira, action, ids)
            if not claimed:
                raise ValueError(
                    "没有处于 implemented 的票"
                    if action == "review"
                    else "没有可运行的票"
                )
            return [
                jobs.submit(action, jira, ticket_ids=[tid], extra=extra)
                for tid in claimed
            ]
        return [jobs.submit(action, jira, ticket_ids=ids, extra=extra)]

    def _jobs_out(submitted):
        return {"jobs": [j.snapshot() for j in submitted]}

    def _requirement_payload(detail):
        return {
            "jira": detail.jira,
            "title": detail.title,
            "phase": detail.phase,
            "next": detail.next_label,
            "contract": detail.contract,
            "contract_summary": detail.contract_summary,
            "test": detail.test,
            "worktrees": detail.worktrees,
            "assets": detail.assets,
            "steps": [
                {"id": s.id, "done": s.done, "current": s.current} for s in detail.steps
            ],
            "tickets": [
                {
                    "id": t.id,
                    "title": t.title,
                    "repo": t.repo,
                    "state": t.state,
                    "depends_on": t.depends_on,
                    "parallel": t.parallel,
                    "can_implement": t.can_implement,
                    "can_review": t.can_review,
                    "last_summary": t.last_summary,
                }
                for t in detail.tickets
            ],
            "actions": [
                {
                    "id": a.id,
                    "label": a.label,
                    "enabled": a.enabled,
                    "reason": a.reason,
                }
                for a in detail.actions
            ],
            "docs": [
                {
                    "slug": d.slug,
                    "filename": d.filename,
                    "filled": d.filled,
                    "exists": d.exists,
                }
                for d in detail.docs
            ],
        }

    def _doc_payload(detail, slug: str):
        doc = next((d for d in detail.docs if d.slug == slug), None)
        if doc is None:
            raise HTTPException(404, f"unknown doc {slug}")
        return {
            "jira": detail.jira,
            "slug": doc.slug,
            "filename": doc.filename,
            "filled": doc.filled,
            "exists": doc.exists,
            "text": doc.text,
            "html": render_markdown(doc.text, detail.jira),
        }

    @app.get("/")
    def dashboard():
        return spa_index()

    @app.get("/repos")
    def repos_page():
        return spa_index()

    @app.post("/repos")
    def repos_add(
        alias: str = Form(""),
        url: str = Form(...),
        default_base: str = Form("main"),
        role: str = Form("svc"),
        path: str = Form(""),
        provider: str = Form(""),
        model: str = Form(""),
    ):
        try:
            submitted = jobs.submit(
                "repo_add",
                "_repo_",
                extra={
                    "alias": alias.strip(),
                    "url": url.strip(),
                    "default_base": default_base.strip() or "main",
                    "role": role.strip() or "svc",
                    "path": path.strip() or None,
                    "provider": provider.strip() or None,
                    "model": model.strip() or None,
                },
            )
        except ValueError as e:
            return RedirectResponse(f"/repos?error={quote(str(e))}", status_code=303)
        return RedirectResponse(f"/repos?job={submitted.id}", status_code=303)

    @app.get("/settings")
    def settings_page():
        return spa_index()

    @app.get("/open")
    def open_page():
        return spa_index()

    @app.post("/open")
    def open_submit(
        jira: str = Form(""),
        key: str = Form(""),
        source: str = Form("pi"),
        target: str = Form(""),
        payload: str = Form(""),
        force: str = Form(""),
    ):
        raw_target = target or jira or key
        req_key = (key or jira).strip()
        if not req_key:
            req_key = extract_req_key(raw_target)
        if not req_key:
            return RedirectResponse("/open?error=Missing+requirement+key+or+URL", status_code=303)
        try:
            paths.req_dir(root, req_key)
        except ValueError as e:
            return RedirectResponse(f"/open?error={quote(str(e))}", status_code=303)
        try:
            job = jobs.submit(
                "open",
                req_key,
                extra={
                    "source": source,
                    "target": raw_target,
                    "payload": payload,
                    "force": bool(force),
                },
            )
        except ValueError as e:
            return RedirectResponse(f"/open?error={quote(str(e))}", status_code=303)
        return RedirectResponse(f"/r/{quote(req_key)}?job={job.id}", status_code=303)

    @app.get("/r/{jira}")
    def req_page(jira: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        return spa_index()

    @app.get("/r/{jira}/docs/{slug}")
    def doc_page(jira: str, slug: str):
        if slug not in DOC_FILES:
            raise HTTPException(404, f"unknown doc {slug}")
        return spa_index()

    @app.post("/r/{jira}/docs/{slug}")
    def doc_save(jira: str, slug: str, body: str = Form("")):
        try:
            save_doc(root, jira, slug, body)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from e
        return RedirectResponse(f"/r/{quote(jira)}/docs/{slug}?saved=1", status_code=303)

    @app.get("/r/{jira}/assets/{name}")
    def asset(jira: str, name: str):
        try:
            path = asset_file(root, jira, name)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, str(e)) from e
        return FileResponse(path)

    @app.post("/r/{jira}/actions/{action}")
    def run_action(
        jira: str,
        action: str,
        ticket_id: str = Form(""),
        force: str = Form(""),
        source: str = Form("pi"),
    ):
        if action not in ACTIONS:
            raise HTTPException(400, f"unknown action {action}")
        ids = [ticket_id] if ticket_id.strip() else None
        extra = {"force": bool(force), "source": source}
        try:
            submitted = _submit_action(action, jira, ids, extra)
        except ValueError as e:
            return RedirectResponse(
                f"/r/{quote(jira)}?error={quote(str(e))}", status_code=303
            )
        if len(submitted) == 1:
            return RedirectResponse(
                f"/r/{quote(jira)}?job={submitted[0].id}", status_code=303
            )
        return RedirectResponse(f"/r/{quote(jira)}", status_code=303)

    @app.get("/api/meta")
    def api_meta():
        return {"root": str(root), "root_name": root.name, "version": __version__}

    @app.get("/api/requirements")
    def api_list():
        items = list_requirements(root)
        return [
            {
                "jira": i.jira,
                "title": i.title,
                "phase": i.phase,
                "next": i.next_label,
                "tickets": i.ticket_total,
                "done": i.ticket_done,
            }
            for i in items
        ]

    @app.get("/api/requirements/{jira}")
    def api_detail(jira: str):
        return _requirement_payload(detail_or_404(jira))

    @app.delete("/api/requirements/{jira}")
    def api_delete(jira: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        for job in jobs.running():
            if job.jira.upper() == jira.strip().upper():
                raise HTTPException(
                    409, f"{jira} 有进行中的任务（{job.action}），结束后再删"
                )
        try:
            yard_service.req_delete(root, jira)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except (ValueError, gitops.GitError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "jira": jira}

    @app.get("/api/requirements/{jira}/docs/{slug}")
    def api_doc(jira: str, slug: str):
        if slug not in DOC_FILES:
            raise HTTPException(404, f"unknown doc {slug}")
        return _doc_payload(detail_or_404(jira), slug)

    @app.get("/api/requirements/{jira}/tickets/{ticket_id}/diff")
    def api_ticket_diff(jira: str, ticket_id: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        try:
            return yard_service.ticket_diff(root, jira, ticket_id)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/requirements/{jira}/tickets/{ticket_id}/review")
    def api_ticket_review_override(jira: str, ticket_id: str, payload: TicketReviewIn):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        try:
            updated = yard_service.ticket_review_override(
                root,
                jira,
                ticket_id,
                verdict=payload.verdict,
                summary=payload.summary,
            )
        except (ValueError, GitError, KeyError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from e

        job_snapshots = []
        norm = payload.verdict.strip().lower()
        if norm in {"failed", "fail", "blocked"} and payload.auto_implement:
            try:
                submitted = _submit_action("implement", jira, [ticket_id], extra={})
                job_snapshots = [j.snapshot() for j in submitted]
            except Exception:
                pass

        return {
            "jira": jira,
            "ticket_id": ticket_id,
            "ticket": updated,
            "jobs": job_snapshots,
        }

    @app.get("/api/requirements/{jira}/diff")
    def api_req_diff(jira: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        try:
            return yard_service.requirement_diff(root, jira)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.put("/api/requirements/{jira}/docs/{slug}")
    def api_doc_save(jira: str, slug: str, payload: DocSaveIn):
        try:
            save_doc(root, jira, slug, payload.body)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from e
        return _doc_payload(detail_or_404(jira), slug)

    @app.post("/api/open")
    def api_open(payload: OpenIn):
        raw_target = payload.target or payload.jira or payload.key
        req_key = (payload.key or payload.jira).strip()
        if not req_key:
            req_key = extract_req_key(raw_target)
        if not req_key:
            raise HTTPException(400, "Requirement key or target URL is required")
        try:
            paths.req_dir(root, req_key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        try:
            job = jobs.submit(
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
        return _jobs_out([job])

    @app.post("/api/requirements/{jira}/actions/{action}")
    def api_run_action(jira: str, action: str, payload: ActionIn | None = None):
        if action not in ACTIONS:
            raise HTTPException(400, f"unknown action {action}")
        body = payload or ActionIn()
        ids = [body.ticket_id] if body.ticket_id.strip() else None
        extra = {"force": body.force, "source": body.source}
        try:
            submitted = _submit_action(action, jira, ids, extra)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return _jobs_out(submitted)

    def _accept_report(jira: str, payload: TestReportIn, default_source: str):
        from dev_yard.test_report import (
            ReportRejected,
            accept_test_report,
            parse_inbound,
        )

        try:
            report = parse_inbound(payload.model_dump(), default_source)
            data = accept_test_report(root, jira, report)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ReportRejected as e:
            msg = str(e)
            code = 409 if "phase=" in msg or "only accepted" in msg else 422
            raise HTTPException(code, msg) from e
        return {
            "jira": jira,
            "phase": data.get("phase"),
            "test": data.get("test"),
        }

    @app.post("/api/requirements/{jira}/test-report")
    def api_test_report_local(jira: str, payload: TestReportIn):
        if payload.source in ("", "api"):
            payload = payload.model_copy(update={"source": "web"})
        return _accept_report(jira, payload, "web")

    @app.post("/api/inbound/reqs/{jira}/test-report")
    def api_test_report_inbound(jira: str, request: Request, payload: TestReportIn):
        token = os.environ.get("YARD_TEST_REPORT_TOKEN") or ""
        if not token.strip():
            raise HTTPException(503, "YARD_TEST_REPORT_TOKEN is not set")
        if not _bearer_ok(request.headers.get("authorization"), token.strip()):
            raise HTTPException(401, "invalid or missing bearer token")
        if not payload.source:
            payload = payload.model_copy(update={"source": "api"})
        return _accept_report(jira, payload, "api")

    @app.get("/api/jobs")
    def api_jobs():
        return jobs.running_brief()

    @app.get("/api/jobs/events")
    async def api_jobs_events():
        async def gen():
            sse = BoardSse(jobs)
            seq = jobs.board_seq()
            while True:
                frames, _seq = sse.poll()
                for frame in frames:
                    yield frame
                new_seq = await asyncio.to_thread(jobs.wait_board, seq, 5.0)
                if new_seq <= seq:
                    yield ": ping\n\n"
                seq = new_seq

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job.snapshot()

    def _pi_run_or_404(job_id: str, run_index: int):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        snap = job.snapshot()
        runs = snap.get("pi_runs") or []
        if run_index < 0 or run_index >= len(runs):
            raise HTTPException(404, "unknown pi run")
        run = runs[run_index]
        cwd = Path(run["cwd"])
        if not cwd_is_under_root(cwd, root):
            raise HTTPException(404, "unknown pi run")
        return job, snap, runs, run

    @app.get("/api/jobs/{job_id}/pi/{run_index}")
    def api_job_pi(job_id: str, run_index: int, offset: int = 0):
        job, snap, runs, run = _pi_run_or_404(job_id, run_index)
        data = load_conversation(
            Path(run["cwd"]),
            root=root,
            started_at=run.get("started_at"),
            until=_pi_run_until(runs, run_index),
            offset=offset,
        )
        data.update(
            job_id=job.id,
            run=run_index,
            started_at=run.get("started_at"),
            job_state=snap["state"],
        )
        return data

    @app.get("/api/jobs/{job_id}/pi/{run_index}/events")
    async def api_job_pi_events(job_id: str, run_index: int):
        job, _snap, _runs, _run = _pi_run_or_404(job_id, run_index)

        async def gen():
            sse = PiChatSse(job, run_index, root)
            while True:
                frames, done, seq = sse.poll()
                for frame in frames:
                    yield frame
                if done:
                    return
                new_seq = await asyncio.to_thread(job.wait_seq, seq, 0.4)
                if new_seq <= seq:
                    yield ": ping\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/jobs/{job_id}/events")
    async def api_job_events(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")

        async def gen():
            sse = JobSse(job)
            while True:
                frames, done, seq = sse.poll()
                for frame in frames:
                    yield frame
                if done:
                    return
                new_seq = await asyncio.to_thread(job.wait_seq, seq, 5.0)
                if new_seq <= seq:
                    yield ": ping\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/jobs/{job_id}/answers")
    def api_job_answers(job_id: str, payload: GrillAnswersIn):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        try:
            job.submit_answers(payload.answers)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    def _pi_settings_out():
        s = load_pi_settings(root)
        stages = {
            name: {
                "provider": (s.stages.get(name).provider if name in s.stages else None) or "",
                "model": (s.stages.get(name).model if name in s.stages else None) or "",
            }
            for name in PI_STAGES
        }
        return {
            "provider": s.provider or "",
            "model": s.model or "",
            "stages": stages,
            "stage_ids": list(PI_STAGES),
            "catalog": list_pi_catalog(),
        }

    @app.get("/api/pi")
    def api_pi_get():
        return _pi_settings_out()

    @app.put("/api/pi")
    def api_pi_put(payload: PiSettingsIn):
        unknown = [n for n in payload.stages if n not in PI_STAGES]
        if unknown:
            raise HTTPException(400, f"unknown pi stages: {', '.join(unknown)}")
        try:
            save_pi_settings(
                root,
                PiSettings(
                    provider=payload.provider.strip() or None,
                    model=payload.model.strip() or None,
                    stages={
                        name: StageModel(
                            provider=entry.provider.strip() or None,
                            model=entry.model.strip() or None,
                        )
                        for name, entry in payload.stages.items()
                    },
                ),
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return _pi_settings_out()

    @app.get("/api/repos")
    def api_repos():
        return list_repos(root)

    @app.post("/api/repos")
    def api_repos_add(payload: RepoAddIn):
        try:
            submitted = jobs.submit(
                "repo_add",
                "_repo_",
                extra={
                    "alias": payload.alias.strip(),
                    "url": payload.url.strip(),
                    "default_base": payload.default_base.strip() or "main",
                    "role": payload.role.strip() or "svc",
                    "path": payload.path.strip() or None,
                    "provider": payload.provider.strip() or None,
                    "model": payload.model.strip() or None,
                },
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return _jobs_out([submitted])

    @app.put("/api/repos/{alias}")
    def api_repos_set_pi(alias: str, payload: RepoPiIn):
        try:
            yard_service.repo_set_pi(
                root,
                alias,
                payload.provider.strip() or None,
                payload.model.strip() or None,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return list_repos(root)

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context=base(request, status=exc.status_code, detail=exc.detail),
            status_code=exc.status_code,
        )

    return app


def serve(
    root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    allow_remote: bool = False,
) -> None:
    import threading
    import webbrowser

    import uvicorn

    check_bind_host(host, allow_remote=allow_remote)
    app = create_app(root)
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    # SSE 连接是无限循环，优雅关闭时会一直"Waiting for connections to close"；
    # 设一个上限，超时后 uvicorn 会强制取消在途任务。
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_graceful_shutdown=3)
