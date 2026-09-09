"""Cold-email drafting.

Gemini writes the copy when a key is configured; otherwise the rule-based
templates carried over from the original tool are used, so this page keeps
working with no API key at all.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import random

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import db, security, settings
from ..enrich.gemini import generate_email_copy

router = APIRouter(prefix="/api/outreach", tags=["outreach"])

OUTREACH_TYPES = ("agency", "saas", "freelance", "consulting")


class SenderIn(BaseModel):
    name: str = Field(default="", max_length=120)
    company: str = Field(default="", max_length=120)
    website: str = Field(default="", max_length=200)
    services: str = Field(default="", max_length=600)


class GenerateIn(BaseModel):
    sender: SenderIn
    outreach_type: str = "agency"
    lead_ids: list[int] = Field(default_factory=list)
    keyword: str = ""
    location: str = ""
    limit: int = Field(default=25, ge=1, le=200)
    use_ai: bool = True


def _subject_pool(outreach_type: str, business: str, niche: str, loc: str, company: str) -> list[str]:
    loc_phrase = " in %s" % loc if loc else ""
    pools = {
        "agency": [
            "Quick idea for %s" % business,
            "Helping %s businesses%s grow" % (niche, loc_phrase),
            "Partnership opportunity for %s" % business,
            "%s x %s - a quick thought" % (company, business),
        ],
        "saas": [
            "A tool built for %s businesses like %s" % (niche, business),
            "Save hours every week at %s" % business,
            "Quick demo for %s?" % business,
            "%s for %s businesses%s" % (company, niche, loc_phrase),
        ],
        "freelance": [
            "Can I help %s with %s?" % (business, niche),
            "Freelance %s expert - quick intro" % niche,
            "Let's work together, %s" % business,
            "Ideas for %s%s" % (business, loc_phrase),
        ],
        "consulting": [
            "Strategic growth ideas for %s" % business,
            "Consulting opportunity - %s" % business,
            "Unlock growth for %s%s" % (business, loc_phrase),
            "%s insights for %s" % (niche, business),
        ],
    }
    return pools.get(outreach_type, pools["agency"])


def build_template(lead: dict, sender: SenderIn, outreach_type: str) -> tuple[str, str]:
    """Rule-based fallback copy."""
    business = lead.get("business_name") or "your business"
    niche = lead.get("category") or lead.get("keyword") or "your industry"
    loc = lead.get("location") or lead.get("address") or ""
    loc_phrase = " in %s" % loc if loc else ""
    services = sender.services or "growth and marketing support"
    company = sender.company or sender.name or "our team"
    site_line = (
        "\nYou can see more of our work at %s." % sender.website if sender.website else ""
    )

    owner = (lead.get("owner_name") or "").strip()
    first_name = owner.split()[0] if owner else "there"

    bodies = {
        "agency": (
            "Hi %s,\n\n"
            "I came across %s%s and was impressed by what you've built in the %s space.\n\n"
            "I'm %s from %s. We specialise in %s, and we've helped similar businesses "
            "increase their visibility and bring in more enquiries.\n\n"
            "I had a couple of ideas specifically for %s - would you be open to a quick "
            "10-minute call this week?%s\n\n"
            "Best regards,\n%s\n%s"
            % (first_name, business, loc_phrase, niche, sender.name or "we",
               company, services, business, site_line, sender.name, company)
        ),
        "saas": (
            "Hi %s,\n\n"
            "I noticed %s%s is doing great work in %s. We've built a tool at %s that's "
            "helping businesses like yours save time and scale faster.\n\n"
            "In short: %s.\n\n"
            "I'd be glad to give you a short walkthrough so you can judge it for "
            "yourself.%s\n\n"
            "Cheers,\n%s\n%s"
            % (first_name, business, loc_phrase, niche, company, services, site_line,
               sender.name, company)
        ),
        "freelance": (
            "Hi %s,\n\n"
            "I found %s%s while researching %s businesses, and I think there's a real "
            "opportunity to build on what you're already doing well.\n\n"
            "I'm %s, a freelance specialist in %s. I've worked with a number of %s "
            "businesses and delivered measurable results.\n\n"
            "Happy to share a couple of tailored ideas - no strings attached.%s\n\n"
            "Best,\n%s"
            % (first_name, business, loc_phrase, niche, sender.name or "a specialist",
               services, niche, site_line, sender.name)
        ),
        "consulting": (
            "Hi %s,\n\n"
            "I've been looking at the %s landscape%s, and %s stood out as a business "
            "with strong potential for accelerated growth.\n\n"
            "At %s we provide strategic consulting in %s, and we've helped comparable "
            "businesses unlock new revenue and tighten operations.\n\n"
            "Could we find a short slot this week to talk it through?%s\n\n"
            "Warm regards,\n%s\n%s"
            % (first_name, niche, loc_phrase, business, company, services, site_line,
               sender.name, company)
        ),
    }

    subject = random.choice(_subject_pool(outreach_type, business, niche, loc, company))
    return subject, bodies.get(outreach_type, bodies["agency"])


@router.post("/generate")
async def generate(payload: GenerateIn, user: dict = Depends(security.active_user)) -> dict:
    if payload.outreach_type not in OUTREACH_TYPES:
        raise HTTPException(400, "Unknown outreach type. Use one of: %s" % ", ".join(OUTREACH_TYPES))

    clauses = ["user_id = ?", "email != ''"]
    params: list = [user["id"]]
    if payload.lead_ids:
        clauses.append("id IN (%s)" % ",".join("?" * len(payload.lead_ids)))
        params.extend(payload.lead_ids)
    if payload.keyword:
        clauses.append("keyword = ?")
        params.append(payload.keyword)
    if payload.location:
        clauses.append("location = ?")
        params.append(payload.location)

    leads = db.rows_to_dicts(
        db.query(
            "SELECT * FROM leads WHERE %s ORDER BY quality = 'strong' DESC, id DESC LIMIT ?"
            % " AND ".join(clauses),
            tuple(params + [payload.limit]),
        )
    )
    if not leads:
        raise HTTPException(404, "No leads with an email address matched that selection.")

    use_ai = payload.use_ai and bool(settings.GEMINI_API_KEY)
    sender_json = json.dumps(payload.sender.model_dump())

    if use_ai:
        sender_dict = payload.sender.model_dump()
        results = await asyncio.gather(
            *(generate_email_copy(lead, sender_dict, payload.outreach_type) for lead in leads),
            return_exceptions=True,
        )
    else:
        results = [None] * len(leads)

    created = []
    rows = []
    for lead, result in zip(leads, results):
        if isinstance(result, tuple):
            subject, body = result
            generated_by = "gemini"
        else:
            subject, body = build_template(lead, payload.sender, payload.outreach_type)
            generated_by = "template"
        rows.append(
            (
                user["id"], lead["id"], lead.get("business_name", ""), lead.get("email", ""),
                subject, body, lead.get("keyword", ""), lead.get("location", ""), sender_json,
            )
        )
        created.append(
            {
                "lead_id": lead["id"],
                "business_name": lead.get("business_name", ""),
                "email": lead.get("email", ""),
                "subject": subject,
                "body": body,
                "generated_by": generated_by,
            }
        )

    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO email_templates (user_id, lead_id, business_name, email, subject, "
            "body, keyword, location, sender_info) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )

    return {
        "generated": len(created),
        "mode": "gemini" if use_ai else "template",
        "templates": created,
    }


@router.get("/templates")
async def list_templates(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str = "",
    user: dict = Depends(security.current_user),
) -> dict:
    clauses = ["user_id = ?"]
    params: list = [user["id"]]
    if search:
        clauses.append("(business_name LIKE ? OR email LIKE ? OR subject LIKE ?)")
        params.extend(["%%%s%%" % search] * 3)
    where = " AND ".join(clauses)

    total = db.query_one("SELECT COUNT(*) AS n FROM email_templates WHERE " + where, tuple(params))
    rows = db.rows_to_dicts(
        db.query(
            "SELECT * FROM email_templates WHERE %s ORDER BY created_at DESC, id DESC "
            "LIMIT ? OFFSET ?" % where,
            tuple(params + [limit, offset]),
        )
    )
    return {"templates": rows, "total": int(total["n"]) if total else 0}


@router.get("/templates/export")
async def export_templates(user: dict = Depends(security.current_user)):
    rows = db.query(
        "SELECT business_name, email, subject, body, keyword, location, created_at "
        "FROM email_templates WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],),
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Business", "Email", "Subject", "Body", "Keyword", "Location", "Created"])
    for row in rows:
        writer.writerow(list(row))
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="email-templates.csv"'},
    )


@router.delete("/templates/{template_id}")
async def delete_template(template_id: int, user: dict = Depends(security.current_user)) -> dict:
    cur = db.execute(
        "DELETE FROM email_templates WHERE id = ? AND user_id = ?", (template_id, user["id"])
    )
    if not cur.rowcount:
        raise HTTPException(404, "Template not found.")
    return {"ok": True}
