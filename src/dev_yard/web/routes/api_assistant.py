"""Assistant session JSON API and SSE stream."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from dev_yard.assistant import AssistantSse
from dev_yard.web.context import AppContext
from dev_yard.web.schemas import AssistantMessageIn, AssistantSessionIn

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    assistants = ctx.assistants

    @router.get("/api/assistant/context")
    def api_assistant_context(route: str = "/", jira: str = ""):
        return assistants.context(route=route, jira=jira)

    @router.post("/api/assistant/sessions")
    def api_assistant_create(payload: AssistantSessionIn):
        session = assistants.create(route=payload.route, jira=payload.jira)
        return session.snapshot()

    @router.get("/api/assistant/sessions/{session_id}")
    def api_assistant_get(session_id: str):
        return ctx.assistant_or_404(session_id).snapshot()

    @router.post("/api/assistant/sessions/{session_id}/messages")
    def api_assistant_message(session_id: str, payload: AssistantMessageIn):
        ctx.assistant_or_404(session_id)
        try:
            session = assistants.send(
                session_id,
                payload.text,
                route=payload.route or None,
                jira=payload.jira,
            )
        except KeyError:
            raise HTTPException(404, "unknown assistant session") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return session.snapshot()

    @router.post("/api/assistant/sessions/{session_id}/abort")
    def api_assistant_abort(session_id: str):
        ctx.assistant_or_404(session_id)
        try:
            return assistants.abort(session_id).snapshot()
        except KeyError:
            raise HTTPException(404, "unknown assistant session") from None

    @router.delete("/api/assistant/sessions/{session_id}")
    def api_assistant_drop(session_id: str):
        ctx.assistant_or_404(session_id)
        try:
            snap = assistants.drop(session_id).snapshot()
        except KeyError:
            raise HTTPException(404, "unknown assistant session") from None
        return {"ok": True, "id": snap["id"]}

    @router.get("/api/assistant/sessions/{session_id}/events")
    async def api_assistant_events(session_id: str):
        session = ctx.assistant_or_404(session_id)

        async def gen():
            sse = AssistantSse(session)
            while True:
                frames, done, seq = sse.poll()
                for frame in frames:
                    yield frame
                if done:
                    return
                new_seq = await asyncio.to_thread(session.wait_seq, seq, 0.4)
                if new_seq <= seq:
                    yield ": ping\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    return router
