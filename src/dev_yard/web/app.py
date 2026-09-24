from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from dev_yard import __version__, paths, service
from dev_yard.actions import ACTION_LABELS
from dev_yard.assistant import AssistantHub
from dev_yard.web.board import DOC_FILES, PIPELINE
from dev_yard.web.context import (
    HERE,
    SPA,
    STATIC,
    AppContext,
    render_markdown,
    spa_index,
)
from dev_yard.web.jobs import JobRunner
from dev_yard.web.routes import (
    api_assistant,
    api_jobs,
    api_reqs,
    api_settings,
    pages,
)
from dev_yard.web.security import OriginGuardMiddleware, bearer_ok

__all__ = [
    "HERE",
    "ACTION_LABELS",
    "create_app",
    "check_bind_host",
    "render_markdown",
    "serve",
    "spa_index",
]

# Kept for tests and external callers that still import them from here.
_bearer_ok = bearer_ok

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

STEP_LABELS = {
    "open": "抽取",
    "grill": "对齐",
    "spec": "规约",
    "tickets": "拆票",
    "freeze": "冻结",
    "implement": "实现",
    "review": "审查",
    "contract": "契约审查",
    "testing": "提测",
    "done": "完成",
    "assistant": "助手",
}


def check_bind_host(host: str, allow_remote: bool = False) -> None:
    h = (host or "").strip().lower()
    if h in _LOOPBACK or allow_remote:
        return
    raise ValueError(
        f"refusing to bind {host!r}: this console has no auth and can run `pi --approve`. "
        "Use 127.0.0.1, or pass --allow-remote."
    )


def create_app(
    root: Path,
    job_runner: JobRunner | None = None,
    sync_jobs: bool = False,
    assistant_hub: AssistantHub | None = None,
    allow_remote: bool = False,
) -> FastAPI:
    root = root.resolve()
    jobs = job_runner or JobRunner(root, sync=sync_jobs)
    assistants = assistant_hub or AssistantHub(root, sync=sync_jobs)
    # Drop bundle uploads stranded by a crash/restart before their import job ran.
    shutil.rmtree(paths.export_uploads_dir(root), ignore_errors=True)
    if not jobs.sync:
        jobs.resume_pending_grills()
        jobs.resume_pending_qa()
        service.recover_stale_tickets(root)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals["step_labels"] = STEP_LABELS
    templates.env.globals["doc_files"] = DOC_FILES
    templates.env.globals["pipeline"] = PIPELINE

    app = FastAPI(title="dev-yard", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.add_middleware(OriginGuardMiddleware, allow_remote=allow_remote)

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    spa_assets = SPA / "assets"
    if spa_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(spa_assets)), name="spa-assets")

    ctx = AppContext(root=root, jobs=jobs, assistants=assistants, templates=templates)
    for module in (pages, api_reqs, api_jobs, api_settings, api_assistant):
        app.include_router(module.build(ctx))

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context=ctx.base(request, status=exc.status_code, detail=exc.detail),
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
    app = create_app(root, allow_remote=allow_remote)
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    # SSE 连接是无限循环，优雅关闭时会一直"Waiting for connections to close"；
    # 设一个上限，超时后 uvicorn 会强制取消在途任务。
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_graceful_shutdown=3)
