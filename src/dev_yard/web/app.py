from __future__ import annotations

import asyncio
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

from dev_yard import paths, service as yard_service
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
ACTIONS = {"open", "grill", "spec", "tickets", "freeze", "implement", "review", "contract"}
STEP_LABELS = {
    "open": "抽取",
    "grill": "对齐",
    "spec": "Spec",
    "tickets": "拆票",
    "freeze": "冻结",
    "implement": "实现",
    "review": "审查",
    "done": "完成",
}
ACTION_LABELS = {
    "open": "抽取需求",
    "grill": "对齐",
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


class OpenIn(BaseModel):
    jira: str
    source: str = "pi"
    force: bool = False


class RepoAddIn(BaseModel):
    alias: str = ""
    url: str
    default_base: str = "main"
    role: str = "svc"
    path: str = ""


class ActionIn(BaseModel):
    ticket_id: str = ""
    force: bool = False
    source: str = "pi"


class DocSaveIn(BaseModel):
    body: str = ""


class StageModelIn(BaseModel):
    provider: str = ""
    model: str = ""


class PiSettingsIn(BaseModel):
    provider: str = ""
    model: str = ""
    stages: dict[str, StageModelIn] = Field(default_factory=dict)


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

    @app.get("/settings")
    def settings_page():
        return spa_index()

    @app.get("/open")
    def open_page():
        return spa_index()

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
        return {"root": str(root), "root_name": root.name}

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

    @app.get("/api/requirements/{jira}/docs/{slug}")
    def api_doc(jira: str, slug: str):
        if slug not in DOC_FILES:
            raise HTTPException(404, f"unknown doc {slug}")
        return _doc_payload(detail_or_404(jira), slug)

    @app.put("/api/requirements/{jira}/docs/{slug}")
    def api_doc_save(jira: str, slug: str, payload: DocSaveIn):
        try:
            save_doc(root, jira, slug, payload.body)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from e
        return _doc_payload(detail_or_404(jira), slug)

    @app.post("/api/open")
    def api_open(payload: OpenIn):
        key = payload.jira.strip().upper()
        try:
            paths.req_dir(root, key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        try:
            job = jobs.submit(
                "open",
                key,
                extra={"source": payload.source, "force": payload.force},
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
                },
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return _jobs_out([submitted])

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
