"""SPA page routes and file-serving endpoints (read-only)."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from dev_yard import paths
from dev_yard.web.board import DOC_FILES
from dev_yard.web.context import STATIC, AppContext, spa_index


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/favicon.ico")
    def favicon():
        return FileResponse(STATIC / "favicon.svg")

    @router.get("/")
    def dashboard():
        return spa_index()

    @router.get("/repos")
    def repos_page():
        return spa_index()

    @router.get("/settings")
    def settings_page():
        return spa_index()

    @router.get("/qa-config")
    def qa_config_page():
        return spa_index()

    @router.get("/open")
    def open_page():
        return spa_index()

    @router.get("/r/{jira}")
    def req_page(jira: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        return spa_index()

    @router.get("/r/{jira}/qa")
    def qa_page(jira: str):
        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        return spa_index()

    @router.get("/r/{jira}/docs/{slug}")
    def doc_page(jira: str, slug: str):
        if slug not in DOC_FILES:
            raise HTTPException(404, f"unknown doc {slug}")
        return spa_index()

    @router.get("/r/{jira}/assets/{name}")
    def asset(jira: str, name: str):
        try:
            path = ctx.asset_file(jira, name)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, str(e)) from e
        return FileResponse(path)

    @router.get("/r/{jira}/qa/evidence/{run_id}/{case_id}/screenshots/{name}")
    def qa_screenshot(jira: str, run_id: str, case_id: str, name: str):
        from dev_yard.qa_board import screenshot_file

        if paths.is_reserved_req_name(jira):
            raise HTTPException(404, f"no requirement {jira}")
        try:
            path = screenshot_file(ctx.root, jira, run_id, case_id, name)
        except FileNotFoundError:
            raise HTTPException(404, "not found") from None
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media)

    return router
