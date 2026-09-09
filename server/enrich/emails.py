"""Email discovery by reading the business's own website.

This replaces model-based enrichment for contact data. The reasoning is simple:
finding a business's email is a lookup, not a reasoning task, and a language
model with no live web access can only produce something email-shaped. Measured
against scraped ground truth, an ungrounded model invented both addresses and
phone numbers with full confidence. The website is the ground truth, and
fetching it costs nothing.

This is *not* the slow website crawling the previous version did. That drove a
real browser through up to eight pages per site, sequentially. This is plain
async HTTP, a handful of pages per domain, dozens of domains in flight at once -
roughly 0.4s per domain wall-clock.

Work is avoided before any request is made: leads that already have an email are
skipped, leads with no website are skipped, and answers are cached by registered
domain so a site is fetched once no matter how often it reappears.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Callable
from urllib.parse import unquote, urljoin

import httpx

from .. import db, settings

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int], None]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r'mailto:([^"\'>?&\s]+)', re.I)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
# Cloudflare replaces addresses with a hex blob; decoding it is a real win
# because the protection is extremely common on small-business sites.
CF_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')

# Addresses that belong to tooling, themes or documentation rather than to the
# business. The previous website crawler filled the database with these.
JUNK_DOMAIN_RE = re.compile(
    r"(sentry\.|sentry-next|wixpress|example\.(com|org|net)|yourdomain|yoursite|"
    r"yourcompany|godaddy|\.wp\.com|schema\.org|w3\.org|googleapis|gstatic|"
    r"cloudflare|jquery|bootstrapcdn|placeholder|lorem|doe\.com|domain\.com|"
    r"website\.com|company\.com|mysite|email\.com|test\.com|"
    r"mailtrap|spamtrap|honeypot|trapmail|guerrillamail|mailinator|10minutemail)",
    re.I,
)
JUNK_MAILBOX_RE = re.compile(
    r"^(john|jane|jon)?doe$|^(your|my)?(name|email|mail)$|^sample$|^demo$|^test$|"
    r"^example$|^user(name)?$|^firstname|^lastname|^abc$|^xyz$|^\d+$|^no-?reply$|"
    r"^donot-?reply$|^postmaster$|^abuse$|^webmaster$",
    re.I,
)
ASSET_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|woff2?|ttf)$", re.I)

# Anchor text/paths that tend to lead to a page carrying contact details.
CONTACT_HINTS = (
    "contact", "kontakt", "contacto", "contatt", "contato", "about",
    "impressum", "reach", "connect", "support", "team", "enquir", "inquir",
    "legal", "privacy", "help", "book", "appointment", "chi-siamo",
    "dove-siamo", "prenota", "quienes", "sobre-nos", "nous-contacter",
    "a-propos", "over-ons", "info",
)
# Tried directly when the homepage offers no useful links.
WELL_KNOWN_PATHS = (
    # English
    "/contact", "/contact-us", "/contactus", "/about", "/about-us",
    # Italian
    "/contatti", "/contatto", "/chi-siamo", "/dove-siamo", "/prenota",
    # Spanish / Portuguese
    "/contacto", "/contactanos", "/quienes-somos", "/sobre-nosotros", "/contato",
    # French
    "/nous-contacter", "/a-propos",
    # German / Dutch
    "/impressum", "/kontakt", "/over-ons",
    # Common everywhere
    "/team", "/support", "/privacy-policy", "/legal", "/info",
)

SOCIAL_PATTERNS = {
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9.\-_]{3,}", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9.\-_]{3,}", re.I),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9.\-_]{3,}", re.I),
    "linkedin": re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9.\-_%]{2,}", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(?:@|c/|channel/|user/)[A-Za-z0-9.\-_]{2,}", re.I),
}
SOCIAL_JUNK = re.compile(r"(sharer|share\.php|/plugins/|intent/|profile\.php$|/home$|dialog/)", re.I)

# A fuller header set matters: a bare User-Agent gets a 403 from Cloudflare and
# similar front ends far more often than a complete browser fingerprint.
#
# Accept-Encoding is deliberately absent: httpx sets it from the codecs actually
# installed. Hard-coding "br" made servers return Brotli that httpx could not
# decode, so every page arrived as binary noise and no email was ever matched.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="144", "Not(A:Brand";v="24", "Google Chrome";v="144"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Cache-Control": "max-age=0",
}

MAX_HTML_BYTES = 400_000

# Anti-bot interstitials. They are not the business's page, and some seed
# honeypot addresses specifically to catch scrapers that harvest them.
CHALLENGE_MARKERS = (
    "anti-robot validation",
    "checking your browser",
    "verify you are human",
    "verifying you are human",
    "enable javascript and cookies to continue",
    "ddos protection by",
    "attention required! | cloudflare",
    "just a moment...",
    "please turn javascript on",
    "request unsuccessful. incapsula",
)

# Sites where Google Maps lists a social profile as the "website". Crawling
# these never yields a business address, so they are recorded as the social
# link they are and skipped for email discovery.
SOCIAL_HOSTS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "tiktok.com", "youtube.com", "pinterest.com", "linktr.ee", "wa.me",
    "business.site", "sites.google.com",
)


def is_challenge_page(html: str) -> bool:
    """True when the response is a bot check rather than real content."""
    head = (html or "")[:6000].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


def social_host(url: str) -> str:
    """The social network a URL belongs to, or '' when it is a real site."""
    host = registered_domain(url)
    for known in SOCIAL_HOSTS:
        if host == known or host.endswith("." + known):
            return known
    return ""


def registered_domain(url: str) -> str:
    """Best-effort eTLD+1, used as the cache key."""
    value = (url or "").strip().lower()
    if not value:
        return ""
    value = re.sub(r"^https?://", "", value).split("/")[0].split("?")[0].split(":")[0]
    if value.startswith("www."):
        value = value[4:]
    parts = [p for p in value.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if parts[-2] in {"co", "com", "org", "net", "gov", "ac", "edu"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _decode_cloudflare(hex_blob: str) -> str:
    """Cloudflare XORs the address with its first byte."""
    try:
        data = bytes.fromhex(hex_blob)
        key = data[0]
        return "".join(chr(b ^ key) for b in data[1:])
    except (ValueError, IndexError):
        return ""


def harvest_emails(html: str) -> list[str]:
    """Pull every candidate address out of a page."""
    decoded = unquote(html)
    found = list(MAILTO_RE.findall(decoded))
    found += [_decode_cloudflare(blob) for blob in CF_RE.findall(html)]
    found += EMAIL_RE.findall(decoded)
    return found


def harvest_socials(html: str) -> dict[str, str]:
    """Social profile links, which sites list in their footer for free."""
    out: dict[str, str] = {}
    for name, pattern in SOCIAL_PATTERNS.items():
        for match in pattern.findall(html) or []:
            url = match if isinstance(match, str) else ""
            if url and not SOCIAL_JUNK.search(url):
                out[name] = url.rstrip("/")
                break
    return out


def clean_emails(candidates: list[str], domain: str = "") -> list[str]:
    """Validate, de-duplicate and rank. Own-domain addresses come first."""
    base = domain.split(".")[0].lower() if domain else ""
    out: list[str] = []
    for raw in candidates:
        value = unquote(raw or "").strip().strip(".,;:<>()[]'\"")
        value = re.sub(r"^(mailto:|%20|\s)+", "", value, flags=re.I).lower()
        # Pages often render a phone immediately before an address with no
        # separator, so the match swallows it: "+447859004628babag@example.com".
        # Trim a leading "+" and any long run of digits in front of the mailbox.
        value = re.sub(r"^\+", "", value)
        value = re.sub(r"^\d{7,}(?=[a-z])", "", value)
        if not value or "@" not in value or len(value) > 80:
            continue
        if not EMAIL_RE.fullmatch(value):
            continue
        if ASSET_RE.search(value) or JUNK_DOMAIN_RE.search(value):
            continue
        if re.match(r"^[0-9a-f]{16,}@", value):
            continue
        if JUNK_MAILBOX_RE.match(value.split("@")[0]):
            continue
        if value not in out:
            out.append(value)

    def rank(email: str) -> tuple[int, int, int]:
        mailbox, _, host = email.partition("@")
        own = 0 if base and base in host else 1
        # A role mailbox is the one a business actually watches.
        role = 0 if mailbox in {
            "info", "hello", "contact", "enquiries", "inquiries", "office",
            "admin", "sales", "reception", "bookings", "mail",
        } else 1
        return (own, role, len(email))

    return sorted(out, key=rank)


# Query shapes used when a site gives nothing up. The engine frequently knows
# the business's contact page even when its homepage is JavaScript-rendered.
SEARCH_QUERY_TEMPLATES = (
    '"{name}" contact email',
    '"{name}" contatti email',
)

_NAME_STOPWORDS = {
    "the", "and", "restaurant", "ristorante", "cafe", "caffe", "bar", "shop",
    "store", "srl", "spa", "ltd", "llc", "inc", "gmbh", "clinic", "studio",
    "milano", "milan", "group", "co", "company", "trattoria", "osteria",
    "pizzeria", "hotel", "dental", "dentist",
}


def name_tokens(business_name: str) -> list[str]:
    """Distinctive words from a business name, for matching against a mailbox."""
    words = re.split(r"[^a-z0-9]+", (business_name or "").lower())
    return [w for w in words if len(w) >= 4 and w not in _NAME_STOPWORDS]


def snippet_email_is_plausible(address: str, domain: str, business_name: str) -> bool:
    """Decide whether an address found in search results belongs to this business.

    Search snippets mix results from many sites, so harvesting them blindly
    attaches other companies' addresses to a lead - measured, a search for one
    restaurant returned three addresses belonging to unrelated businesses. Two
    things make an address trustworthy: it sits on the business's own domain, or
    its mailbox carries a distinctive word from the business name (a chain using
    a group domain, e.g. ilcairoli@unacucina.it).
    """
    mailbox, _, host = address.partition("@")
    base = domain.split(".")[0].lower() if domain else ""
    if base and base in host:
        return True
    return any(token in mailbox.lower() for token in name_tokens(business_name))


class EmailFinder:
    """Finds published email addresses by fetching business websites."""

    def __init__(self, on_progress: ProgressFn | None = None):
        self._on_progress = on_progress
        self._cancelled = False
        self.stats = {
            "considered": 0, "skipped": 0, "from_cache": 0, "fetched": 0,
            "found": 0, "challenged": 0, "social_only": 0,
            "searched": 0, "from_search": 0, "search_pages": 0,
        }
        self._search_budget = settings.EMAIL_SEARCH_MAX_LOOKUPS
        self._search_semaphore = asyncio.Semaphore(
            max(1, settings.EMAIL_SEARCH_CONCURRENCY)
        )

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def configured(self) -> bool:
        """Always available - there is no key or quota to satisfy."""
        return True

    def _progress(self, message: str, percent: int = -1) -> None:
        log.info("[emails] %s", message)
        if self._on_progress:
            try:
                self._on_progress(message, percent)
            except Exception:  # noqa: BLE001
                log.debug("progress callback failed", exc_info=True)

    # -- selection -----------------------------------------------------------

    def select(self, leads: list[dict]) -> list[dict]:
        wanted = []
        for lead in leads:
            self.stats["considered"] += 1
            has_email = bool(lead.get("emails")) or bool((lead.get("email") or "").strip())
            if has_email or not (lead.get("website") or "").strip():
                self.stats["skipped"] += 1
                continue
            wanted.append(lead)
        return wanted

    # -- cache ---------------------------------------------------------------

    def _load_cache(self, domains: set[str]) -> dict[str, dict]:
        if not settings.EMAIL_USE_CACHE:
            return {}
        wanted = [d for d in domains if d]
        found: dict[str, dict] = {}
        for i in range(0, len(wanted), 400):
            chunk = wanted[i : i + 400]
            for row in db.query(
                "SELECT * FROM contact_cache WHERE domain IN (%s)" % ",".join("?" * len(chunk)),
                tuple(chunk),
            ):
                found[row["domain"]] = db.row_to_dict(row)  # type: ignore[assignment]
        return found

    def _save_cache(self, domain: str, emails: list[str], socials: dict[str, str]) -> None:
        if not settings.EMAIL_USE_CACHE or not domain:
            return
        try:
            db.execute(
                "INSERT INTO contact_cache (domain, emails, facebook, instagram, twitter, "
                "linkedin, youtube, grounded) VALUES (?,?,?,?,?,?,?,1) "
                "ON CONFLICT(domain) DO UPDATE SET emails = excluded.emails, "
                "facebook = COALESCE(NULLIF(excluded.facebook, ''), contact_cache.facebook), "
                "instagram = COALESCE(NULLIF(excluded.instagram, ''), contact_cache.instagram), "
                "linkedin = COALESCE(NULLIF(excluded.linkedin, ''), contact_cache.linkedin)",
                (
                    domain, json.dumps(emails), socials.get("facebook", ""),
                    socials.get("instagram", ""), socials.get("twitter", ""),
                    socials.get("linkedin", ""), socials.get("youtube", ""),
                ),
            )
        except Exception:  # noqa: BLE001 - caching must never break a job
            log.debug("could not cache %s", domain, exc_info=True)

    # -- fetching ------------------------------------------------------------

    @staticmethod
    def _origin_variants(website: str) -> list[str]:
        """Every sensible way to reach a site, best first.

        Plenty of small-business domains resolve only with the "www" prefix, or
        only over http. Trying the variants recovers sites that a single attempt
        reports as dead.
        """
        raw = (website or "").strip()
        if not raw:
            return []
        raw = re.sub(r"^https?://", "", raw)
        host = raw.split("/")[0]
        path = raw[len(host):] or "/"
        bare = host[4:] if host.startswith("www.") else host
        variants = [
            "https://%s%s" % (host, path),
            "https://www.%s%s" % (bare, path),
            "https://%s%s" % (bare, path),
            "http://%s%s" % (host, path),
            "http://www.%s%s" % (bare, path),
        ]
        seen, out = set(), []
        for item in variants:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    async def _get(
        self, client: httpx.AsyncClient, url: str, timeout: float | None = None
    ) -> tuple[str, str]:
        """Fetch one exact URL. Returns (html, final_url); empty html on failure."""
        try:
            response = await client.get(
                url,
                headers=BROWSER_HEADERS,
                follow_redirects=True,
                timeout=timeout or settings.EMAIL_TIMEOUT,
            )
        except (httpx.HTTPError, UnicodeDecodeError, ValueError):
            return "", url
        # Accept any 2xx: some front ends answer 202/203 for a perfectly good page.
        if not (200 <= response.status_code < 300):
            return "", str(response.url)
        content_type = response.headers.get("content-type", "")
        if content_type and "html" not in content_type.lower():
            return "", str(response.url)
        return response.text[:MAX_HTML_BYTES], str(response.url)

    async def _get_site(self, client: httpx.AsyncClient, website: str) -> tuple[str, str]:
        """Fetch a site's homepage, trying host variants and retrying failures.

        DNS lookups fail intermittently under load - the same domain that
        reports "getaddrinfo failed" mid-run resolves perfectly a moment later.
        A second pass after a short pause recovers most of those.
        """
        variants = self._origin_variants(website)
        for attempt in range(2):
            for index, candidate in enumerate(variants):
                if self._cancelled:
                    return "", website
                html, final = await self._get(client, candidate)
                self.stats["fetched"] += 1
                if html and not is_challenge_page(html):
                    return html, final
                if html:
                    # A bot check: the real page is not behind this one for us.
                    self.stats["challenged"] = self.stats.get("challenged", 0) + 1
                    return "", final
                # One slow-host retry on the primary variant before moving on.
                if (
                    attempt == 0
                    and index == 0
                    and settings.EMAIL_SLOW_RETRY_TIMEOUT > settings.EMAIL_TIMEOUT
                ):
                    html, final = await self._get(
                        client, candidate, timeout=settings.EMAIL_SLOW_RETRY_TIMEOUT
                    )
                    self.stats["fetched"] += 1
                    if html and not is_challenge_page(html):
                        return html, final
            if attempt == 0:
                await asyncio.sleep(settings.EMAIL_RETRY_PAUSE)
        return "", website

    def _candidate_pages(self, html: str, base_url: str, domain: str) -> list[str]:
        """Pages most likely to carry contact details, best first."""
        candidates: list[str] = []
        seen: set[str] = set()

        for href in HREF_RE.findall(html):
            low = href.lower()
            if low.startswith(("mailto:", "tel:", "#", "javascript:")):
                continue
            if not any(hint in low for hint in CONTACT_HINTS):
                continue
            try:
                full = urljoin(base_url, href).split("#")[0]
            except ValueError:
                continue
            if registered_domain(full) != domain or full in seen:
                continue
            seen.add(full)
            candidates.append(full)

        for path in WELL_KNOWN_PATHS:
            full = urljoin(base_url, path)
            if full not in seen:
                seen.add(full)
                candidates.append(full)

        return candidates

    async def _for_lead(self, client: httpx.AsyncClient, lead: dict) -> tuple[list[str], dict]:
        website = (lead.get("website") or "").strip()

        network = social_host(website)
        if network:
            # Maps often lists a Facebook or Instagram page as the website.
            # Keep it as the social link; there is no site here to read.
            self.stats["social_only"] = self.stats.get("social_only", 0) + 1
            key = {"x.com": "twitter", "wa.me": "", "linktr.ee": "",
                   "business.site": "", "sites.google.com": ""}.get(
                network, network.split(".")[0]
            )
            return [], ({key: website} if key else {})

        domain = registered_domain(website)
        html, final_url = await self._get_site(client, website)
        if not html:
            # Site unreachable or behind a bot check - the search engine may
            # still know its contact page.
            if settings.EMAIL_SEARCH_FALLBACK:
                found, socials = await self._search_fallback(client, lead, domain)
                if found:
                    lead["email_source"] = "search"
                return found, socials
            return [], {}

        socials = harvest_socials(html)
        raw_found = harvest_emails(html)

        def has_own_domain(values: list[str]) -> bool:
            """An address on the business's own domain is the one we want."""
            base = domain.split(".")[0].lower()
            return any(base and base in e.split("@")[-1] for e in values)

        emails = clean_emails(raw_found, domain)
        if emails and has_own_domain(emails):
            return emails, socials

        # Keep looking. Stopping at the first page with *any* address picks up
        # the web designer's or theme vendor's mailbox instead of the business's.
        budget = max(0, settings.EMAIL_MAX_PAGES - 1)
        for page in self._candidate_pages(html, final_url, domain)[:budget]:
            if self._cancelled:
                break
            page_html, _ = await self._get(client, page)
            self.stats["fetched"] += 1
            if not page_html:
                continue
            for key, value in harvest_socials(page_html).items():
                socials.setdefault(key, value)
            raw_found += harvest_emails(page_html)
            emails = clean_emails(raw_found, domain)
            if emails and has_own_domain(emails):
                break

        emails = clean_emails(raw_found, domain)
        if not emails and settings.EMAIL_SEARCH_FALLBACK:
            found, extra_socials = await self._search_fallback(client, lead, domain)
            for key, value in extra_socials.items():
                socials.setdefault(key, value)
            if found:
                lead["email_source"] = "search"
                return found, socials
        return emails, socials

    async def _search_fallback(
        self, client: httpx.AsyncClient, lead: dict, domain: str
    ) -> tuple[list[str], dict]:
        """Ask a search engine about a business whose own site gave nothing.

        Two things are worth having from the results: contact-page URLs on the
        business's own domain (which are then fetched normally), and addresses
        in the snippets themselves - but only those that plausibly belong to
        this business.
        """
        name = (lead.get("business_name") or "").strip()
        if not name or self._cancelled:
            return [], {}
        if self._search_budget <= 0:
            return [], {}
        self._search_budget -= 1

        try:
            from ddgs import DDGS  # noqa: PLC0415
        except ImportError:
            return [], {}

        results = []
        async with self._search_semaphore:
            for template in SEARCH_QUERY_TEMPLATES:
                query = template.format(name=name)
                try:
                    # ddgs is synchronous; keep it off the event loop.
                    results = await asyncio.to_thread(
                        lambda q=query: DDGS().text(q, max_results=8)
                    )
                except Exception as exc:  # noqa: BLE001 - assorted network errors
                    log.debug("search fallback failed for %s: %s", name, exc)
                    results = []
                await asyncio.sleep(settings.EMAIL_SEARCH_DELAY)
                if results:
                    break
        if not results:
            return [], {}

        self.stats["searched"] = self.stats.get("searched", 0) + 1

        blob = " ".join(
            (item.get("title") or "") + " " + (item.get("body") or "") for item in results
        )
        snippet_emails = [
            address
            for address in clean_emails(harvest_emails(blob), domain)
            if snippet_email_is_plausible(address, domain, name)
        ]

        # Contact pages on the business's own domain that the engine knows about.
        pages: list[str] = []
        for item in results:
            href = (item.get("href") or "").strip()
            if not href or registered_domain(href) != domain:
                continue
            if any(hint in href.lower() for hint in CONTACT_HINTS) and href not in pages:
                pages.append(href)

        socials: dict[str, str] = {}
        page_emails: list[str] = []
        for page in pages[:2]:
            if self._cancelled:
                break
            html, _ = await self._get(client, page)
            self.stats["fetched"] += 1
            if not html or is_challenge_page(html):
                continue
            self.stats["search_pages"] = self.stats.get("search_pages", 0) + 1
            for key, value in harvest_socials(html).items():
                socials.setdefault(key, value)
            page_emails += harvest_emails(html)

        found = clean_emails(page_emails, domain) or snippet_emails
        if found:
            self.stats["from_search"] = self.stats.get("from_search", 0) + 1
        return found, socials

    # -- entry point ---------------------------------------------------------

    @staticmethod
    def _apply(lead: dict, emails: list[str], socials: dict[str, str]) -> bool:
        changed = False
        if emails:
            lead["emails"] = emails
            lead["email"] = emails[0]
            changed = True
        for key, value in socials.items():
            if value and not (lead.get(key) or "").strip():
                lead[key] = value
                changed = True
        return changed

    async def enrich(self, leads: list[dict]) -> int:
        """Fill in emails for ``leads`` in place. Returns the number changed."""
        if not leads:
            return 0

        pending = self.select(leads)
        if not pending:
            self._progress(
                "No leads need an email lookup - %d already had one or had no website."
                % self.stats["skipped"],
                95,
            )
            return 0

        cached = self._load_cache({registered_domain(l.get("website", "")) for l in pending})
        remaining: list[dict] = []
        changed_total = 0
        for lead in pending:
            hit = cached.get(registered_domain(lead.get("website", "")))
            if hit and hit.get("emails"):
                socials = {k: hit.get(k) or "" for k in SOCIAL_PATTERNS}
                if self._apply(lead, list(hit["emails"]), socials):
                    lead["enriched"] = 1
                    changed_total += 1
                self.stats["from_cache"] += 1
            else:
                remaining.append(lead)

        if not remaining:
            self.stats["found"] = changed_total
            self._progress(
                "All %d lookups served from cache - no requests made."
                % self.stats["from_cache"],
                95,
            )
            return changed_total

        self._progress(
            "Reading %d business websites for contact details (%d at a time); "
            "%d skipped, %d from cache."
            % (len(remaining), settings.EMAIL_CONCURRENCY, self.stats["skipped"],
               self.stats["from_cache"]),
            68,
        )

        semaphore = asyncio.Semaphore(max(1, settings.EMAIL_CONCURRENCY))
        completed = 0
        lock = asyncio.Lock()
        limits = httpx.Limits(
            max_connections=max(1, settings.EMAIL_CONCURRENCY + 5),
            max_keepalive_connections=max(1, settings.EMAIL_CONCURRENCY),
        )

        async with httpx.AsyncClient(
            timeout=settings.EMAIL_TIMEOUT, limits=limits, verify=False, headers=BROWSER_HEADERS
        ) as client:

            async def process(lead: dict) -> None:
                nonlocal changed_total, completed
                async with semaphore:
                    if self._cancelled:
                        return
                    try:
                        emails, socials = await self._for_lead(client, lead)
                    except Exception:  # noqa: BLE001 - one bad site must not stop the run
                        log.debug("site read failed for %s", lead.get("website"), exc_info=True)
                        emails, socials = [], {}

                if self._apply(lead, emails, socials):
                    lead["enriched"] = 1
                if emails:
                    self._save_cache(registered_domain(lead.get("website", "")), emails, socials)

                async with lock:
                    if emails:
                        changed_total += 1
                    completed += 1
                    if completed % 5 == 0 or completed == len(remaining):
                        pct = 68 + int((completed / len(remaining)) * 27)
                        self._progress(
                            "Checked %d/%d websites (%d emails found)"
                            % (completed, len(remaining), changed_total),
                            pct,
                        )

            await asyncio.gather(*(process(lead) for lead in remaining))

        self.stats["found"] = changed_total
        hit_rate = int((changed_total / len(remaining)) * 100) if remaining else 0
        extras = []
        if self.stats.get("social_only"):
            extras.append("%d listed a social page as their website" % self.stats["social_only"])
        if self.stats.get("challenged"):
            extras.append("%d were behind a bot check" % self.stats["challenged"])
        if self.stats.get("from_search"):
            extras.append(
                "%d recovered by searching for the business by name"
                % self.stats["from_search"]
            )
        self._progress(
            "Email lookup complete: %d found from %d sites (%d%%), %d from cache, "
            "%d skipped.%s"
            % (changed_total, len(remaining), hit_rate, self.stats["from_cache"],
               self.stats["skipped"],
               (" " + "; ".join(extras) + "." if extras else "")),
            95,
        )
        return changed_total
