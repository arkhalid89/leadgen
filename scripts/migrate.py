"""One-shot migration from the old 30-table schema to the new one.

Old layout                              New layout
----------------------------------------------------------------
scrape_history + gmaps_sessions    ->   jobs
leads + gmaps_session_leads        ->   leads
gmaps_session_logs/_events         ->   job_events
email_templates, users, licences   ->   unchanged
everything else (20 empty tables)  ->   dropped

Run it twice and nothing bad happens: existing rows are matched on their
natural keys and skipped.

    python -m scripts.migrate [--db leadgen.db] [--no-backup] [--drop-legacy]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db import SCHEMA, _add_missing_columns  # noqa: E402

# Tables the rebuilt tool has no use for. Every one of these was empty in the
# database this migration was written against.
DEAD_TABLES = [
    "activity_log", "agents", "campaign_leads", "campaign_sequences", "campaigns",
    "checkpoints", "lead_core", "lead_enrichment", "lead_insights", "lead_scores",
    "lead_signals", "lead_sources", "merge_proposals", "outreach_events",
    "pipeline_items", "pipeline_stages", "user_smtp_config", "workflow_actions",
    "workflow_event_queue", "workflow_run_logs", "workflow_runs",
    "workflow_triggers", "workflows",
]

LEGACY_TABLES = [
    "leads_legacy", "scrape_history", "gmaps_sessions", "gmaps_session_leads",
    "gmaps_session_logs", "gmaps_session_events", "gmaps_session_tasks",
    "gmaps_task_chunks", "jobs_legacy",
]

TOOL_TO_SOURCE = {
    "gmaps": "gmaps", "google_maps": "gmaps",
    "webcrawler": "websearch", "web_crawler": "websearch",
    "linkedin": "websearch", "instagram": "websearch",
}


def log(msg: str) -> None:
    print("  %s" % msg)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute('PRAGMA table_info("%s")' % table)}


def make_uid(place_id: str, website: str, name: str, address: str) -> str:
    basis = (
        (place_id or "").strip()
        or (website or "").strip().lower()
        or "%s|%s" % ((name or "").strip().lower(), (address or "").strip().lower())
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


def score(email: str, phone: str, website: str) -> str:
    if email and (phone or website):
        return "strong"
    if email or phone:
        return "medium"
    return "weak"


PLACEHOLDERS = {"n/a", "na", "none", "null", "-", "unknown", "not found", "not available"}
SOCIAL_COLUMNS = ["facebook", "instagram", "twitter", "linkedin", "youtube"]

EMAIL_RE = re.compile(r"[^@\s;,]+@[^@\s;,]+\.[a-zA-Z]{2,}")
# A phone number, allowing the usual separators but capped so that runs of
# concatenated numbers do not match as one.
PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{6,18}\d")


def clean_email(raw: str) -> str:
    """The old scraper concatenated every address it found with ';'."""
    value = (raw or "").strip()
    if not value or value.lower() in PLACEHOLDERS:
        return ""
    matches = EMAIL_RE.findall(value)
    if not matches:
        return ""
    # Prefer a human-looking mailbox over a hashed/no-reply one.
    preferred = [
        m for m in matches
        if not re.match(r"^[0-9a-f]{16,}@", m, re.I)
        and not m.lower().startswith(("no-reply", "noreply", "donotreply"))
    ]
    return (preferred or matches)[0].strip().lower()


def clean_phone(raw: str) -> str:
    """Likewise for phone numbers, which arrived space-joined."""
    value = (raw or "").strip()
    if not value or value.lower() in PLACEHOLDERS:
        return ""
    match = PHONE_RE.search(value)
    if not match:
        return ""
    number = re.sub(r"\s{2,}", " ", match.group(0)).strip(" -.")
    digits = re.sub(r"\D", "", number)
    return number if 7 <= len(digits) <= 15 else ""


# A legacy phone field that contains an ISO date is a scraped calendar or
# changelog, not a phone list.
DATE_LIKE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}")
YEAR_RE = re.compile(r"(19|20)\d{2}")
MAX_RECOVERED_EMAILS = 10
MAX_RECOVERED_PHONES = 10


def clean_email_list(raw: str, own_domain: str = "") -> list[str]:
    """Every valid address in a legacy field, best first.

    The old scraper joined all addresses it found with ";", including Sentry and
    Wix telemetry mailboxes that belong to nobody, and on corporate contact
    pages it swept up a parent company's entire global directory. Junk is
    dropped, the business's own domain is preferred, and the list is capped.
    """
    value = (raw or "").strip()
    if not value or value.lower() in PLACEHOLDERS:
        return []
    junk = re.compile(r"(sentry\.|sentry-next|wixpress\.com|example\.(com|org)|yourdomain)", re.I)
    hashed = re.compile(r"^[0-9a-f]{16,}@", re.I)
    out: list[str] = []
    for match in EMAIL_RE.findall(value):
        candidate = match.strip().lower()
        if junk.search(candidate) or hashed.match(candidate):
            continue
        if candidate not in out:
            out.append(candidate)

    def rank(email: str) -> tuple[int, int, int]:
        domain = email.split("@", 1)[1] if "@" in email else ""
        mailbox = email.split("@", 1)[0]
        # An address on the business's own domain beats one harvested from a
        # partner or parent company listed on the same page.
        own = 0 if own_domain and own_domain in domain else 1
        if mailbox.startswith(("no-reply", "noreply", "donotreply")):
            return (own, 2, len(email))
        if mailbox in {"info", "hello", "contact", "enquiries", "office", "admin"}:
            return (own, 0, len(email))
        return (own, 1, len(email))

    return sorted(out, key=rank)[:MAX_RECOVERED_EMAILS]


def plausible_phone(value: str) -> bool:
    """Reject legacy fragments and year ranges.

    Legacy values came from a regex over whole web pages, so short digit runs
    with no country code are almost always debris rather than a number. Numbers
    scraped from Maps always carry a "+", so this only filters historic rows.
    """
    text = (value or "").strip()
    if not text:
        return False
    if re.match(r"^(19|20)\d{2}\s*[-/]\s*(19|20)\d{2}$", text):
        return False
    digits = re.sub(r"\D", "", text)
    if not (7 <= len(digits) <= 15):
        return False
    if len(digits) < 9 and not text.startswith(("+", "(")):
        return False
    return True


def rank_phones(values: list[str]) -> list[str]:
    """Most callable number first.

    A number in international form is the one worth calling; a bare fragment
    left over from a run-together legacy blob is not.
    """
    def key(value: str) -> tuple[int, int, int]:
        digits = re.sub(r"\D", "", value)
        international = 0 if value.strip().startswith("+") else 1
        plausible = 0 if 9 <= len(digits) <= 14 else 1
        return (international, plausible, -len(digits))

    return sorted(values, key=key)


def clean_phone_list(raw: str) -> list[str]:
    """Every plausible phone number in a legacy field.

    The permissive digit-run regex happily matches date ranges and numeric ids,
    so anything date-shaped is rejected outright.
    """
    value = (raw or "").strip()
    if not value or value.lower() in PLACEHOLDERS:
        return []
    if DATE_LIKE_RE.search(value):
        return []

    out: list[str] = []
    for match in PHONE_RE.findall(value):
        number = re.sub(r"\s{2,}", " ", match).strip(" -.")
        digits = re.sub(r"\D", "", number)
        if not (7 <= len(digits) <= 15):
            continue
        # A long unbroken digit run with no punctuation is an id, not a number.
        if len(digits) > 11 and not re.search(r"[+()\-\s]", number):
            continue
        # Two or more year-like groups means a date sequence, not a number.
        if len(YEAR_RE.findall(number)) >= 2:
            continue
        # Placeholder numbers such as "+49 (000) 000-00000".
        if len(set(digits)) <= 2 or "00000" in digits:
            continue
        if number not in out:
            out.append(number)

    return rank_phones([p for p in out if plausible_phone(p)])[:MAX_RECOVERED_PHONES]


def own_domain(website: str) -> str:
    """Bare registrable name of a website, for matching addresses against."""
    value = re.sub(r"^https?://", "", (website or "").strip().lower()).split("/")[0]
    if value.startswith("www."):
        value = value[4:]
    return value


def recover_multi_contacts(conn: sqlite3.Connection) -> None:
    """Repopulate the emails/phones arrays from the legacy tables.

    The first pass of this migration kept only the best single value per lead.
    The legacy tables still hold the originals, so every address and number can
    be recovered without touching the backup file.
    """
    legacy: dict[str, tuple[list[str], list[str]]] = {}

    if table_exists(conn, "gmaps_session_leads"):
        for row in conn.execute(
            "SELECT lead_uid, email, phone, website FROM gmaps_session_leads"
        ):
            uid = row["lead_uid"]
            if not uid:
                continue
            emails = clean_email_list(row["email"], own_domain(row["website"]))
            phones = clean_phone_list(row["phone"])
            if emails or phones:
                prev = legacy.get(uid, ([], []))
                legacy[uid] = (
                    prev[0] + [e for e in emails if e not in prev[0]],
                    prev[1] + [p for p in phones if p not in prev[1]],
                )

    if table_exists(conn, "leads_legacy"):
        for row in conn.execute("SELECT data, email, phone, website, title FROM leads_legacy"):
            try:
                data = json.loads(row["data"] or "{}")
            except (json.JSONDecodeError, TypeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            uid = make_uid(
                data.get("place_id", ""),
                row["website"] or data.get("website", ""),
                data.get("business_name") or data.get("name") or row["title"] or "",
                data.get("address", ""),
            )
            site = row["website"] or data.get("website", "")
            emails = clean_email_list(row["email"] or data.get("email", ""), own_domain(site))
            phones = clean_phone_list(row["phone"] or data.get("phone", ""))
            if emails or phones:
                prev = legacy.get(uid, ([], []))
                legacy[uid] = (
                    prev[0] + [e for e in emails if e not in prev[0]],
                    prev[1] + [p for p in phones if p not in prev[1]],
                )

    updates = []
    multi_email = multi_phone = 0
    for row in conn.execute("SELECT id, uid, email, phone FROM leads"):
        emails, phones = legacy.get(row["uid"], ([], []))
        # Anything already on the row stays, even without a legacy match.
        if row["email"] and row["email"] not in emails:
            emails = [row["email"]] + emails
        if row["phone"] and row["phone"] not in phones and plausible_phone(row["phone"]):
            phones = phones + [row["phone"]]
        # Re-rank the combined set so a good number beats a leftover fragment
        # that happened to be sitting in the singular column.
        phones = rank_phones(phones)[:MAX_RECOVERED_PHONES]
        if not emails and not phones:
            continue
        if len(emails) > 1:
            multi_email += 1
        if len(phones) > 1:
            multi_phone += 1
        updates.append(
            (
                json.dumps(emails), json.dumps(phones),
                emails[0] if emails else "", phones[0] if phones else "",
                row["id"],
            )
        )

    conn.executemany(
        "UPDATE leads SET emails=?, phones=?, email=?, phone=? WHERE id=?", updates
    )
    conn.commit()
    log("populated contact arrays on %d leads" % len(updates))
    log("  %d now hold more than one email, %d more than one phone" % (multi_email, multi_phone))


def clean_text(raw: str) -> str:
    value = (raw or "").strip()
    return "" if value.lower() in PLACEHOLDERS else value


def normalise(conn: sqlite3.Connection) -> None:
    """Strip placeholder values and split concatenated contact fields."""
    updates = []
    for row in conn.execute(
        "SELECT id, email, phone, website, business_name, owner_name, address, "
        "category, " + ", ".join(SOCIAL_COLUMNS) + " FROM leads"
    ):
        email = clean_email(row["email"])
        phone = clean_phone(row["phone"])
        website = clean_text(row["website"])
        owner = clean_text(row["owner_name"])
        address = clean_text(row["address"])
        category = clean_text(row["category"])
        name = clean_text(row["business_name"])
        socials = {k: clean_text(row[k]) for k in SOCIAL_COLUMNS}

        if (
            email != (row["email"] or "")
            or phone != (row["phone"] or "")
            or website != (row["website"] or "")
            or owner != (row["owner_name"] or "")
            or address != (row["address"] or "")
            or category != (row["category"] or "")
            or name != (row["business_name"] or "")
            or any(socials[k] != (row[k] or "") for k in SOCIAL_COLUMNS)
        ):
            updates.append(
                (email, phone, website, owner, address, category, name,
                 *[socials[k] for k in SOCIAL_COLUMNS],
                 score(email, phone, website), row["id"])
            )

    conn.executemany(
        "UPDATE leads SET email=?, phone=?, website=?, owner_name=?, address=?, "
        "category=?, business_name=?, "
        + ", ".join("%s=?" % c for c in SOCIAL_COLUMNS)
        + ", quality=? WHERE id=?",
        updates,
    )

    # Email drafts addressed to a placeholder are useless.
    removed = conn.execute(
        "DELETE FROM email_templates WHERE lower(trim(email)) IN "
        "('n/a','na','none','null','-','unknown','') OR email NOT LIKE '%@%'"
    ).rowcount or 0
    conn.commit()
    log("normalised %d lead rows" % len(updates))
    log("removed %d email drafts with no usable address" % removed)


def repair_email_templates(conn: sqlite3.Connection) -> None:
    """Rebuild email_templates if its foreign key was rewritten by a rename.

    SQLite's ALTER TABLE ... RENAME TO rewrites references to the old name in
    *other* tables' foreign keys. Renaming ``leads`` to ``leads_legacy`` therefore
    repointed email_templates.lead_id at the legacy table, which then rejected
    every draft written against the new leads table. Drafts should outlive the
    lead they were written for anyway, so the rebuilt table drops that FK.
    """
    if not table_exists(conn, "email_templates"):
        return
    refs = {row[2] for row in conn.execute("PRAGMA foreign_key_list(email_templates)")}
    if "leads_legacy" not in refs:
        return

    conn.executescript(
        """
        CREATE TABLE email_templates_new (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            lead_id       INTEGER,
            business_name TEXT DEFAULT '',
            email         TEXT DEFAULT '',
            subject       TEXT DEFAULT '',
            body          TEXT DEFAULT '',
            keyword       TEXT DEFAULT '',
            location      TEXT DEFAULT '',
            sender_info   TEXT DEFAULT '{}',
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        INSERT INTO email_templates_new
            SELECT id, user_id, lead_id, business_name, email, subject, body,
                   keyword, location, sender_info, created_at
            FROM email_templates;
        DROP TABLE email_templates;
        ALTER TABLE email_templates_new RENAME TO email_templates;
        CREATE INDEX IF NOT EXISTS idx_email_tpl_user
            ON email_templates(user_id, created_at DESC);
        """
    )
    conn.commit()
    log("rebuilt email_templates to drop the stale leads_legacy foreign key")


def migrate(db_path: str, backup: bool, drop_legacy: bool) -> None:
    if not os.path.exists(db_path):
        print("No database at %s - nothing to migrate." % db_path)
        return

    if backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = "%s.backup-%s" % (db_path, stamp)
        shutil.copy2(db_path, dest)
        print("Backup written to %s" % dest)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    # Keep ALTER TABLE ... RENAME from rewriting foreign keys that point
    # at the table being renamed.
    conn.execute("PRAGMA legacy_alter_table=ON")

    # --- 1. move the old leads table aside ---------------------------------
    print("\n[1/6] Preparing tables")
    if table_exists(conn, "leads") and "uid" not in columns(conn, "leads"):
        if table_exists(conn, "leads_legacy"):
            conn.execute("DROP TABLE leads_legacy")
        conn.execute("ALTER TABLE leads RENAME TO leads_legacy")
        log("renamed leads -> leads_legacy")
    if table_exists(conn, "jobs") and "source" not in columns(conn, "jobs"):
        if table_exists(conn, "jobs_legacy"):
            conn.execute("DROP TABLE jobs_legacy")
        conn.execute("ALTER TABLE jobs RENAME TO jobs_legacy")
        log("renamed jobs -> jobs_legacy")

    conn.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS leaves an existing table alone, so columns
    # added after the first migration have to be applied separately.
    _add_missing_columns(conn)
    conn.commit()
    log("new schema applied")
    repair_email_templates(conn)

    # --- 2. jobs ------------------------------------------------------------
    print("\n[2/6] Migrating sessions and scrape history into jobs")
    inserted = 0

    if table_exists(conn, "gmaps_sessions"):
        for row in conn.execute("SELECT * FROM gmaps_sessions"):
            status = (row["status"] or "").lower()
            status = {
                "success": "completed", "completed": "completed", "done": "completed",
                "failed": "failed", "error": "failed",
                "stopped": "stopped", "cancelled": "stopped",
            }.get(status, "failed")
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, user_id, source, keyword, location, "
                "max_leads, enrich_mode, status, stage, progress, message, total_found, "
                "created_at, finished_at, params) "
                "VALUES (?,?,'gmaps',?,?,?,'standard',?,'done',?,?,?,?,?,'{}')",
                (
                    row["session_id"], row["user_id"], row["keyword"] or "",
                    row["place"] or "", row["max_leads"] or 0, status,
                    row["progress"] or 0, row["message"] or "",
                    row["results_count"] or 0, row["created_at"], row["finished_at"],
                ),
            )
            inserted += conn.total_changes and 1 or 0
    log("gmaps_sessions -> jobs")

    if table_exists(conn, "scrape_history"):
        for row in conn.execute("SELECT * FROM scrape_history"):
            status = (row["status"] or "").lower()
            status = {"completed": "completed", "running": "failed",
                      "stopped": "stopped", "failed": "failed"}.get(status, "failed")
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, user_id, source, keyword, location, "
                "status, stage, progress, message, total_found, created_at, finished_at, params) "
                "VALUES (?,?,?,?,?,?,'done',100,'',?,?,?,?)",
                (
                    row["job_id"], row["user_id"],
                    TOOL_TO_SOURCE.get(row["tool"], "gmaps"),
                    row["keyword"] or "", row["location"] or "", status,
                    row["lead_count"] or 0, row["started_at"], row["finished_at"],
                    json.dumps({"legacy_tool": row["tool"]}),
                ),
            )
    conn.commit()
    job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log("jobs now holds %d rows" % job_count)

    # --- 3. gmaps session leads --------------------------------------------
    print("\n[3/6] Migrating Google Maps session leads")
    moved = 0
    if table_exists(conn, "gmaps_session_leads"):
        known_jobs = {r[0] for r in conn.execute("SELECT id FROM jobs")}
        cols = columns(conn, "gmaps_session_leads")
        for row in conn.execute("SELECT * FROM gmaps_session_leads"):
            job_id = row["session_id"] if row["session_id"] in known_jobs else ""
            keyword = location = ""
            if job_id:
                jr = conn.execute(
                    "SELECT keyword, location FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if jr:
                    keyword, location = jr["keyword"], jr["location"]

            email = row["email"] or ""
            phone = row["phone"] or ""
            website = row["website"] or ""
            uid = row["lead_uid"] or make_uid(
                "", website, row["business_name"] or "", row["address"] or ""
            )
            extra = {}
            if "payload" in cols and row["payload"]:
                try:
                    extra = json.loads(row["payload"])
                except (json.JSONDecodeError, TypeError):
                    extra = {}
            for social in ("tiktok", "pinterest"):
                if social in cols and row[social]:
                    extra[social] = row[social]

            conn.execute(
                "INSERT OR IGNORE INTO leads (job_id, user_id, uid, source, business_name, "
                "owner_name, email, phone, website, address, rating, reviews, category, "
                "latitude, longitude, facebook, instagram, twitter, linkedin, youtube, "
                "quality, enriched, keyword, location, extra, created_at, updated_at) "
                "VALUES (?,?,?,'gmaps',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, row["user_id"], uid, row["business_name"] or "",
                    row["owner_name"] or "", email, phone, website, row["address"] or "",
                    row["rating"] or "", row["reviews"] or "", row["category"] or "",
                    row["latitude"] or "", row["longitude"] or "",
                    row["facebook"] or "", row["instagram"] or "", row["twitter"] or "",
                    row["linkedin"] or "", row["youtube"] or "",
                    score(email, phone, website), 0, keyword, location,
                    json.dumps(extra), row["created_at"], row["updated_at"],
                ),
            )
            moved += 1
    conn.commit()
    log("processed %d gmaps session leads" % moved)

    # --- 4. legacy leads table ---------------------------------------------
    print("\n[4/6] Migrating the legacy leads table")
    moved = 0
    if table_exists(conn, "leads_legacy"):
        history = {
            r["id"]: r
            for r in conn.execute("SELECT id, job_id, tool FROM scrape_history")
        } if table_exists(conn, "scrape_history") else {}

        for row in conn.execute("SELECT * FROM leads_legacy"):
            try:
                data = json.loads(row["data"] or "{}")
            except (json.JSONDecodeError, TypeError):
                data = {}
            if not isinstance(data, dict):
                data = {}

            hist = history.get(row["scrape_id"])
            job_id = hist["job_id"] if hist else ""
            if job_id and not conn.execute(
                "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
            ).fetchone():
                job_id = ""

            name = (
                data.get("business_name") or data.get("name") or row["title"] or ""
            )
            email = row["email"] or data.get("email") or ""
            phone = row["phone"] or data.get("phone") or ""
            website = row["website"] or data.get("website") or ""
            address = data.get("address") or ""
            uid = make_uid(data.get("place_id", ""), website, name, address)

            # Skip anything already brought over from the richer gmaps table.
            if conn.execute(
                "SELECT 1 FROM leads WHERE user_id = ? AND uid = ?", (row["user_id"], uid)
            ).fetchone():
                continue

            conn.execute(
                "INSERT OR IGNORE INTO leads (job_id, user_id, uid, source, business_name, "
                "owner_name, email, phone, website, address, rating, reviews, category, "
                "latitude, longitude, facebook, instagram, twitter, linkedin, youtube, "
                "summary, quality, enriched, keyword, location, extra, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)",
                (
                    job_id, row["user_id"], uid,
                    TOOL_TO_SOURCE.get(row["tool"], "gmaps"), name,
                    data.get("owner_name", ""), email, phone, website, address,
                    str(data.get("rating", "") or ""), str(data.get("reviews", "") or ""),
                    data.get("category", ""),
                    str(data.get("latitude", "") or ""), str(data.get("longitude", "") or ""),
                    data.get("facebook", ""), data.get("instagram", ""),
                    data.get("twitter", ""), data.get("linkedin", ""),
                    data.get("youtube", ""), data.get("summary", ""),
                    row["quality"] or score(email, phone, website),
                    row["keyword"] or "", row["location"] or "",
                    json.dumps({"legacy_tool": row["tool"]}), row["created_at"],
                ),
            )
            moved += 1
    conn.commit()
    log("brought over %d legacy leads" % moved)

    # --- 5. logs ------------------------------------------------------------
    print("\n[5/6] Migrating session logs into job_events")
    moved = 0
    if table_exists(conn, "gmaps_session_logs"):
        known_jobs = {r[0] for r in conn.execute("SELECT id FROM jobs")}
        for row in conn.execute(
            "SELECT * FROM gmaps_session_logs ORDER BY id"
        ):
            if row["session_id"] not in known_jobs:
                continue
            conn.execute(
                "INSERT INTO job_events (job_id, user_id, level, stage, progress, message, created_at) "
                "VALUES (?,?,'info',?,?,?,?)",
                (
                    row["session_id"], row["user_id"], row["phase"] or "",
                    row["progress"] or 0, row["message"] or "", row["created_at"],
                ),
            )
            moved += 1
    conn.commit()
    log("brought over %d log lines" % moved)

    # --- 6. normalise -------------------------------------------------------
    print("\n[6/7] Normalising legacy field values")
    normalise(conn)
    recover_multi_contacts(conn)

    # --- 7. drop the dead weight -------------------------------------------
    print("\n[7/7] Dropping unused tables")
    dropped = []
    for table in DEAD_TABLES:
        if table_exists(conn, table):
            count = conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
            if count:
                log("KEEPING %s - it unexpectedly holds %d rows" % (table, count))
                continue
            conn.execute('DROP TABLE "%s"' % table)
            dropped.append(table)
    conn.commit()
    log("dropped %d empty tables: %s" % (len(dropped), ", ".join(dropped) or "none"))

    if drop_legacy:
        removed = []
        for table in LEGACY_TABLES:
            if table_exists(conn, table):
                conn.execute('DROP TABLE "%s"' % table)
                removed.append(table)
        conn.commit()
        log("dropped %d legacy tables: %s" % (len(removed), ", ".join(removed) or "none"))
    else:
        log("legacy tables kept - rerun with --drop-legacy once you are happy")

    # --- summary ------------------------------------------------------------
    print("\nResult")
    for table in ("users", "license_keys", "jobs", "leads", "job_events", "email_templates"):
        count = conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        print("  %-18s %d" % (table, count))

    with_email = conn.execute("SELECT COUNT(*) FROM leads WHERE email != ''").fetchone()[0]
    print("  %-18s %d" % ("leads with email", with_email))

    conn.execute("VACUUM")
    conn.close()
    print("\nMigration complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the LeadGen database.")
    parser.add_argument("--db", default="leadgen.db")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--drop-legacy", action="store_true")
    args = parser.parse_args()
    migrate(args.db, backup=not args.no_backup, drop_legacy=args.drop_legacy)


if __name__ == "__main__":
    main()
