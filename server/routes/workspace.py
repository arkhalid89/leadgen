"""Lists, tags, notes, suppression and saved searches.

These are what turn a pile of scraped rows into a workspace: a way to group
leads, label them, remember what happened, never contact the wrong person, and
re-run a search that worked.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, security

router = APIRouter(prefix="/api", tags=["workspace"])


# ---------------------------------------------------------------- models
class ListIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    colour: str = Field(default="#4f7cff", max_length=16)


class ListMembersIn(BaseModel):
    lead_ids: list[int] = Field(default_factory=list)


class TagIn(BaseModel):
    lead_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    kind: str = Field(default="note", max_length=24)


class StatusIn(BaseModel):
    lead_ids: list[int] = Field(default_factory=list)
    status: str = Field(default="new", max_length=24)


class SuppressIn(BaseModel):
    values: list[str] = Field(default_factory=list)
    reason: str = Field(default="", max_length=200)


class SavedSearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: str = "gmaps"
    keyword: str = ""
    location: str = ""
    params: dict = Field(default_factory=dict)


LEAD_STATUSES = ("new", "contacted", "replied", "qualified", "customer", "rejected")


# ---------------------------------------------------------------- lists
@router.get("/lists")
async def get_lists(user: dict = Depends(security.current_user)) -> dict:
    rows = db.query(
        """
        SELECT l.*, (
            SELECT COUNT(*) FROM lead_list_members m WHERE m.list_id = l.id
        ) AS lead_count
        FROM lead_lists l WHERE l.user_id = ? ORDER BY l.created_at DESC
        """,
        (user["id"],),
    )
    return {"lists": db.rows_to_dicts(rows)}


@router.post("/lists", status_code=201)
async def create_list(payload: ListIn, user: dict = Depends(security.current_user)) -> dict:
    try:
        cur = db.execute(
            "INSERT INTO lead_lists (user_id, name, description, colour) VALUES (?,?,?,?)",
            (user["id"], payload.name.strip(), payload.description.strip(), payload.colour),
        )
    except Exception as exc:  # noqa: BLE001 - unique constraint
        raise HTTPException(409, "You already have a list called that.") from exc
    return {"id": cur.lastrowid, "name": payload.name.strip()}


@router.delete("/lists/{list_id}")
async def delete_list(list_id: int, user: dict = Depends(security.current_user)) -> dict:
    owned = db.query_one(
        "SELECT id FROM lead_lists WHERE id = ? AND user_id = ?", (list_id, user["id"])
    )
    if not owned:
        raise HTTPException(404, "List not found.")
    with db.transaction() as conn:
        conn.execute("DELETE FROM lead_list_members WHERE list_id = ?", (list_id,))
        conn.execute("DELETE FROM lead_lists WHERE id = ?", (list_id,))
    return {"ok": True}


@router.post("/lists/{list_id}/members")
async def add_to_list(
    list_id: int, payload: ListMembersIn, user: dict = Depends(security.current_user)
) -> dict:
    owned = db.query_one(
        "SELECT id FROM lead_lists WHERE id = ? AND user_id = ?", (list_id, user["id"])
    )
    if not owned:
        raise HTTPException(404, "List not found.")
    if not payload.lead_ids:
        return {"ok": True, "added": 0}
    with db.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO lead_list_members (list_id, lead_id) VALUES (?, ?)",
            [(list_id, lid) for lid in payload.lead_ids],
        )
        conn.execute(
            "UPDATE lead_lists SET updated_at = datetime('now') WHERE id = ?", (list_id,)
        )
    return {"ok": True, "added": len(payload.lead_ids)}


@router.delete("/lists/{list_id}/members")
async def remove_from_list(
    list_id: int, payload: ListMembersIn, user: dict = Depends(security.current_user)
) -> dict:
    if not payload.lead_ids:
        return {"ok": True, "removed": 0}
    placeholders = ",".join("?" * len(payload.lead_ids))
    cur = db.execute(
        "DELETE FROM lead_list_members WHERE list_id = ? AND lead_id IN (%s)" % placeholders,
        tuple([list_id, *payload.lead_ids]),
    )
    return {"ok": True, "removed": cur.rowcount or 0}


# ---------------------------------------------------------------- tags
@router.get("/tags")
async def get_tags(user: dict = Depends(security.current_user)) -> dict:
    rows = db.query(
        "SELECT tag, COUNT(*) AS n FROM lead_tags WHERE user_id = ? "
        "GROUP BY tag ORDER BY n DESC, tag",
        (user["id"],),
    )
    return {"tags": [{"tag": r["tag"], "count": r["n"]} for r in rows]}


@router.post("/tags")
async def add_tags(payload: TagIn, user: dict = Depends(security.current_user)) -> dict:
    clean = [t.strip().lower()[:40] for t in payload.tags if t.strip()]
    if not clean or not payload.lead_ids:
        return {"ok": True, "applied": 0}
    rows = [(lid, user["id"], tag) for lid in payload.lead_ids for tag in clean]
    with db.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO lead_tags (lead_id, user_id, tag) VALUES (?,?,?)", rows
        )
    return {"ok": True, "applied": len(rows)}


@router.delete("/tags")
async def remove_tags(payload: TagIn, user: dict = Depends(security.current_user)) -> dict:
    clean = [t.strip().lower() for t in payload.tags if t.strip()]
    if not clean or not payload.lead_ids:
        return {"ok": True, "removed": 0}
    lead_ph = ",".join("?" * len(payload.lead_ids))
    tag_ph = ",".join("?" * len(clean))
    cur = db.execute(
        "DELETE FROM lead_tags WHERE user_id = ? AND lead_id IN (%s) AND tag IN (%s)"
        % (lead_ph, tag_ph),
        tuple([user["id"], *payload.lead_ids, *clean]),
    )
    return {"ok": True, "removed": cur.rowcount or 0}


# ---------------------------------------------------------------- one lead
@router.get("/leads/{lead_id}/detail")
async def lead_detail(lead_id: int, user: dict = Depends(security.current_user)) -> dict:
    lead = db.row_to_dict(
        db.query_one("SELECT * FROM leads WHERE id = ? AND user_id = ?", (lead_id, user["id"]))
    )
    if lead is None:
        raise HTTPException(404, "Lead not found.")
    lead["tags"] = [
        r["tag"] for r in db.query("SELECT tag FROM lead_tags WHERE lead_id = ?", (lead_id,))
    ]
    lead["notes"] = db.rows_to_dicts(
        db.query(
            "SELECT * FROM lead_notes WHERE lead_id = ? ORDER BY id DESC LIMIT 100", (lead_id,)
        )
    )
    lead["lists"] = db.rows_to_dicts(
        db.query(
            "SELECT l.id, l.name, l.colour FROM lead_lists l "
            "JOIN lead_list_members m ON m.list_id = l.id WHERE m.lead_id = ?",
            (lead_id,),
        )
    )
    lead["drafts"] = db.rows_to_dicts(
        db.query(
            "SELECT id, subject, created_at FROM email_templates WHERE lead_id = ? "
            "ORDER BY id DESC LIMIT 10",
            (lead_id,),
        )
    )
    return lead


@router.post("/leads/{lead_id}/notes", status_code=201)
async def add_note(
    lead_id: int, payload: NoteIn, user: dict = Depends(security.current_user)
) -> dict:
    owned = db.query_one(
        "SELECT id FROM leads WHERE id = ? AND user_id = ?", (lead_id, user["id"])
    )
    if not owned:
        raise HTTPException(404, "Lead not found.")
    cur = db.execute(
        "INSERT INTO lead_notes (lead_id, user_id, kind, body) VALUES (?,?,?,?)",
        (lead_id, user["id"], payload.kind, payload.body.strip()),
    )
    return {"id": cur.lastrowid}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int, user: dict = Depends(security.current_user)) -> dict:
    cur = db.execute(
        "DELETE FROM lead_notes WHERE id = ? AND user_id = ?", (note_id, user["id"])
    )
    if not cur.rowcount:
        raise HTTPException(404, "Note not found.")
    return {"ok": True}


@router.post("/leads/status")
async def set_status(payload: StatusIn, user: dict = Depends(security.current_user)) -> dict:
    if payload.status not in LEAD_STATUSES:
        raise HTTPException(400, "Unknown status. Use one of: %s" % ", ".join(LEAD_STATUSES))
    if not payload.lead_ids:
        return {"ok": True, "updated": 0}
    placeholders = ",".join("?" * len(payload.lead_ids))
    cur = db.execute(
        "UPDATE leads SET status = ?, updated_at = datetime('now') "
        "WHERE user_id = ? AND id IN (%s)" % placeholders,
        tuple([payload.status, user["id"], *payload.lead_ids]),
    )
    return {"ok": True, "updated": cur.rowcount or 0}


@router.post("/leads/{lead_id}/favourite")
async def toggle_favourite(lead_id: int, user: dict = Depends(security.current_user)) -> dict:
    row = db.query_one(
        "SELECT favourite FROM leads WHERE id = ? AND user_id = ?", (lead_id, user["id"])
    )
    if row is None:
        raise HTTPException(404, "Lead not found.")
    value = 0 if row["favourite"] else 1
    db.execute("UPDATE leads SET favourite = ? WHERE id = ?", (value, lead_id))
    return {"ok": True, "favourite": bool(value)}


# ---------------------------------------------------------------- suppression
@router.get("/suppression")
async def get_suppression(
    limit: int = Query(default=200, ge=1, le=2000),
    user: dict = Depends(security.current_user),
) -> dict:
    rows = db.query(
        "SELECT * FROM suppression WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user["id"], limit),
    )
    total = db.query_one(
        "SELECT COUNT(*) AS n FROM suppression WHERE user_id = ?", (user["id"],)
    )
    return {"entries": db.rows_to_dicts(rows), "total": int(total["n"]) if total else 0}


@router.post("/suppression")
async def add_suppression(
    payload: SuppressIn, user: dict = Depends(security.current_user)
) -> dict:
    rows = []
    for raw in payload.values:
        value = (raw or "").strip().lower()
        if not value:
            continue
        kind = "email" if "@" in value else "domain"
        rows.append((user["id"], value, kind, payload.reason.strip()))
    if not rows:
        return {"ok": True, "added": 0}
    with db.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO suppression (user_id, value, kind, reason) "
            "VALUES (?,?,?,?)",
            rows,
        )
    return {"ok": True, "added": len(rows)}


@router.delete("/suppression/{entry_id}")
async def delete_suppression(
    entry_id: int, user: dict = Depends(security.current_user)
) -> dict:
    cur = db.execute(
        "DELETE FROM suppression WHERE id = ? AND user_id = ?", (entry_id, user["id"])
    )
    if not cur.rowcount:
        raise HTTPException(404, "Entry not found.")
    return {"ok": True}


# ---------------------------------------------------------------- saved searches
@router.get("/saved-searches")
async def get_saved_searches(user: dict = Depends(security.current_user)) -> dict:
    rows = db.query(
        "SELECT * FROM saved_searches WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],),
    )
    return {"searches": db.rows_to_dicts(rows)}


@router.post("/saved-searches", status_code=201)
async def create_saved_search(
    payload: SavedSearchIn, user: dict = Depends(security.current_user)
) -> dict:
    try:
        cur = db.execute(
            "INSERT INTO saved_searches (user_id, name, source, keyword, location, params) "
            "VALUES (?,?,?,?,?,?)",
            (
                user["id"], payload.name.strip(), payload.source,
                payload.keyword.strip(), payload.location.strip(),
                json.dumps(payload.params),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(409, "You already saved a search with that name.") from exc
    return {"id": cur.lastrowid}


@router.delete("/saved-searches/{search_id}")
async def delete_saved_search(
    search_id: int, user: dict = Depends(security.current_user)
) -> dict:
    cur = db.execute(
        "DELETE FROM saved_searches WHERE id = ? AND user_id = ?", (search_id, user["id"])
    )
    if not cur.rowcount:
        raise HTTPException(404, "Saved search not found.")
    return {"ok": True}
