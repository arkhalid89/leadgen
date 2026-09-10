"""Job orchestration.

A job is one run of one source: discover leads, optionally enrich them with
Gemini, persist as we go. Scraping is blocking (Selenium) so each job owns a
worker thread; the Gemini phase opens its own event loop inside that thread.

Progress is published two ways: appended to ``job_events`` for history, and
pushed to in-memory subscriber queues for the SSE stream.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from . import db
from .enrich.ai_emails import AiEmailSuggester
from .enrich.emails import EmailFinder
from .enrich import smtp_verify
from .sources.gmaps import GoogleMapsSource
from .sources.websearch import WebSearchSource

log = logging.getLogger(__name__)

SOURCES = ("gmaps", "websearch")
# A retry pass over leads already in the database, rather than a new search.
ENRICH_SOURCE = "find-emails"
ENRICH_MODES = ("off", "standard")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalise_contacts(lead: dict) -> None:
    """Keep the array and singular contact fields consistent.

    ``emails``/``phones`` hold everything found; ``email``/``phone`` hold the
    first of each so existing filters, sorting and exports keep working.
    """
    from .enrich.emails import clean_emails, registered_domain

    raw_emails = lead.get("emails") or lead.get("email") or []
    if isinstance(raw_emails, str):
        raw_emails = [raw_emails]
    emails = clean_emails(list(raw_emails), registered_domain(lead.get("website", "")))
    lead["emails"] = emails
    lead["email"] = emails[0] if emails else ""

    phones: list[str] = []
    raw_phones = lead.get("phones") or []
    if isinstance(raw_phones, str):
        raw_phones = [raw_phones]
    for value in list(raw_phones) + [lead.get("phone") or ""]:
        text = (value or "").strip()
        if text and text not in phones:
            phones.append(text)
    lead["phones"] = phones
    lead["phone"] = phones[0] if phones else ""


def _tag_website_source(leads: list[dict]) -> None:
    """Mark addresses that came off a real website as verified fact."""
    for lead in leads:
        if (lead.get("email") or "").strip() and not lead.get("email_source"):
            lead["email_source"] = "website"
            lead["email_status"] = "verified"


def score_lead(lead: dict) -> str:
    has_email = bool((lead.get("email") or "").strip())
    has_phone = bool((lead.get("phone") or "").strip())
    has_site = bool((lead.get("website") or "").strip())
    if has_email and (has_phone or has_site):
        return "strong"
    if has_email or has_phone:
        return "medium"
    return "weak"


def lead_uid(lead: dict) -> str:
    """Stable identity for deduplication within a job."""
    basis = (
        (lead.get("place_id") or "").strip()
        or (lead.get("website") or "").strip().lower()
        or "%s|%s" % (
            (lead.get("business_name") or "").strip().lower(),
            (lead.get("address") or "").strip().lower(),
        )
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


class _JobHandle:
    """Live state for one running job."""

    def __init__(self, job_id: str, user_id: int):
        self.job_id = job_id
        self.user_id = user_id
        self.stop_event = threading.Event()
        self.source: Any = None
        self.enricher: EmailFinder | None = None
        self.thread: threading.Thread | None = None

    def request_stop(self) -> None:
        self.stop_event.set()
        if self.source is not None:
            try:
                self.source.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.enricher is not None:
            self.enricher.cancel()


class JobManager:
    """Owns running jobs and the SSE fan-out."""

    def __init__(self) -> None:
        self._jobs: dict[str, _JobHandle] = {}
        self._subscribers: dict[str, list[queue.Queue]] = {}
        self._lock = threading.RLock()

    # -- pub/sub -------------------------------------------------------------

    def subscribe(self, job_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(job_id)
            if not subs:
                return
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subscribers.pop(job_id, None)

    def _publish(self, job_id: str, payload: dict) -> None:
        with self._lock:
            subs = list(self._subscribers.get(job_id, []))
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                log.debug("dropping SSE event for %s: subscriber is slow", job_id)

    # -- job state -----------------------------------------------------------

    def _emit(
        self,
        job_id: str,
        user_id: int,
        message: str,
        percent: int = -1,
        stage: str | None = None,
        level: str = "info",
    ) -> None:
        fields: list[str] = ["message = ?"]
        params: list[Any] = [message]
        if percent >= 0:
            fields.append("progress = ?")
            params.append(min(100, max(0, percent)))
        if stage:
            fields.append("stage = ?")
            params.append(stage)
        params.append(job_id)
        db.execute("UPDATE jobs SET " + ", ".join(fields) + " WHERE id = ?", tuple(params))

        db.execute(
            "INSERT INTO job_events (job_id, user_id, level, stage, progress, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, user_id, level, stage or "", max(0, percent), message),
        )
        self._publish(
            job_id,
            {
                "type": "progress",
                "job_id": job_id,
                "message": message,
                "progress": percent if percent >= 0 else None,
                "stage": stage,
                "level": level,
                "at": _now(),
            },
        )

    # -- lifecycle -----------------------------------------------------------

    def active_count(self, user_id: int) -> int:
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE user_id = ? AND status IN ('queued','running')",
            (user_id,),
        )
        return int(row["n"]) if row else 0

    def create_find_emails(
        self, user_id: int, lead_ids: list[int], use_ai: bool,
        verify: bool = False,
    ) -> str:
        """Queue a retry pass over leads that have no email yet."""
        job_id = uuid.uuid4().hex[:16]
        db.execute(
            "INSERT INTO jobs (id, user_id, source, keyword, location, max_leads, "
            "enrich_mode, status, stage, message, params) "
            "VALUES (?, ?, ?, 'Find missing emails', '', ?, 'standard', 'queued', "
            "'queued', 'Queued', ?)",
            (
                job_id, user_id, ENRICH_SOURCE, len(lead_ids),
                json.dumps({"lead_ids": lead_ids, "use_ai": use_ai, "verify": verify}),
            ),
        )
        handle = _JobHandle(job_id, user_id)
        with self._lock:
            self._jobs[job_id] = handle
        thread = threading.Thread(
            target=self._run_find_emails,
            args=(handle, lead_ids, use_ai, verify),
            daemon=True,
            name="find-emails-%s" % job_id,
        )
        handle.thread = thread
        thread.start()
        return job_id

    def _run_find_emails(
        self, handle: _JobHandle, lead_ids: list[int], use_ai: bool,
        verify: bool = False,
    ) -> None:
        job_id, user_id = handle.job_id, handle.user_id

        def progress(message: str, percent: int = -1, stage: str | None = None) -> None:
            self._emit(job_id, user_id, message, percent, stage)

        try:
            db.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, stage = 'enrich' "
                "WHERE id = ?",
                (_now(), job_id),
            )
            placeholders = ",".join("?" * len(lead_ids))
            rows = db.rows_to_dicts(
                db.query(
                    "SELECT * FROM leads WHERE user_id = ? AND id IN (%s)" % placeholders,
                    tuple([user_id, *lead_ids]),
                )
            )
            leads = [r for r in rows if not (r.get("email") or "").strip()]
            progress("Retrying %d lead(s) that have no email yet." % len(leads), 5, "enrich")
            if not leads:
                self._finish_update(job_id, user_id, "completed", 0, "Nothing to do.")
                return

            finder = EmailFinder(on_progress=lambda m, p: progress(m, p, "enrich"))
            handle.enricher = finder
            found = asyncio.run(finder.enrich(leads))
            _tag_website_source(leads)
            self._update_leads(user_id, leads)

            if verify and not handle.stop_event.is_set():
                found += self._verify_pass(leads, user_id, progress)

            if use_ai and not handle.stop_event.is_set():
                still_missing = [l for l in leads if not (l.get("email") or "").strip()]
                if not still_missing:
                    progress("Every lead now has an email - AI was not needed.", -1, "enrich")
                else:
                    suggester = AiEmailSuggester(
                        on_progress=lambda m, p: progress(m, p, "enrich")
                    )
                    handle.enricher = suggester
                    if suggester.configured:
                        found += asyncio.run(suggester.suggest(still_missing))
                        self._update_leads(user_id, still_missing)
                    else:
                        progress(
                            "AI suggestions were requested but no Gemini key is configured.",
                            -1, "enrich",
                        )

            status = "stopped" if handle.stop_event.is_set() else "completed"
            self._finish_update(job_id, user_id, status, found)

        except Exception as exc:  # noqa: BLE001
            log.exception("find-emails job %s failed", job_id)
            db.execute(
                "UPDATE jobs SET status = 'failed', error = ?, finished_at = ?, "
                "stage = 'failed', message = ? WHERE id = ?",
                (str(exc), _now(), "Failed: %s" % exc, job_id),
            )
            self._publish(
                job_id,
                {"type": "done", "job_id": job_id, "status": "failed", "error": str(exc)},
            )
        finally:
            with self._lock:
                self._jobs.pop(job_id, None)

    def _verify_pass(self, leads: list[dict], user_id: int, progress) -> int:
        """Confirm scraped addresses, then guess-and-verify for the rest.

        Both halves ask the domain's own mail server, so a "verified" address
        here is a mailbox that actually accepted a delivery attempt, not a
        pattern that merely looks plausible.
        """
        async def run() -> tuple[dict, int]:
            verifier = smtp_verify.SmtpVerifier()
            if not await verifier.port_reachable():
                return {}, -1
            tally = await smtp_verify.verify_existing(
                leads, verifier, on_progress=lambda m, p: progress(m, p, "verify")
            )
            gained = await smtp_verify.discover_by_pattern(
                leads, verifier, on_progress=lambda m, p: progress(m, p, "verify")
            )
            return tally, gained

        try:
            tally, gained = asyncio.run(run())
        except Exception as exc:  # noqa: BLE001 - verification is best-effort
            log.exception("smtp verification failed")
            progress("SMTP verification failed: %s" % exc, -1, "verify")
            return 0

        if gained < 0:
            progress(
                "Outbound port 25 is blocked on this machine, so mailboxes cannot be "
                "verified. Run the app from a host that allows outbound port 25 to "
                "enable this.",
                -1,
                "verify",
            )
            return 0

        if tally:
            progress(
                "Verified existing addresses: "
                + ", ".join("%d %s" % (n, k) for k, n in sorted(tally.items())),
                -1,
                "verify",
            )
        progress(
            "Confirmed %d mailbox(es) by guessing common addresses and testing them."
            % gained,
            -1,
            "verify",
        )
        self._update_leads(user_id, leads)
        return gained

    def _update_leads(self, user_id: int, leads: list[dict]) -> None:
        """Write contact fields back onto leads that already exist."""
        rows = []
        for lead in leads:
            normalise_contacts(lead)
            rows.append(
                (
                    lead.get("email", ""), json.dumps(lead.get("emails") or []),
                    lead.get("email_source", ""), lead.get("email_status", ""),
                    lead.get("facebook", ""), lead.get("instagram", ""),
                    lead.get("twitter", ""), lead.get("linkedin", ""),
                    lead.get("youtube", ""), score_lead(lead),
                    int(lead.get("enriched") or 0), lead["id"], user_id,
                )
            )
        with db.transaction() as conn:
            conn.executemany(
                """
                UPDATE leads SET
                    email        = ?,
                    emails       = ?,
                    email_source = ?,
                    email_status = ?,
                    facebook     = COALESCE(NULLIF(?, ''), facebook),
                    instagram    = COALESCE(NULLIF(?, ''), instagram),
                    twitter      = COALESCE(NULLIF(?, ''), twitter),
                    linkedin     = COALESCE(NULLIF(?, ''), linkedin),
                    youtube      = COALESCE(NULLIF(?, ''), youtube),
                    quality      = ?,
                    enriched     = ?,
                    updated_at   = datetime('now')
                WHERE id = ? AND user_id = ?
                """,
                rows,
            )

    def _finish_update(
        self, job_id: str, user_id: int, status: str, found: int,
        message: str | None = None,
    ) -> None:
        summary = message or "Found %d new email address(es)." % found
        db.execute(
            "UPDATE jobs SET status = ?, stage = 'done', progress = 100, message = ?, "
            "enriched_count = ?, finished_at = ? WHERE id = ?",
            (status, summary, found, _now(), job_id),
        )
        db.execute(
            "INSERT INTO job_events (job_id, user_id, level, stage, progress, message) "
            "VALUES (?, ?, 'info', 'done', 100, ?)",
            (job_id, user_id, summary),
        )
        self._publish(
            job_id,
            {"type": "done", "job_id": job_id, "status": status,
             "message": summary, "enriched_count": found},
        )

    def create(
        self,
        user_id: int,
        source: str,
        keyword: str,
        location: str,
        max_leads: int,
        enrich_mode: str,
    ) -> str:
        job_id = uuid.uuid4().hex[:16]
        db.execute(
            "INSERT INTO jobs (id, user_id, source, keyword, location, max_leads, "
            "enrich_mode, status, stage, message, params) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 'queued', 'Queued', ?)",
            (
                job_id,
                user_id,
                source,
                keyword,
                location,
                max_leads,
                enrich_mode,
                json.dumps({"source": source, "enrich_mode": enrich_mode}),
            ),
        )

        handle = _JobHandle(job_id, user_id)
        with self._lock:
            self._jobs[job_id] = handle
        thread = threading.Thread(
            target=self._run,
            args=(handle, source, keyword, location, max_leads, enrich_mode),
            daemon=True,
            name="job-%s" % job_id,
        )
        handle.thread = thread
        thread.start()
        return job_id

    def stop(self, job_id: str) -> bool:
        with self._lock:
            handle = self._jobs.get(job_id)
        if handle is None:
            return False
        handle.request_stop()
        return True

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            handle = self._jobs.get(job_id)
        return handle is not None and handle.thread is not None and handle.thread.is_alive()

    # -- the worker ----------------------------------------------------------

    def _run(
        self,
        handle: _JobHandle,
        source_name: str,
        keyword: str,
        location: str,
        max_leads: int,
        enrich_mode: str,
    ) -> None:
        job_id, user_id = handle.job_id, handle.user_id

        def progress(message: str, percent: int = -1, stage: str | None = None) -> None:
            self._emit(job_id, user_id, message, percent, stage)

        try:
            db.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, stage = 'discover' WHERE id = ?",
                (_now(), job_id),
            )
            progress("Starting %s search..." % source_name, 1, "discover")

            # --- discovery ---------------------------------------------------
            if source_name == "gmaps":
                # Long runs save as they go, so stopping never loses work and
                # results appear while the search is still going.
                # Track what was last written for each lead, not merely which
                # ones exist. Discovery finds the business; the detail phase
                # then fills in phone and website on the *same* lead, so a
                # seen-once check would silently discard every enrichment.
                written: dict[str, str] = {}

                def on_batch(found: list) -> None:
                    fresh = []
                    for item in found:
                        lead = asdict(item)
                        lead["keyword"] = keyword
                        lead["location"] = location
                        lead["source"] = source_name
                        uid = lead_uid(lead)
                        lead["uid"] = uid
                        normalise_contacts(lead)
                        signature = "%s|%s|%s|%s" % (
                            lead.get("business_name", ""),
                            lead.get("phone", ""),
                            lead.get("website", ""),
                            lead.get("address", ""),
                        )
                        if written.get(uid) == signature:
                            continue
                        written[uid] = signature
                        fresh.append(lead)
                    if fresh:
                        self._persist(job_id, user_id, fresh)
                        db.execute(
                            "UPDATE jobs SET total_found = ? WHERE id = ?",
                            (len(written), job_id),
                        )

                source = GoogleMapsSource(
                    on_progress=lambda m, p: progress(m, p, "discover"),
                    on_batch=on_batch,
                )
                handle.source = source
                raw = source.run(keyword, location, max_leads)
            else:
                source = WebSearchSource(on_progress=lambda m, p: progress(m, p, "discover"))
                handle.source = source
                raw = source.run(keyword, location, max_leads)

            leads = [asdict(item) for item in raw]
            for lead in leads:
                lead["keyword"] = keyword
                lead["location"] = location
                lead["source"] = source_name
                lead["enriched"] = 0

            if handle.stop_event.is_set():
                self._finish(job_id, user_id, "stopped", leads, "Stopped by user.")
                return

            if not leads:
                self._finish(job_id, user_id, "completed", [], "No businesses matched this search.")
                return

            self._persist(job_id, user_id, leads)
            progress("Saved %d leads. " % len(leads), 65, "enrich")

            # --- enrichment ---------------------------------------------------
            if enrich_mode != "off" and not handle.stop_event.is_set():
                finder = EmailFinder(on_progress=lambda m, p: progress(m, p, "enrich"))
                handle.enricher = finder
                try:
                    asyncio.run(finder.enrich(leads))
                    _tag_website_source(leads)
                except Exception as exc:  # noqa: BLE001 - lookup is best-effort
                    log.exception("email lookup failed for job %s", job_id)
                    progress("Email lookup failed: %s" % exc, -1, "enrich")
                else:
                    self._persist(job_id, user_id, leads)

            status = "stopped" if handle.stop_event.is_set() else "completed"
            self._finish(job_id, user_id, status, leads)

        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            log.exception("job %s failed", job_id)
            db.execute(
                "UPDATE jobs SET status = 'failed', error = ?, finished_at = ?, "
                "stage = 'failed', message = ? WHERE id = ?",
                (str(exc), _now(), "Failed: %s" % exc, job_id),
            )
            self._publish(
                job_id,
                {"type": "done", "job_id": job_id, "status": "failed", "error": str(exc)},
            )
        finally:
            with self._lock:
                self._jobs.pop(job_id, None)

    def _persist(self, job_id: str, user_id: int, leads: list[dict]) -> None:
        """Upsert the current lead set. Safe to call repeatedly."""
        rows = []
        for lead in leads:
            normalise_contacts(lead)
            uid = lead.get("uid") or lead_uid(lead)
            lead["uid"] = uid
            rows.append(
                (
                    job_id, user_id, uid, lead.get("source", ""),
                    lead.get("business_name", ""), lead.get("owner_name", ""),
                    lead.get("email", ""), lead.get("phone", ""),
                    json.dumps(lead.get("emails") or []),
                    json.dumps(lead.get("phones") or []),
                    lead.get("email_source", ""), lead.get("email_status", ""),
                    lead.get("website", ""), lead.get("address", ""),
                    lead.get("rating", ""), lead.get("reviews", ""),
                    lead.get("category", ""), lead.get("latitude", ""),
                    lead.get("longitude", ""), lead.get("place_id", ""),
                    lead.get("facebook", ""), lead.get("instagram", ""),
                    lead.get("twitter", ""), lead.get("linkedin", ""),
                    lead.get("youtube", ""), lead.get("summary", ""),
                    score_lead(lead), int(lead.get("enriched") or 0),
                    lead.get("keyword", ""), lead.get("location", ""),
                    json.dumps(lead.get("extra") or {}),
                )
            )

        with db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO leads (
                    job_id, user_id, uid, source, business_name, owner_name, email, phone,
                    emails, phones, email_source, email_status,
                    website, address, rating, reviews, category,
                    latitude, longitude, place_id, facebook, instagram, twitter,
                    linkedin, youtube, summary, quality, enriched, keyword, location, extra
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id, uid) DO UPDATE SET
                    business_name = excluded.business_name,
                    owner_name    = COALESCE(NULLIF(excluded.owner_name, ''), leads.owner_name),
                    email         = COALESCE(NULLIF(excluded.email, ''), leads.email),
                    phone         = COALESCE(NULLIF(excluded.phone, ''), leads.phone),
                    emails        = CASE WHEN excluded.emails IN ('[]','') THEN leads.emails ELSE excluded.emails END,
                    phones        = CASE WHEN excluded.phones IN ('[]','') THEN leads.phones ELSE excluded.phones END,
                    email_source  = COALESCE(NULLIF(excluded.email_source, ''), leads.email_source),
                    email_status  = COALESCE(NULLIF(excluded.email_status, ''), leads.email_status),
                    website       = COALESCE(NULLIF(excluded.website, ''), leads.website),
                    address       = COALESCE(NULLIF(excluded.address, ''), leads.address),
                    facebook      = COALESCE(NULLIF(excluded.facebook, ''), leads.facebook),
                    instagram     = COALESCE(NULLIF(excluded.instagram, ''), leads.instagram),
                    twitter       = COALESCE(NULLIF(excluded.twitter, ''), leads.twitter),
                    linkedin      = COALESCE(NULLIF(excluded.linkedin, ''), leads.linkedin),
                    youtube       = COALESCE(NULLIF(excluded.youtube, ''), leads.youtube),
                    summary       = COALESCE(NULLIF(excluded.summary, ''), leads.summary),
                    quality       = excluded.quality,
                    enriched      = excluded.enriched,
                    updated_at    = datetime('now')
                """,
                rows,
            )

    def _finish(
        self,
        job_id: str,
        user_id: int,
        status: str,
        leads: list[dict],
        message: str | None = None,
    ) -> None:
        enriched = sum(1 for lead in leads if lead.get("enriched"))
        summary = message or "Finished with %d leads (%d enriched)." % (len(leads), enriched)
        db.execute(
            "UPDATE jobs SET status = ?, stage = 'done', progress = 100, message = ?, "
            "total_found = ?, enriched_count = ?, finished_at = ? WHERE id = ?",
            (status, summary, len(leads), enriched, _now(), job_id),
        )
        db.execute(
            "INSERT INTO job_events (job_id, user_id, level, stage, progress, message) "
            "VALUES (?, ?, 'info', 'done', 100, ?)",
            (job_id, user_id, summary),
        )
        self._publish(
            job_id,
            {
                "type": "done",
                "job_id": job_id,
                "status": status,
                "message": summary,
                "total_found": len(leads),
                "enriched_count": enriched,
            },
        )

    def recover_orphans(self) -> int:
        """Mark jobs left running by a previous process as failed."""
        cur = db.execute(
            "UPDATE jobs SET status = 'failed', stage = 'failed', "
            "error = 'Interrupted by a server restart', "
            "message = 'Interrupted by a server restart', finished_at = ? "
            "WHERE status IN ('queued','running')",
            (_now(),),
        )
        return cur.rowcount or 0


manager = JobManager()
