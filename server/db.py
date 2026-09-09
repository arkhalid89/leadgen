"""SQLite access layer.

One connection per thread, WAL mode, and a single declarative schema. The old
codebase spread 30+ tables across six feature modules; 20 of them never held a
single row. What survives here is only what the tool actually reads and writes.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from . import settings

log = logging.getLogger(__name__)

_local = threading.local()

# Bump to force the full-text index to be rebuilt on next start.
FTS_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    full_name   TEXT DEFAULT '',
    license_key TEXT DEFAULT '',
    is_active   INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    last_login  TEXT
);

CREATE TABLE IF NOT EXISTS license_keys (
    key         TEXT PRIMARY KEY,
    plan        TEXT DEFAULT 'pro',
    max_uses    INTEGER DEFAULT 1,
    used_count  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    expires_at  TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id             TEXT PRIMARY KEY,
    user_id        INTEGER NOT NULL,
    source         TEXT NOT NULL,
    keyword        TEXT DEFAULT '',
    location       TEXT DEFAULT '',
    max_leads      INTEGER DEFAULT 0,
    enrich_mode    TEXT DEFAULT 'standard',
    status         TEXT DEFAULT 'queued',
    stage          TEXT DEFAULT 'queued',
    progress       INTEGER DEFAULT 0,
    message        TEXT DEFAULT '',
    total_found    INTEGER DEFAULT 0,
    enriched_count INTEGER DEFAULT 0,
    error          TEXT DEFAULT '',
    params         TEXT DEFAULT '{}',
    created_at     TEXT DEFAULT (datetime('now')),
    started_at     TEXT,
    finished_at    TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT DEFAULT '',
    user_id       INTEGER NOT NULL,
    uid           TEXT NOT NULL,
    source        TEXT DEFAULT 'gmaps',
    business_name TEXT DEFAULT '',
    owner_name    TEXT DEFAULT '',
    email         TEXT DEFAULT '',
    phone         TEXT DEFAULT '',
    emails        TEXT DEFAULT '[]',
    phones        TEXT DEFAULT '[]',
    -- Where the email came from: 'website' (read off their site, factual),
    -- 'ai' (model-suggested pattern, unverified), '' (legacy/unknown).
    email_source  TEXT DEFAULT '',
    email_status  TEXT DEFAULT '',
    website       TEXT DEFAULT '',
    address       TEXT DEFAULT '',
    rating        TEXT DEFAULT '',
    reviews       TEXT DEFAULT '',
    category      TEXT DEFAULT '',
    latitude      TEXT DEFAULT '',
    longitude     TEXT DEFAULT '',
    place_id      TEXT DEFAULT '',
    facebook      TEXT DEFAULT '',
    instagram     TEXT DEFAULT '',
    twitter       TEXT DEFAULT '',
    linkedin      TEXT DEFAULT '',
    youtube       TEXT DEFAULT '',
    summary       TEXT DEFAULT '',
    quality       TEXT DEFAULT 'weak',
    enriched      INTEGER DEFAULT 0,
    keyword       TEXT DEFAULT '',
    location      TEXT DEFAULT '',
    extra         TEXT DEFAULT '{}',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(job_id, uid),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_leads_user ON leads(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_job ON leads(job_id);
CREATE INDEX IF NOT EXISTS idx_leads_user_source ON leads(user_id, source);
CREATE INDEX IF NOT EXISTS idx_leads_user_keyword ON leads(user_id, keyword);
CREATE INDEX IF NOT EXISTS idx_leads_user_email ON leads(user_id, email);

CREATE TABLE IF NOT EXISTS job_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    user_id    INTEGER NOT NULL,
    level      TEXT DEFAULT 'info',
    stage      TEXT DEFAULT '',
    progress   INTEGER DEFAULT 0,
    message    TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id);

CREATE TABLE IF NOT EXISTS contact_cache (
    domain      TEXT PRIMARY KEY,
    emails      TEXT DEFAULT '[]',
    phones      TEXT DEFAULT '[]',
    owner_name  TEXT DEFAULT '',
    facebook    TEXT DEFAULT '',
    instagram   TEXT DEFAULT '',
    twitter     TEXT DEFAULT '',
    linkedin    TEXT DEFAULT '',
    youtube     TEXT DEFAULT '',
    grounded    INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS email_templates (
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
CREATE INDEX IF NOT EXISTS idx_email_tpl_user ON email_templates(user_id, created_at DESC);

-- ---------------------------------------------------------------- lists
-- A named, saved set of leads. The industry calls these lists or segments;
-- they are what turns a pile of scraped rows into something you can work.
CREATE TABLE IF NOT EXISTS lead_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    colour      TEXT DEFAULT '#4f7cff',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS lead_list_members (
    list_id    INTEGER NOT NULL,
    lead_id    INTEGER NOT NULL,
    added_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (list_id, lead_id)
);
CREATE INDEX IF NOT EXISTS idx_list_members_lead ON lead_list_members(lead_id);

-- ---------------------------------------------------------------- tags
CREATE TABLE IF NOT EXISTS lead_tags (
    lead_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    tag        TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (lead_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_lead_tags_user ON lead_tags(user_id, tag);

-- ---------------------------------------------------------------- pipeline
-- Where a lead has got to, and everything that has happened to it.
CREATE TABLE IF NOT EXISTS lead_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    kind       TEXT DEFAULT 'note',
    body       TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lead_notes_lead ON lead_notes(lead_id, id DESC);

-- ---------------------------------------------------------------- suppression
-- Addresses and domains that must never be contacted again. Unsubscribes,
-- bounces, competitors, and anyone who asked. Checked before every export and
-- every draft, because getting this wrong is a legal problem, not a data one.
CREATE TABLE IF NOT EXISTS suppression (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    value      TEXT NOT NULL,
    kind       TEXT DEFAULT 'email',
    reason     TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, value)
);
CREATE INDEX IF NOT EXISTS idx_suppression_user ON suppression(user_id, value);

-- ---------------------------------------------------------------- saved searches
CREATE TABLE IF NOT EXISTS saved_searches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    source     TEXT DEFAULT 'gmaps',
    keyword    TEXT DEFAULT '',
    location   TEXT DEFAULT '',
    params     TEXT DEFAULT '{}',
    run_count  INTEGER DEFAULT 0,
    last_run   TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- ---------------------------------------------------------------- full-text
-- LIKE '%term%' cannot use an index, so lead search was a full table scan.
-- An external-content FTS5 table indexes the words instead and ranks with
-- bm25, which is what search engines use.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS leads_fts USING fts5(
    business_name, email, phone, website, address, category, keyword, location,
    content='leads', content_rowid='id', tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS leads_fts_insert AFTER INSERT ON leads BEGIN
    INSERT INTO leads_fts(rowid, business_name, email, phone, website, address,
                          category, keyword, location)
    VALUES (new.id, new.business_name, new.email, new.phone, new.website,
            new.address, new.category, new.keyword, new.location);
END;
CREATE TRIGGER IF NOT EXISTS leads_fts_delete AFTER DELETE ON leads BEGIN
    INSERT INTO leads_fts(leads_fts, rowid, business_name, email, phone, website,
                          address, category, keyword, location)
    VALUES ('delete', old.id, old.business_name, old.email, old.phone, old.website,
            old.address, old.category, old.keyword, old.location);
END;
CREATE TRIGGER IF NOT EXISTS leads_fts_update AFTER UPDATE ON leads BEGIN
    INSERT INTO leads_fts(leads_fts, rowid, business_name, email, phone, website,
                          address, category, keyword, location)
    VALUES ('delete', old.id, old.business_name, old.email, old.phone, old.website,
            old.address, old.category, old.keyword, old.location);
    INSERT INTO leads_fts(rowid, business_name, email, phone, website, address,
                          category, keyword, location)
    VALUES (new.id, new.business_name, new.email, new.phone, new.website,
            new.address, new.category, new.keyword, new.location);
END;
"""



def get_conn() -> sqlite3.Connection:
    """Thread-local connection. Safe because each worker thread gets its own."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(settings.DB_PATH, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with transaction() as conn:
        return conn.execute(sql, params)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for json_field in ("extra", "params", "sender_info", "emails", "phones"):
        if json_field in out and isinstance(out[json_field], str):
            empty = "[]" if json_field in ("emails", "phones") else "{}"
            try:
                out[json_field] = json.loads(out[json_field] or empty)
            except json.JSONDecodeError:
                out[json_field] = [] if empty == "[]" else {}
    return out


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]


ADDITIVE_COLUMNS = [
    ("leads", "emails", "TEXT DEFAULT '[]'"),
    ("leads", "phones", "TEXT DEFAULT '[]'"),
    ("leads", "email_source", "TEXT DEFAULT ''"),
    ("leads", "email_status", "TEXT DEFAULT ''"),
    ("leads", "status", "TEXT DEFAULT 'new'"),
    ("leads", "favourite", "INTEGER DEFAULT 0"),
]


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema without a rebuild."""
    for table, column, spec in ADDITIVE_COLUMNS:
        existing = {r[1] for r in conn.execute('PRAGMA table_info("%s")' % table)}
        if column not in existing:
            conn.execute('ALTER TABLE "%s" ADD COLUMN %s %s' % (table, column, spec))


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    # Populate the search index the first time it appears on an existing
    # database - the triggers only cover rows written from now on.
    # The search index is populated once, tracked by a marker. Counting rows
    # cannot tell us: COUNT(*) on an external-content FTS table reads the
    # *content* table, so it always matches and never reveals an empty index.
    try:
        marker = conn.execute(
            "SELECT value FROM meta WHERE key = 'fts_version'"
        ).fetchone()
        if (marker[0] if marker else "") != FTS_VERSION:
            conn.execute("INSERT INTO leads_fts(leads_fts) VALUES('rebuild')")
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('fts_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (FTS_VERSION,),
            )
            total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            log.info("built the lead search index over %d rows", total)
    except sqlite3.OperationalError as exc:
        log.warning("could not build the search index: %s", exc)

    # Seed a demo activation key on a fresh install so the app is usable.
    if conn.execute("SELECT COUNT(*) FROM license_keys").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO license_keys (key, plan, max_uses) VALUES (?, 'pro', 1000)",
            ("LEAD-PRO-2026-DEMO",),
        )
    conn.commit()
