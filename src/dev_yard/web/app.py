from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import markdown
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from dev_yard import paths, service as yard_service
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
ACTIONS = {"open", "grill", "spec", "tickets", "freeze", "implement", "review", "contract"}
STEP_LABELS = {
    "open": "抽取",
    "grill": "Grill",
    "spec": "Spec",
    "tickets": "拆票",
    "freeze": "冻结",
    "implement": "实现",
    "review": "审查",
    "done": "完成",
}
ACTION_LABELS = {
    "open": "抽取需求",
    "grill": "Grill",
    "spec": "写 Spec",
    "tickets": "拆票",
    "freeze": "冻结 worktree",
    "implement": "实现",
    "review": "审查",
    "contract": "契约审查",
}

_ASSET_SRC = re.compile(r'src=(["\'])(?:\./)?assets/([^"\']+)\1')
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class GrillAnswersIn(BaseModel):
    answers: list[dict[str, Any]] = Field(default_factory=list)


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

    app = FastAPI(title="dev-yard", docs_url="/api/docs", redoc_url=None)
    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(HERE / "static" / "favicon.svg")

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

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
        if action in {"implement", "review"}:
            if not ids:
                detail = detail_or_404(jira)
                wanted = [
                    t.id
                    for t in detail.tickets
                    if (t.can_implement if action == "implement" else t.can_review)
                ]
                busy = jobs.busy_tickets(jira, action)
                if busy is None:
                    raise ValueError(f"{jira} already has a running job")
                wanted = [tid for tid in wanted if tid not in busy]
                if not wanted:
                    if busy:
                        raise ValueError(
                            f"{jira} already has a running job ({action})"
                        )
                    return [jobs.submit(action, jira, ticket_ids=None, extra=extra)]
                ids = wanted
            yard_service.claim_run(root, jira, action, ids)
            return [
                jobs.submit(action, jira, ticket_ids=[tid], extra=extra)
                for tid in ids
            ]
        return [jobs.submit(action, jira, ticket_ids=ids, extra=extra)]

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=base(request, items=list_requirements(root), repos=list_repos(root)),
        )

    @app.get("/repos", response_class=HTMLResponse)
    def repos_page(request: Request, job: str | None = None):
        job_obj = jobs.get(job) if job else None
        return templates.TemplateResponse(
            request=request,
            name="repos.html",
            context=base(
                request,
                repos=list_repos(root),
                job=job_obj.snapshot() if job_obj else None,
            ),
        )

    @app.post("/repos")
    def repos_add(
        alias: str = Form(""),
        url: str = Form(...),
        default_base: str = Form("main"),
        role: str = Form("svc"),
        path: str = Form(""),
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
                },
            )
        except ValueError as e:
            return RedirectResponse(f"/repos?error={quote(str(e))}", status_code=303)
        return RedirectResponse(f"/repos?job={submitted.id}", status_code=303)

    @app.get("/open", response_class=HTMLResponse)
    def open_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="open.html", context=base(request)
        )

    @app.post("/open")
    def open_submit(
        jira: str = Form(...),
        source: str = Form("pi"),
        force: str = Form(""),
    ):
        key = jira.strip().upper()
        try:
            paths.req_dir(root, key)
        except ValueError as e:
            return RedirectResponse(f"/open?error={quote(str(e))}", status_code=303)
        try:
            job = jobs.submit(
                "open",
                key,
                extra={"source": source, "force": bool(force)},
            )
        except ValueError as e:
            return RedirectResponse(f"/open?error={quote(str(e))}", status_code=303)
        return RedirectResponse(f"/r/{quote(key)}?job={job.id}", status_code=303)

    @app.get("/r/{jira}", response_class=HTMLResponse)
    def req_page(request: Request, jira: str, job: str | None = None):
        detail = requirement_detail(root, jira)
        page_job_objs = jobs.page_jobs(jira, job)
        job_obj = page_job_objs[0] if page_job_objs else None
        if detail is None and job_obj is None:
            raise HTTPException(404, f"no requirement {jira}")
        return templates.TemplateResponse(
            request=request,
            name="requirement.html",
            context=base(
                request,
                jira=jira,
                detail=detail,
                job=job_obj.snapshot() if job_obj else None,
                page_jobs=[j.snapshot() for j in page_job_objs],
            ),
        )

    @app.get("/r/{jira}/docs/{slug}", response_class=HTMLResponse)
    def doc_page(request: Request, jira: str, slug: str, edit: str | None = None):
        if slug not in DOC_FILES:
            raise HTTPException(404, f"unknown doc {slug}")
        detail = detail_or_404(jira)
        doc = next(d for d in detail.docs if d.slug == slug)
        return templates.TemplateResponse(
            request=request,
            name="doc.html",
            context=base(
                request,
                jira=jira,
                detail=detail,
                doc=doc,
                rendered=render_markdown(doc.text, jira),
                editing=bool(edit),
            ),
        )

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

    @app.get("/api/requirements")
    def api_list():
        items = list_requirements(root)
        return [
            {
                "jira": i.jira,
                "phase": i.phase,
                "next": i.next_label,
                "tickets": i.ticket_total,
                "done": i.ticket_done,
            }
            for i in items
        ]

    @app.get("/api/requirements/{jira}")
    def api_detail(jira: str):
        detail = detail_or_404(jira)
        return {
            "jira": detail.jira,
            "phase": detail.phase,
            "next": detail.next_label,
            "contract": detail.contract,
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
                {"id": a.id, "enabled": a.enabled, "reason": a.reason} for a in detail.actions
            ],
            "docs": [
                {"slug": d.slug, "filename": d.filename, "filled": d.filled} for d in detail.docs
            ],
        }

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

    @app.get("/api/repos")
    def api_repos():
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
