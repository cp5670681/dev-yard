"""Job JSON API and SSE streams."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from dev_yard.web.context import AppContext
from dev_yard.web.jobs import BoardSse, JobSse, PiChatSse
from dev_yard.web.schemas import GrillAnswersIn

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse_response(gen) -> StreamingResponse:
    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    jobs = ctx.jobs

    @router.get("/api/jobs")
    def api_jobs():
        return jobs.running_brief()

    @router.get("/api/jobs/events")
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

        return _sse_response(gen)

    @router.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job.snapshot()

    @router.get("/api/jobs/{job_id}/pi/{run_index}")
    def api_job_pi(job_id: str, run_index: int, offset: int = 0):
        job, snap, runs, run = ctx.pi_run_or_404(job_id, run_index)
        data = ctx.load_run_conversation(runs, run_index, offset)
        data.update(
            job_id=job.id,
            run=run_index,
            started_at=run.get("started_at"),
            job_state=snap["state"],
        )
        return data

    @router.get("/api/jobs/{job_id}/pi/{run_index}/events")
    async def api_job_pi_events(job_id: str, run_index: int):
        job, _snap, _runs, _run = ctx.pi_run_or_404(job_id, run_index)

        async def gen():
            sse = PiChatSse(job, run_index, ctx.root)
            while True:
                frames, done, seq = await asyncio.to_thread(sse.poll)
                for frame in frames:
                    yield frame
                if done:
                    return
                new_seq = await asyncio.to_thread(job.wait_seq, seq, 0.4)
                if new_seq <= seq:
                    yield ": ping\n\n"

        return _sse_response(gen)

    @router.get("/api/jobs/{job_id}/events")
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

        return _sse_response(gen)

    @router.post("/api/jobs/{job_id}/answers")
    def api_job_answers(job_id: str, payload: GrillAnswersIn):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        try:
            job.submit_answers(payload.answers)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @router.post("/api/jobs/{job_id}/cancel")
    def api_job_cancel(job_id: str):
        job = jobs.cancel(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return {"ok": True, "state": job.snapshot()["state"]}

    return router
