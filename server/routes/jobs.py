"""Job control: start a search, watch it, stop it, read its results."""
from __future__ import annotations

import asyncio
import json
import queue

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import db, security, settings
from ..jobs import ENRICH_MODES, SOURCES, manager

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class StartJobIn(BaseModel):
    source: str = Field(default="gmaps")
    keyword: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)
    # 0 means unlimited: keep going until the area is exhausted, the search
    # ceiling is reached, or the user stops it.
    max_leads: int = Field(default=100, ge=0, le=100000)
    enrich_mode: str = Field(default="standard")


def _job_or_404(job_id: str, user_id: int) -> dict:
    row = db.row_to_dict(
        db.query_one("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    )
    if row is None:
        raise HTTPException(404, "Job not found.")
    return row


@router.post("")
async def start_job(payload: StartJobIn, user: dict = Depends(security.active_user)) -> dict:
    if payload.source not in SOURCES:
        raise HTTPException(400, "Unknown source. Use one of: %s" % ", ".join(SOURCES))
    if payload.enrich_mode == "fast":
        # Legacy value: "fast" used to skip detail pages and lean on a model for
        # phone and website. Those now come from the listing, so it means nothing.
        payload.enrich_mode = "standard"
    if payload.enrich_mode not in ENRICH_MODES:
        raise HTTPException(400, "Unknown enrichment mode. Use one of: %s" % ", ".join(ENRICH_MODES))

    if payload.max_leads == 0 and not payload.location.strip():
        raise HTTPException(
            400,
            "An unlimited search needs a location to work through. Add one, or "
            "set a lead limit.",
        )

    if manager.active_count(user["id"]) >= settings.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(
            429,
            "You already have %d searches running. Wait for one to finish."
            % settings.MAX_ACTIVE_JOBS_PER_USER,
        )

    job_id = manager.create(
        user_id=user["id"],
        source=payload.source,
        keyword=payload.keyword.strip(),
        location=payload.location.strip(),
        max_leads=payload.max_leads,
        enrich_mode=payload.enrich_mode,
    )
    return _job_or_404(job_id, user["id"])


@router.get("")
async def list_jobs(
    limit: int = 50, offset: int = 0, user: dict = Depends(security.current_user)
) -> dict:
    limit = max(1, min(limit, settings.JOB_HISTORY_LIMIT))
    rows = db.rows_to_dicts(
        db.query(
            "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user["id"], limit, offset),
        )
    )
    total = db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE user_id = ?", (user["id"],))
    return {"jobs": rows, "total": int(total["n"]) if total else 0}


@router.get("/{job_id}")
async def get_job(job_id: str, user: dict = Depends(security.current_user)) -> dict:
    job = _job_or_404(job_id, user["id"])
    job["running"] = manager.is_running(job_id)
    return job


@router.get("/{job_id}/events")
async def job_events(
    job_id: str, limit: int = 200, user: dict = Depends(security.current_user)
) -> dict:
    _job_or_404(job_id, user["id"])
    rows = db.rows_to_dicts(
        db.query(
            "SELECT * FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT ?",
            (job_id, max(1, min(limit, 1000))),
        )
    )
    return {"events": list(reversed(rows))}


@router.get("/{job_id}/leads")
async def job_leads(
    job_id: str, limit: int = 500, offset: int = 0, user: dict = Depends(security.current_user)
) -> dict:
    _job_or_404(job_id, user["id"])
    rows = db.rows_to_dicts(
        db.query(
            "SELECT * FROM leads WHERE job_id = ? AND user_id = ? "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            (job_id, user["id"], max(1, min(limit, 2000)), offset),
        )
    )
    total = db.query_one(
        "SELECT COUNT(*) AS n FROM leads WHERE job_id = ? AND user_id = ?", (job_id, user["id"])
    )
    return {"leads": rows, "total": int(total["n"]) if total else 0}


@router.post("/{job_id}/stop")
async def stop_job(job_id: str, user: dict = Depends(security.current_user)) -> dict:
    job = _job_or_404(job_id, user["id"])
    if job["status"] in {"completed", "failed", "stopped"}:
        return {"ok": True, "status": job["status"], "message": "Job already finished."}
    stopped = manager.stop(job_id)
    if not stopped:
        db.execute(
            "UPDATE jobs SET status = 'stopped', message = 'Stopped by user.' WHERE id = ?",
            (job_id,),
        )
    return {"ok": True, "status": "stopping" if stopped else "stopped"}


@router.delete("/{job_id}")
async def delete_job(job_id: str, user: dict = Depends(security.current_user)) -> dict:
    _job_or_404(job_id, user["id"])
    if manager.is_running(job_id):
        raise HTTPException(409, "Stop the job before deleting it.")
    with db.transaction() as conn:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM leads WHERE job_id = ? AND user_id = ?", (job_id, user["id"]))
        conn.execute("DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, user["id"]))
    return {"ok": True}


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, request: Request, user: dict = Depends(security.current_user)):
    """Server-sent events for live progress."""
    job = _job_or_404(job_id, user["id"])
    subscriber = manager.subscribe(job_id)

    async def event_source():
        try:
            # Replay current state so a late subscriber is never blank.
            yield "data: %s\n\n" % json.dumps(
                {
                    "type": "snapshot",
                    "job_id": job_id,
                    "status": job["status"],
                    "stage": job["stage"],
                    "progress": job["progress"],
                    "message": job["message"],
                }
            )
            while True:
                if await request.is_disconnected():
                    return
                try:
                    payload = subscriber.get_nowait()
                except queue.Empty:
                    current = db.query_one("SELECT status FROM jobs WHERE id = ?", (job_id,))
                    if current and current["status"] in {"completed", "failed", "stopped"}:
                        if not manager.is_running(job_id):
                            yield "data: %s\n\n" % json.dumps(
                                {"type": "closed", "job_id": job_id, "status": current["status"]}
                            )
                            return
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(1.0)
                    continue
                yield "data: %s\n\n" % json.dumps(payload)
        finally:
            manager.unsubscribe(job_id, subscriber)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
