"""Dashboard aggregates."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import db, security, settings

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def stats(user: dict = Depends(security.current_user)) -> dict:
    uid = (user["id"],)

    leads = db.query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN email    != '' THEN 1 ELSE 0 END) AS with_email,
               SUM(CASE WHEN phone    != '' THEN 1 ELSE 0 END) AS with_phone,
               SUM(CASE WHEN quality = 'strong' THEN 1 ELSE 0 END) AS strong,
               SUM(CASE WHEN quality = 'medium' THEN 1 ELSE 0 END) AS medium,
               SUM(CASE WHEN quality = 'weak'   THEN 1 ELSE 0 END) AS weak,
               SUM(CASE WHEN enriched = 1 THEN 1 ELSE 0 END) AS enriched
        FROM leads WHERE user_id = ?
        """,
        uid,
    )
    jobs = db.query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM jobs WHERE user_id = ?
        """,
        uid,
    )
    templates = db.query_one(
        "SELECT COUNT(*) AS n FROM email_templates WHERE user_id = ?", uid
    )

    total = leads["total"] or 0
    with_email = leads["with_email"] or 0
    return {
        "leads": {
            "total": total,
            "with_email": with_email,
            "with_phone": leads["with_phone"] or 0,
            "enriched": leads["enriched"] or 0,
            "email_rate": round((with_email / total) * 100, 1) if total else 0.0,
            "strong": leads["strong"] or 0,
            "medium": leads["medium"] or 0,
            "weak": leads["weak"] or 0,
        },
        "jobs": {
            "total": jobs["total"] or 0,
            "completed": jobs["completed"] or 0,
            "active": jobs["active"] or 0,
            "failed": jobs["failed"] or 0,
        },
        "email_templates": templates["n"] if templates else 0,
    }


@router.get("/timeline")
async def timeline(days: int = 30, user: dict = Depends(security.current_user)) -> dict:
    days = max(1, min(days, 365))
    rows = db.query(
        """
        SELECT date(created_at) AS day,
               COUNT(*) AS leads,
               SUM(CASE WHEN email != '' THEN 1 ELSE 0 END) AS with_email
        FROM leads
        WHERE user_id = ? AND created_at >= date('now', ?)
        GROUP BY day ORDER BY day
        """,
        (user["id"], "-%d days" % days),
    )
    return {
        "days": days,
        "points": [
            {"day": r["day"], "leads": r["leads"], "with_email": r["with_email"] or 0}
            for r in rows
        ],
    }


@router.get("/top-keywords")
async def top_keywords(limit: int = 8, user: dict = Depends(security.current_user)) -> dict:
    rows = db.query(
        """
        SELECT keyword, COUNT(*) AS leads,
               SUM(CASE WHEN email != '' THEN 1 ELSE 0 END) AS with_email
        FROM leads WHERE user_id = ? AND keyword != ''
        GROUP BY keyword ORDER BY leads DESC LIMIT ?
        """,
        (user["id"], max(1, min(limit, 50))),
    )
    return {
        "keywords": [
            {"keyword": r["keyword"], "leads": r["leads"], "with_email": r["with_email"] or 0}
            for r in rows
        ]
    }


@router.get("/config")
async def config(user: dict = Depends(security.current_user)) -> dict:
    """What the frontend needs to know about how the server is set up."""
    return {
        # Emails are read from business websites; no key or quota involved.
        "email_source": "website",
        "email_concurrency": settings.EMAIL_CONCURRENCY,
        "email_max_pages": settings.EMAIL_MAX_PAGES,
        "smtp_verify_enabled": settings.SMTP_VERIFY_ENABLED,
        # Gemini is optional and only writes outreach copy.
        "gemini_configured": bool(settings.GEMINI_API_KEY),
        "gemini_model": settings.GEMINI_MODEL,
        "search_backend": "serper" if settings.SERPER_API_KEY else "duckduckgo",
        "gmaps_detail_workers": settings.GMAPS_DETAIL_WORKERS,
        "max_active_jobs": settings.MAX_ACTIVE_JOBS_PER_USER,
    }
