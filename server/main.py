"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, settings
from .jobs import manager
from .routes import auth, dashboard, jobs, leads, outreach, workspace

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("leadgen")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    orphans = manager.recover_orphans()
    if orphans:
        log.warning("marked %d interrupted job(s) as failed on startup", orphans)
    log.info(
        "leadgen ready - db=%s emails=website search=%s outreach-ai=%s",
        settings.DB_PATH,
        "serper" if settings.SERPER_API_KEY else "duckduckgo",
        "on" if settings.GEMINI_API_KEY else "templates",
    )
    yield


app = FastAPI(title="LeadGen", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(auth.account_router)
app.include_router(jobs.router)
app.include_router(leads.router)
app.include_router(dashboard.router)
app.include_router(outreach.router)
app.include_router(workspace.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "leadgen", "version": app.version}
