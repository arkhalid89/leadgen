"""Web search lead source.

Given a keyword (and optionally a location) this finds the websites of real
businesses in that niche. It fans the keyword out into several query variants,
runs them through a SERP backend, then keeps one lead per registered domain
after stripping out directories, social networks and marketplaces.

Backends, in order of preference:
  * Serper.dev - genuine Google results, needs ``SERPER_API_KEY``.
  * DuckDuckGo via the ``ddgs`` package - free, no key, the default.

Scraping google.com's own SERP with a browser is deliberately not an option:
that is exactly what made the previous LinkedIn tool return zero leads.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Callable
from urllib.parse import urlparse

import requests

from .. import settings

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int], None]

# Hosts that are never a business's own site.
EXCLUDED_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "tiktok.com", "pinterest.com", "reddit.com", "quora.com",
    "yelp.com", "yellowpages.com", "tripadvisor.com", "trustpilot.com",
    "bbb.org", "angi.com", "houzz.com", "thumbtack.com", "manta.com",
    "foursquare.com", "mapquest.com", "google.com", "bing.com", "apple.com",
    "amazon.com", "ebay.com", "etsy.com", "alibaba.com", "indeed.com",
    "glassdoor.com", "ziprecruiter.com", "crunchbase.com", "wikipedia.org",
    "medium.com", "wordpress.com", "blogspot.com", "substack.com",
    "eventbrite.com", "meetup.com", "groupon.com", "booking.com",
    "expedia.com", "opentable.com", "doordash.com", "ubereats.com",
    "grubhub.com", "zomato.com", "justdial.com", "clutch.co", "g2.com",
    "capterra.com", "producthunt.com", "github.com", "nextdoor.com",
    "healthgrades.com", "zocdoc.com", "webmd.com", "vagaro.com", "yell.com",
    # Trade directories and quote-comparison sites - heavy in UK/AU results.
    "checkatrade.com", "trustatrader.com", "ratedpeople.com", "mybuilder.com",
    "which.co.uk", "bark.com", "freeindex.co.uk", "thomsonlocal.com",
    "scoot.co.uk", "192.com", "cylex.us.com", "hotfrog.com", "brownbook.net",
    "tupalo.com", "local.com", "superpages.com", "citysearch.com",
    "yelp.co.uk", "trustpilot.co.uk", "hipages.com.au", "oneflare.com.au",
    "truelocal.com.au", "productreview.com.au", "sulekha.com", "urbanpro.com",
    "quora.com", "pinterest.co.uk", "gumtree.com", "craigslist.org",
}

EXCLUDED_SUFFIXES = (".gov", ".edu", ".mil")

# Domain shapes that almost always mean a directory, comparison or lead-broker
# site rather than a business that could itself become a lead.
DIRECTORY_DOMAIN_RE = re.compile(
    r"(^|[.\-])("
    r"compare|comparison|directory|directories|listings?|reviews?|ratings?"
    r"|quotes?|finda|find-?a-|top\d+|best\d+|bestof|nearme|near-me"
    r"|tradespeople|checka|yellowpages|whitepages|businesslist"
    r")([.\-]|$)",
    re.I,
)

# Marks a URL as an article/listicle rather than a business homepage.
LISTICLE_RE = re.compile(
    r"\b(top|best|cheapest|list of|guide to|reviews of|\d+\s+best)\b", re.I
)

# Query shapes that surface business homepages rather than directory pages.
QUERY_TEMPLATES = [
    "{kw} {loc}",
    "{kw} {loc} official website",
    "{kw} company {loc}",
    "{kw} services {loc} contact",
    "best {kw} in {loc}",
    "{kw} {loc} about us",
    "{kw} agency {loc}",
    "local {kw} business {loc}",
]

QUERY_TEMPLATES_NO_LOC = [
    "{kw}",
    "{kw} official website",
    "{kw} company",
    "{kw} services contact",
    "{kw} agency",
    "top {kw} companies",
    "{kw} providers",
    "{kw} about us",
]


@dataclass
class WebLead:
    business_name: str = ""
    owner_name: str = ""
    phone: str = ""
    website: str = ""
    email: str = ""
    address: str = ""
    rating: str = ""
    reviews: str = ""
    category: str = ""
    latitude: str = ""
    longitude: str = ""
    place_id: str = ""
    source_url: str = ""
    facebook: str = ""
    instagram: str = ""
    twitter: str = ""
    linkedin: str = ""
    youtube: str = ""
    summary: str = ""
    extra: dict = field(default_factory=dict)


def registered_domain(url: str) -> str:
    """Best-effort eTLD+1. Good enough for deduplication."""
    try:
        host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    except ValueError:
        return ""
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Handle the common two-part public suffixes (co.uk, com.au, com.pk...).
    if parts[-2] in {"co", "com", "org", "net", "gov", "ac", "edu"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _is_business_domain(domain: str) -> bool:
    if not domain or "." not in domain:
        return False
    if domain in EXCLUDED_DOMAINS:
        return False
    if domain.endswith(EXCLUDED_SUFFIXES):
        return False
    if DIRECTORY_DOMAIN_RE.search(domain.rsplit(".", 1)[0]):
        return False
    return True


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _title_to_name(title: str, domain: str = "") -> str:
    """Turn a SERP title into a plausible business name.

    Titles come as "<Business> | <tagline>" about as often as the reverse, so
    position alone is a poor guide. The domain is the tiebreaker: the segment
    whose letters best match the registered domain is almost always the brand
    ("The Dental Centre" for dentalcentreaustin.com).
    """
    cleaned = re.sub(r"\s+", " ", title or "").strip()
    if not cleaned:
        return ""
    parts = [p.strip(" -|:·") for p in re.split(r"\s[|–—\-·:]\s", cleaned) if p.strip(" -|:·")]
    if not parts:
        return cleaned

    domain_slug = _slug(domain.rsplit(".", 1)[0] if domain else "")

    def score(part: str) -> tuple[int, int]:
        words = part.split()
        points = 0
        part_slug = _slug(part)
        if domain_slug and part_slug:
            if part_slug in domain_slug or domain_slug in part_slug:
                points += 6
            else:
                # Reward each word that survives into the domain.
                points += min(4, sum(1 for w in words if len(w) > 3 and _slug(w) in domain_slug))
        if 1 <= len(words) <= 5:
            points += 2
        if len(words) > 8:
            points -= 2
        if re.search(r"\b(in|near|around|serving)\b", part, re.I):
            points -= 2
        if LISTICLE_RE.search(part):
            points -= 4
        # Prefer segments that read like a name rather than a sentence.
        if words and sum(1 for w in words if w[:1].isupper()) >= max(1, len(words) - 1):
            points += 1
        return points, -len(words)

    return max(parts, key=score)


class WebSearchSource:
    """Finds business websites for a keyword using a SERP backend."""

    def __init__(self, on_progress: ProgressFn | None = None):
        self._on_progress = on_progress
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _progress(self, message: str, percent: int = -1) -> None:
        log.info("[websearch] %s", message)
        if self._on_progress:
            try:
                self._on_progress(message, percent)
            except Exception:  # noqa: BLE001
                log.debug("progress callback failed", exc_info=True)

    @property
    def backend(self) -> str:
        return "serper" if settings.SERPER_API_KEY else "duckduckgo"

    def _build_queries(self, keyword: str, location: str) -> list[str]:
        templates = QUERY_TEMPLATES if location else QUERY_TEMPLATES_NO_LOC
        queries = []
        for tpl in templates[: settings.SEARCH_MAX_QUERIES]:
            q = tpl.format(kw=keyword, loc=location).strip()
            q = re.sub(r"\s+", " ", q)
            if q not in queries:
                queries.append(q)
        return queries

    # -- backends ------------------------------------------------------------

    def _search_serper(self, query: str, limit: int) -> list[dict]:
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": settings.SERPER_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": min(limit, 100)},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("serper query failed (%s): %s", query, exc)
            return []
        return [
            {
                "title": item.get("title", ""),
                "href": item.get("link", ""),
                "body": item.get("snippet", ""),
            }
            for item in payload.get("organic", [])
        ]

    def _search_ddg(self, query: str, limit: int) -> list[dict]:
        try:
            from ddgs import DDGS
        except ImportError:
            log.error("ddgs is not installed; web search is unavailable")
            return []
        try:
            return DDGS().text(query, max_results=limit, region="wt-wt")
        except Exception as exc:  # noqa: BLE001 - ddgs raises assorted network errors
            log.warning("duckduckgo query failed (%s): %s", query, exc)
            return []

    def _search(self, query: str, limit: int) -> list[dict]:
        if settings.SERPER_API_KEY:
            results = self._search_serper(query, limit)
            if results:
                return results
        return self._search_ddg(query, limit)

    # -- main ----------------------------------------------------------------

    def run(self, keyword: str, location: str, max_leads: int) -> list[WebLead]:
        queries = self._build_queries(keyword, location)
        per_query = settings.SEARCH_RESULTS_PER_QUERY
        by_domain: dict[str, WebLead] = {}

        self._progress(
            "Searching the web via %s across %d query variants..." % (self.backend, len(queries)),
            5,
        )

        for idx, query in enumerate(queries):
            if self._stop.is_set():
                break
            if max_leads and len(by_domain) >= max_leads:
                break

            results = self._search(query, per_query)
            new_here = 0

            for item in results:
                href = (item.get("href") or "").strip()
                if not href:
                    continue
                domain = registered_domain(href)
                if not _is_business_domain(domain) or domain in by_domain:
                    continue

                title = item.get("title") or ""
                if LISTICLE_RE.search(title):
                    continue

                name = _title_to_name(title, domain) or domain
                by_domain[domain] = WebLead(
                    business_name=name,
                    website="https://" + domain,
                    summary=(item.get("body") or "").strip(),
                    category=keyword,
                    address=location,
                    source_url=href,
                    extra={"serp_title": title, "serp_query": query},
                )
                new_here += 1
                if max_leads and len(by_domain) >= max_leads:
                    break

            pct = 5 + int(((idx + 1) / len(queries)) * 55)
            self._progress(
                "Query %d/%d: +%d new sites (%d total)"
                % (idx + 1, len(queries), new_here, len(by_domain)),
                pct,
            )

        leads = list(by_domain.values())
        if max_leads:
            leads = leads[:max_leads]
        self._progress("Found %d unique business websites." % len(leads), 62)
        return leads


def lead_to_dict(lead: WebLead) -> dict:
    return asdict(lead)
