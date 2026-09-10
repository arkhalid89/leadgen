"""Central configuration. Every tunable is an env var with a sane default."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# --- Core -------------------------------------------------------------------
SECRET_KEY: str = os.environ.get("LEADGEN_SECRET_KEY", "dev-insecure-change-me")
DB_PATH: str = os.environ.get("LEADGEN_DB_PATH", str(BASE_DIR / "leadgen.db"))
OUTPUT_DIR: str = os.environ.get("LEADGEN_OUTPUT_DIR", str(BASE_DIR / "output"))
IS_PRODUCTION: bool = os.environ.get("LEADGEN_ENV", "development").lower() == "production"
ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("LEADGEN_ALLOWED_ORIGINS", "http://localhost:3600,http://localhost:3000").split(",") if o.strip()
]
SESSION_COOKIE: str = "leadgen_session"
SESSION_MAX_AGE: int = _env_int("LEADGEN_SESSION_MAX_AGE", 60 * 60 * 24 * 14)

# --- Google Maps scraping ---------------------------------------------------
# Concurrent tabs reading listing detail pages. Under Playwright these are tabs
# in one browser rather than separate Chrome processes, so this can be higher
# than it could with Selenium for the same memory.
GMAPS_DETAIL_WORKERS: int = _env_int("LEADGEN_GMAPS_DETAIL_WORKERS", 8)
GMAPS_HEADLESS: bool = _env_bool("LEADGEN_GMAPS_HEADLESS", True)
GMAPS_SCROLL_ROUNDS: int = _env_int("LEADGEN_GMAPS_SCROLL_ROUNDS", 40)
# One Google Maps search returns roughly 100-120 results however far you
# scroll. To go past that the map has to be searched in tiles, so a wider
# area is covered by many viewport searches instead of one.
GMAPS_TILING: bool = _env_bool("LEADGEN_GMAPS_TILING", True)
# Above this target, tiling kicks in.
GMAPS_SINGLE_SEARCH_YIELD: int = _env_int("LEADGEN_GMAPS_SINGLE_SEARCH_YIELD", 90)
GMAPS_MAX_CELLS: int = _env_int("LEADGEN_GMAPS_MAX_CELLS", 25)
# Expected new businesses per tile; drives how fine the grid is.
GMAPS_LEADS_PER_CELL: int = _env_int("LEADGEN_GMAPS_LEADS_PER_CELL", 30)
# A cell returning at least this many results is assumed to be hiding more
# behind Maps' per-search cap, so it is split into four and searched again.
# This is what makes an unlimited run possible.
GMAPS_SATURATION: int = _env_int("LEADGEN_GMAPS_SATURATION", 45)
GMAPS_MAX_DEPTH: int = _env_int("LEADGEN_GMAPS_MAX_DEPTH", 3)
# Hard ceiling on searches for one job, so an unlimited run still ends.
GMAPS_MAX_SEARCHES: int = _env_int("LEADGEN_GMAPS_MAX_SEARCHES", 400)
# Detail results are written back every this many businesses, so stopping
# mid-phase keeps the phone numbers already gathered.
GMAPS_DETAIL_BATCH: int = _env_int("LEADGEN_GMAPS_DETAIL_BATCH", 20)
GMAPS_CELL_ZOOM: int = _env_int("LEADGEN_GMAPS_CELL_ZOOM", 15)
GMAPS_CELL_SCROLLS: int = _env_int("LEADGEN_GMAPS_CELL_SCROLLS", 12)
# Nominatim asks for a real identifying User-Agent and no more than 1 req/s.
GEOCODE_USER_AGENT: str = os.environ.get(
    "LEADGEN_GEOCODE_UA", "LeadGen/3.0 (business lead research)"
)
GMAPS_PAGE_TIMEOUT: float = _env_float("LEADGEN_GMAPS_PAGE_TIMEOUT", 20.0)
# Optional: point Playwright at a specific Chromium build. Normally unset -
# Playwright downloads and manages its own, matched to the driver version.
CHROME_BINARY: str = os.environ.get("CHROME_BIN", "")

# --- Web search discovery ---------------------------------------------------
SEARCH_MAX_QUERIES: int = _env_int("LEADGEN_SEARCH_MAX_QUERIES", 8)
SEARCH_RESULTS_PER_QUERY: int = _env_int("LEADGEN_SEARCH_RESULTS_PER_QUERY", 40)
SERPER_API_KEY: str = os.environ.get("SERPER_API_KEY", "").strip()

# --- Email discovery (website scraping, no API key needed) ------------------
# Emails are read from the business's own site. A model with no live web access
# can only invent them, so no model is involved in contact data.
EMAIL_CONCURRENCY: int = _env_int("LEADGEN_EMAIL_CONCURRENCY", 25)
EMAIL_TIMEOUT: float = _env_float("LEADGEN_EMAIL_TIMEOUT", 8.0)
# Slow or overloaded hosting is common; one longer retry recovers a good share
# of what a single short attempt reports as dead.
EMAIL_SLOW_RETRY_TIMEOUT: float = _env_float("LEADGEN_EMAIL_SLOW_RETRY_TIMEOUT", 20.0)
# Pause before the second pass over a site whose DNS or connection failed.
EMAIL_RETRY_PAUSE: float = _env_float("LEADGEN_EMAIL_RETRY_PAUSE", 2.0)
# When a site yields nothing, ask a search engine for the business by name.
# It often knows the contact page we could not guess, and sometimes shows the
# address in the result snippet.
EMAIL_SEARCH_FALLBACK: bool = _env_bool("LEADGEN_EMAIL_SEARCH_FALLBACK", True)
# Searches are rate-limited by the engine, so this is deliberately modest.
EMAIL_SEARCH_CONCURRENCY: int = _env_int("LEADGEN_EMAIL_SEARCH_CONCURRENCY", 3)
EMAIL_SEARCH_MAX_LOOKUPS: int = _env_int("LEADGEN_EMAIL_SEARCH_MAX_LOOKUPS", 150)
EMAIL_SEARCH_DELAY: float = _env_float("LEADGEN_EMAIL_SEARCH_DELAY", 1.2)
# Homepage plus this many follow-up pages (contact, about, impressum...).
EMAIL_MAX_PAGES: int = _env_int("LEADGEN_EMAIL_MAX_PAGES", 4)
EMAIL_USE_CACHE: bool = _env_bool("LEADGEN_EMAIL_USE_CACHE", True)

# --- Gemini (outreach copy only) --------------------------------------------
# Used only to draft outreach emails, which is generative and has no ground
# truth to get wrong. It is never used for contact data.
GEMINI_API_KEY: str = (
    os.environ.get("GEMINI_API_KEY", "").strip()
    or os.environ.get("GOOGLE_GEMINI_API_KEY", "").strip()
)
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_TIMEOUT: float = _env_float("LEADGEN_GEMINI_TIMEOUT", 90.0)
# Thinking tokens count towards the output budget on 3.x models and run ~4x
# the visible answer, so this has to be generous or replies get truncated.
GEMINI_MAX_OUTPUT_TOKENS: int = _env_int("LEADGEN_GEMINI_MAX_OUTPUT_TOKENS", 8192)

# --- AI email suggestions (opt-in, measured 30% precision) -------------------
# Businesses per request. Larger batches spend quota per batch, not per lead.
AI_EMAIL_BATCH_SIZE: int = _env_int("LEADGEN_AI_EMAIL_BATCH_SIZE", 20)
AI_EMAIL_CONCURRENCY: int = _env_int("LEADGEN_AI_EMAIL_CONCURRENCY", 3)
AI_EMAIL_MAX_RETRIES: int = _env_int("LEADGEN_AI_EMAIL_MAX_RETRIES", 2)

# --- SMTP verification ------------------------------------------------------
# Asks each domain's mail server whether a mailbox exists, without sending mail.
# This is what turns a pattern guess into a fact. It needs outbound port 25,
# which residential ISPs and AWS/GCP block by default, so it is opt-in.
SMTP_VERIFY_ENABLED: bool = _env_bool("LEADGEN_SMTP_VERIFY", False)
SMTP_PORT: int = _env_int("LEADGEN_SMTP_PORT", 25)
# Identify honestly. Use a domain you actually control: mail servers check it,
# and a bogus HELO is a fast route to being blocked.
SMTP_HELO_HOST: str = os.environ.get("LEADGEN_SMTP_HELO_HOST", "localhost")
SMTP_MAIL_FROM: str = os.environ.get("LEADGEN_SMTP_MAIL_FROM", "verify@localhost")
SMTP_TIMEOUT: float = _env_float("LEADGEN_SMTP_TIMEOUT", 10.0)
SMTP_DNS_TIMEOUT: float = _env_float("LEADGEN_SMTP_DNS_TIMEOUT", 6.0)
# Domains probed in parallel. Keep low: one connection per domain is polite,
# hammering is how an IP gets blacklisted.
SMTP_CONCURRENCY: int = _env_int("LEADGEN_SMTP_CONCURRENCY", 4)
SMTP_PROBE_DELAY: float = _env_float("LEADGEN_SMTP_PROBE_DELAY", 0.4)
# Enough room for the top role mailboxes plus owner-derived guesses.
SMTP_MAX_CANDIDATES: int = _env_int("LEADGEN_SMTP_MAX_CANDIDATES", 12)
SMTP_MX_ATTEMPTS: int = _env_int("LEADGEN_SMTP_MX_ATTEMPTS", 2)
# Host used once to test whether outbound 25 works at all.
SMTP_PROBE_HOST: str = os.environ.get("LEADGEN_SMTP_PROBE_HOST", "gmail-smtp-in.l.google.com")
# Comma-separated role mailboxes to try. Empty uses the built-in list.
SMTP_PATTERNS: list[str] = [
    p.strip().lower()
    for p in os.environ.get("LEADGEN_SMTP_PATTERNS", "").split(",")
    if p.strip()
]

# --- Job runner -------------------------------------------------------------
MAX_ACTIVE_JOBS_PER_USER: int = _env_int("LEADGEN_MAX_ACTIVE_JOBS_PER_USER", 3)
JOB_HISTORY_LIMIT: int = _env_int("LEADGEN_JOB_HISTORY_LIMIT", 200)

os.makedirs(OUTPUT_DIR, exist_ok=True)
