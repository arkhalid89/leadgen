"""The lead database: filtering, export, cleanup and stats."""
from __future__ import annotations

import csv
import io
import re
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import db, security, settings
from ..jobs import manager

router = APIRouter(prefix="/api/leads", tags=["leads"])

# (header, lead key). "emails"/"phones" hold every value found; the singular
# columns keep the primary one for tools that expect a single cell.
EXPORT_COLUMNS = [
    ("Business Name", "business_name"), ("Owner Name", "owner_name"),
    ("Email", "email"), ("All Emails", "emails"),
    ("Email Source", "email_source"), ("Email Verified", "email_status"),
    ("Phone", "phone"), ("All Phones", "phones"),
    ("Website", "website"), ("Address", "address"),
    ("Category", "category"), ("Rating", "rating"), ("Reviews", "reviews"),
    ("Summary", "summary"),
    ("Facebook", "facebook"), ("Instagram", "instagram"), ("Twitter", "twitter"),
    ("LinkedIn", "linkedin"), ("YouTube", "youtube"),
    ("Latitude", "latitude"), ("Longitude", "longitude"),
    ("Source", "source"), ("Keyword", "keyword"), ("Location", "location"),
    ("Quality", "quality"), ("Created At", "created_at"),
]
MULTI_COLUMNS = {"emails", "phones"}

SORTABLE = {
    "created_at": "created_at",
    "business_name": "business_name",
    "quality": "quality",
    "email": "email",
    "rating": "rating",
}


class BulkDeleteIn(BaseModel):
    ids: list[int]


class CleanupIn(BaseModel):
    remove_without_email: bool = False
    remove_without_phone: bool = False
    remove_duplicates: bool = True


def fts_query(raw: str) -> str:
    """Turn user input into a safe FTS5 MATCH expression.

    FTS5 has its own query syntax, so a stray quote or operator from a user is
    a syntax error rather than a search. Every token is quoted, and a trailing
    prefix wildcard is added so typing "dent" finds "dental" as you go.
    """
    tokens = [t for t in re.split(r"[^\w@.\-]+", raw or "") if t]
    if not tokens:
        return ""
    parts = []
    for index, token in enumerate(tokens):
        safe = token.replace('"', "")
        if not safe:
            continue
        # Only the last token gets prefix matching - the rest are complete words.
        parts.append('"%s"*' % safe if index == len(tokens) - 1 else '"%s"' % safe)
    return " AND ".join(parts)


def _filters(
    user_id: int,
    search: str,
    source: str,
    quality: str,
    keyword: str,
    location: str,
    has_email: bool | None,
    job_id: str,
    status: str = "",
    list_id: int | None = None,
    tag: str = "",
    favourite: bool | None = None,
    has_phone: bool | None = None,
    verified: bool | None = None,
) -> tuple[str, list]:
    clauses = ["user_id = ?"]
    params: list = [user_id]

    if search:
        # An indexed full-text lookup rather than five LIKE scans, which could
        # not use an index at all.
        match = fts_query(search)
        if match:
            clauses.append(
                "id IN (SELECT rowid FROM leads_fts WHERE leads_fts MATCH ?)"
            )
            params.append(match)
        else:
            clauses.append("1 = 0")
    if source:
        clauses.append("source = ?")
        params.append(source)
    if quality:
        clauses.append("quality = ?")
        params.append(quality)
    if keyword:
        clauses.append("keyword = ?")
        params.append(keyword)
    if location:
        clauses.append("location = ?")
        params.append(location)
    if job_id:
        clauses.append("job_id = ?")
        params.append(job_id)
    if has_email is True:
        clauses.append("email != ''")
    elif has_email is False:
        clauses.append("email = ''")
    if has_phone is True:
        clauses.append("phone != ''")
    if verified is True:
        clauses.append("email_status = 'verified'")
    if status:
        clauses.append("status = ?")
        params.append(status)
    if favourite:
        clauses.append("favourite = 1")
    if list_id:
        clauses.append("id IN (SELECT lead_id FROM lead_list_members WHERE list_id = ?)")
        params.append(list_id)
    if tag:
        clauses.append("id IN (SELECT lead_id FROM lead_tags WHERE tag = ?)")
        params.append(tag.strip().lower())

    return " AND ".join(clauses), params


@router.get("")
async def list_leads(
    search: str = "",
    source: str = "",
    quality: str = "",
    keyword: str = "",
    location: str = "",
    job_id: str = "",
    has_email: bool | None = None,
    has_phone: bool | None = None,
    verified: bool | None = None,
    status: str = "",
    list_id: int | None = None,
    tag: str = "",
    favourite: bool | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(security.current_user),
) -> dict:
    where, params = _filters(
        user["id"], search, source, quality, keyword, location, has_email, job_id,
        status, list_id, tag, favourite, has_phone, verified,
    )
    order_col = SORTABLE.get(sort, "created_at")
    order_dir = "ASC" if direction.lower() == "asc" else "DESC"

    total_row = db.query_one("SELECT COUNT(*) AS n FROM leads WHERE " + where, tuple(params))
    rows = db.rows_to_dicts(
        db.query(
            "SELECT * FROM leads WHERE %s ORDER BY %s %s LIMIT ? OFFSET ?"
            % (where, order_col, order_dir),
            tuple(params + [limit, offset]),
        )
    )
    if rows:
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        by_lead: dict[int, list[str]] = {}
        for tag_row in db.query(
            "SELECT lead_id, tag FROM lead_tags WHERE lead_id IN (%s)" % placeholders,
            tuple(ids),
        ):
            by_lead.setdefault(tag_row["lead_id"], []).append(tag_row["tag"])
        for row in rows:
            row["tags"] = by_lead.get(row["id"], [])

    return {
        "leads": rows,
        "total": int(total_row["n"]) if total_row else 0,
        "limit": limit,
        "offset": offset,
    }


@router.get("/filters")
async def lead_filters(user: dict = Depends(security.current_user)) -> dict:
    uid = (user["id"],)
    def distinct(column: str) -> list[str]:
        rows = db.query(
            "SELECT DISTINCT %s AS v FROM leads WHERE user_id = ? AND %s != '' ORDER BY v"
            % (column, column),
            uid,
        )
        return [r["v"] for r in rows]

    return {
        "sources": distinct("source"),
        "keywords": distinct("keyword"),
        "locations": distinct("location"),
        "qualities": ["strong", "medium", "weak"],
    }


@router.get("/stats")
async def lead_stats(user: dict = Depends(security.current_user)) -> dict:
    uid = (user["id"],)
    totals = db.query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN email   != '' THEN 1 ELSE 0 END) AS with_email,
               SUM(CASE WHEN phone   != '' THEN 1 ELSE 0 END) AS with_phone,
               SUM(CASE WHEN emails NOT IN ('[]','') AND emails LIKE '%,%' THEN 1 ELSE 0 END) AS multi_email,
               SUM(CASE WHEN phones NOT IN ('[]','') AND phones LIKE '%,%' THEN 1 ELSE 0 END) AS multi_phone,
               SUM(CASE WHEN website != '' THEN 1 ELSE 0 END) AS with_website,
               SUM(CASE WHEN enriched = 1  THEN 1 ELSE 0 END) AS enriched
        FROM leads WHERE user_id = ?
        """,
        uid,
    )
    by_quality = {
        r["quality"]: r["n"]
        for r in db.query(
            "SELECT quality, COUNT(*) AS n FROM leads WHERE user_id = ? GROUP BY quality", uid
        )
    }
    by_source = {
        r["source"]: r["n"]
        for r in db.query(
            "SELECT source, COUNT(*) AS n FROM leads WHERE user_id = ? GROUP BY source", uid
        )
    }
    return {
        "total": totals["total"] or 0,
        "with_email": totals["with_email"] or 0,
        "with_phone": totals["with_phone"] or 0,
        "with_website": totals["with_website"] or 0,
        "enriched": totals["enriched"] or 0,
        "multi_email": totals["multi_email"] or 0,
        "multi_phone": totals["multi_phone"] or 0,
        "by_quality": by_quality,
        "by_source": by_source,
    }


@router.get("/export")
async def export_leads(
    search: str = "",
    source: str = "",
    quality: str = "",
    keyword: str = "",
    location: str = "",
    job_id: str = "",
    has_email: bool | None = None,
    status: str = "",
    list_id: int | None = None,
    tag: str = "",
    user: dict = Depends(security.current_user),
):
    where, params = _filters(
        user["id"], search, source, quality, keyword, location, has_email, job_id,
        status, list_id, tag,
    )
    # Never export anyone on the do-not-contact list.
    where += (
        " AND (email = '' OR email NOT IN "
        "(SELECT value FROM suppression WHERE user_id = ? AND kind = 'email'))"
    )
    params = params + [user["id"]]
    rows = db.query(
        "SELECT * FROM leads WHERE %s ORDER BY created_at DESC" % where, tuple(params)
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for header, _ in EXPORT_COLUMNS])
    for row in rows:
        keys = row.keys()
        line = []
        for _, key in EXPORT_COLUMNS:
            value = row[key] if key in keys else ""
            if key in MULTI_COLUMNS:
                try:
                    value = "; ".join(json.loads(value or "[]"))
                except (json.JSONDecodeError, TypeError):
                    value = ""
            line.append(value)
        writer.writerow(line)
    buffer.seek(0)

    filename = "leads-%s.csv" % (job_id or keyword or "export")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


@router.delete("/{lead_id}")
async def delete_lead(lead_id: int, user: dict = Depends(security.current_user)) -> dict:
    cur = db.execute(
        "DELETE FROM leads WHERE id = ? AND user_id = ?", (lead_id, user["id"])
    )
    if not cur.rowcount:
        raise HTTPException(404, "Lead not found.")
    return {"ok": True}


@router.post("/bulk-delete")
async def bulk_delete(payload: BulkDeleteIn, user: dict = Depends(security.current_user)) -> dict:
    if not payload.ids:
        return {"ok": True, "deleted": 0}
    placeholders = ",".join("?" * len(payload.ids))
    cur = db.execute(
        "DELETE FROM leads WHERE user_id = ? AND id IN (%s)" % placeholders,
        tuple([user["id"], *payload.ids]),
    )
    return {"ok": True, "deleted": cur.rowcount or 0}


@router.post("/cleanup")
async def cleanup(payload: CleanupIn, user: dict = Depends(security.current_user)) -> dict:
    uid = user["id"]
    removed = {"no_email": 0, "no_phone": 0, "duplicates": 0}

    with db.transaction() as conn:
        if payload.remove_without_email:
            removed["no_email"] = conn.execute(
                "DELETE FROM leads WHERE user_id = ? AND email = ''", (uid,)
            ).rowcount or 0
        if payload.remove_without_phone:
            removed["no_phone"] = conn.execute(
                "DELETE FROM leads WHERE user_id = ? AND phone = ''", (uid,)
            ).rowcount or 0
        if payload.remove_duplicates:
            # Keep the richest row per identity: prefer one with an email, then
            # the most recently updated.
            removed["duplicates"] = conn.execute(
                """
                DELETE FROM leads WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY user_id, COALESCE(NULLIF(uid, ''), lower(business_name))
                            ORDER BY (email != '') DESC, (phone != '') DESC, updated_at DESC, id DESC
                        ) AS rn
                        FROM leads WHERE user_id = ?
                    ) WHERE rn > 1
                )
                """,
                (uid,),
            ).rowcount or 0

    return {"ok": True, "removed": removed, "total_removed": sum(removed.values())}


class FindEmailsIn(BaseModel):
    """Target either an explicit set of leads, or everything matching a filter."""
    lead_ids: list[int] = Field(default_factory=list)
    search: str = ""
    source: str = ""
    quality: str = ""
    keyword: str = ""
    location: str = ""
    job_id: str = ""
    use_ai: bool = False
    verify: bool = False
    limit: int = Field(default=200, ge=1, le=2000)


@router.post("/find-emails")
async def find_emails(payload: FindEmailsIn, user: dict = Depends(security.active_user)) -> dict:
    """Retry email discovery on leads that have none.

    Reads each business's website again with the hardened fetcher (host
    variants, longer retry for slow hosting). Optionally follows up with
    AI-suggested addresses, which are stored flagged as unverified.
    """
    if payload.lead_ids:
        placeholders = ",".join("?" * len(payload.lead_ids))
        rows = db.query(
            "SELECT id FROM leads WHERE user_id = ? AND email = '' AND id IN (%s) LIMIT ?"
            % placeholders,
            tuple([user["id"], *payload.lead_ids, payload.limit]),
        )
    else:
        where, params = _filters(
            user["id"], payload.search, payload.source, payload.quality,
            payload.keyword, payload.location, False, payload.job_id,
        )
        rows = db.query(
            "SELECT id FROM leads WHERE %s ORDER BY id DESC LIMIT ?" % where,
            tuple(params + [payload.limit]),
        )

    lead_ids = [int(r["id"]) for r in rows]
    if not lead_ids:
        raise HTTPException(404, "No leads without an email matched that selection.")

    if payload.use_ai and not settings.GEMINI_API_KEY:
        raise HTTPException(
            400,
            "AI suggestions need a Gemini API key. Set GEMINI_API_KEY and restart, "
            "or run the website lookup on its own.",
        )

    if manager.active_count(user["id"]) >= settings.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(
            429,
            "You already have %d jobs running. Wait for one to finish."
            % settings.MAX_ACTIVE_JOBS_PER_USER,
        )

    job_id = manager.create_find_emails(
        user["id"], lead_ids, payload.use_ai, payload.verify
    )
    return {
        "job_id": job_id,
        "leads": len(lead_ids),
        "use_ai": payload.use_ai,
        "verify": payload.verify,
    }


@router.get("/missing-email-count")
async def missing_email_count(user: dict = Depends(security.current_user)) -> dict:
    """How many leads could benefit from a retry."""
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM leads WHERE user_id = ? AND email = '' AND website != ''",
        (user["id"],),
    )
    return {
        "missing": int(row["n"]) if row else 0,
        "ai_available": bool(settings.GEMINI_API_KEY),
        "verify_available": settings.SMTP_VERIFY_ENABLED,
    }
