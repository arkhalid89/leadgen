"""
Multi-Source Web Crawler — Lead Generation Engine
Searches Google, Bing, and DuckDuckGo via HTTP requests (no browser
automation) and deep-scrapes discovered websites for emails, phones,
addresses, and social profiles.

Sources:
  1. Google Search    (HTTP — parses /url?q= redirect links)
  2. Bing Search      (HTTP — parses li.b_algo containers)
  3. DuckDuckGo HTML  (HTTP — most bot-friendly engine)
  4. Website crawling  (HTTP — checks main page + /contact, /about)

No Selenium / headless browser is required, which makes this scraper
faster, lighter, and far less likely to be blocked by CAPTCHAs.
"""

import re
import json
import time
import random
import logging
import warnings
from dataclasses import dataclass, asdict
from urllib.parse import (
    quote_plus, urljoin, urlparse, unquote, parse_qs,
)
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from bs4 import BeautifulSoup
from ddgs import DDGS

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---- Regex patterns -------------------------------------------------------

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I
)

PHONE_RE = re.compile(
    r"(?:\+?\d{1,4}[\s\-.]?)?"          # country code
    r"(?:\(?\d{1,5}\)?[\s\-.]?)?"        # area code
    r"\d{2,4}[\s\-.]?\d{2,4}[\s\-.]?\d{0,4}",
)

EMAIL_BLACKLIST = {
    "example.com", "test.com", "email.com", "domain.com",
    "yoursite.com", "company.com", "website.com", "sentry.io",
    "wixpress.com", "w3.org", "schema.org", "googleapis.com",
    "googleusercontent.com", "gstatic.com", "facebook.com",
    "twitter.com", "instagram.com", "linkedin.com", "google.com",
    "bing.com", "microsoft.com", "apple.com", "amazon.com",
}

SOCIAL_PATTERNS = {
    "facebook": re.compile(
        r'https?://(?:www\.)?facebook\.com/[\w.\-]+', re.I
    ),
    "instagram": re.compile(
        r'https?://(?:www\.)?instagram\.com/[\w.\-]+', re.I
    ),
    "twitter": re.compile(
        r'https?://(?:www\.)?(?:twitter|x)\.com/[\w.\-]+', re.I
    ),
    "linkedin": re.compile(
        r'https?://(?:www\.)?linkedin\.com/(?:in|company)/[\w.\-]+', re.I
    ),
    "youtube": re.compile(
        r'https?://(?:www\.)?youtube\.com/(?:@|channel/|c/)[\w.\-]+', re.I
    ),
}

# Domains to skip when crawling (search engines, social, CDN, etc.)
SKIP_DOMAINS = {
    "google.com", "bing.com", "yahoo.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "tiktok.com", "pinterest.com", "reddit.com",
    "wikipedia.org", "amazon.com", "ebay.com", "apple.com",
    "microsoft.com", "github.com", "stackoverflow.com",
    "cloudflare.com", "gstatic.com", "googleapis.com",
    "googleusercontent.com", "yelp.com", "duckduckgo.com",
}


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class WebLead:
    """A business lead found via web crawling."""
    business_name: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    address: str = ""
    description: str = ""
    source: str = ""  # which search/site found this
    facebook: str = ""
    instagram: str = ""
    twitter: str = ""
    linkedin: str = ""
    youtube: str = ""


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class WebCrawlerScraper:
    """
    Multi-source web crawler for maximum lead generation.

    Uses plain HTTP requests (no Selenium / headless browser) for search
    engines, which is faster and avoids CAPTCHA / bot-detection issues.
    """

    USER_AGENTS = [
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.2 Safari/605.1.15"
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) "
            "Gecko/20100101 Firefox/134.0"
        ),
    ]

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._progress_callback = None
        self._should_stop = False
        self._partial_leads: list[dict] = []
        self._scrape_stats = {
            "queries_completed": 0,
            "total_queries": 0,
            "leads_found": 0,
            "websites_scanned": 0,
            "total_websites": 0,
            "phase": "idle",
        }

        # Reusable HTTP session with connection pooling
        self._http_session = requests.Session()
        self._http_session.verify = False
        self._http_session.headers.update({
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
        adapter = HTTPAdapter(
            pool_connections=15, pool_maxsize=30,
            max_retries=Retry(total=2, backoff_factor=0.05),
        )
        self._http_session.mount("https://", adapter)
        self._http_session.mount("http://", adapter)

    def set_progress_callback(self, callback):
        self._progress_callback = callback

    def stop(self):
        self._should_stop = True

    def get_partial_leads(self) -> list[dict]:
        """Return leads collected so far (used when stopping early)."""
        return list(self._partial_leads)

    @property
    def scrape_stats(self) -> dict:
        return dict(self._scrape_stats)

    def _report_progress(self, message: str, percentage: int = -1):
        logger.info(message)
        if self._progress_callback:
            self._progress_callback(message, percentage)

    # ---- Utility -------------------------------------------------------

    def _is_valid_result_url(self, url: str) -> bool:
        """Check that a URL is a real external business site."""
        if not url or not url.startswith("http"):
            return False
        domain = urlparse(url).netloc.lower()
        root = ".".join(domain.split(".")[-2:])
        return root not in SKIP_DOMAINS

    def _random_ua_headers(self, referer: str = "") -> dict:
        """Return a fresh set of headers with a random User-Agent."""
        h = {"User-Agent": random.choice(self.USER_AGENTS)}
        if referer:
            h["Referer"] = referer
        return h

    # ---- Build search queries ------------------------------------------

    def _build_queries(
        self, keyword: str, place: str,
    ) -> list[tuple[str, str]]:
        """
        Build a diverse set of (query, engine) tuples for maximum coverage.
        Returns list of (query_string, "google" | "bing" | "duckduckgo").
        """
        queries: list[tuple[str, str]] = []

        # --- Google queries ---
        google_qs = [
            f'"{keyword}" "{place}" email phone',
            f'"{keyword}" "{place}" contact',
            f'"{keyword}" in {place} "phone" OR "email" OR "contact"',
            f'"{keyword}" "{place}" "@gmail.com" OR "@yahoo.com" OR "@hotmail.com"',
            f'"{keyword}" company "{place}" directory',
            f'"{keyword}" business "{place}" list',
            f'"{keyword}" "{place}" site:yellowpages.com OR site:yelp.com',
            f'"{keyword}" "{place}" "phone:" OR "tel:" OR "email:"',
            f'inurl:directory "{keyword}" "{place}"',
            f'"{keyword}" services "{place}" contact us',
        ]
        for q in google_qs:
            queries.append((q, "google"))

        # --- Bing queries ---
        bing_qs = [
            f'"{keyword}" "{place}" email phone contact',
            f'"{keyword}" business "{place}" directory listing',
            f'"{keyword}" "{place}" "@gmail.com" OR "@yahoo.com"',
            f'"{keyword}" companies "{place}" contact details',
            f'"{keyword}" "{place}" telephone address website',
            f'"{keyword}" professional "{place}" email',
            f'"{keyword}" shop store "{place}" phone',
            f'"{keyword}" agency firm "{place}" contact',
        ]
        for q in bing_qs:
            queries.append((q, "bing"))

        # --- DuckDuckGo queries (simpler phrasing works best) ---
        ddg_qs = [
            f'{keyword} {place} email phone contact',
            f'{keyword} business {place} directory',
            f'{keyword} {place} contact details address',
            f'{keyword} company {place} phone email website',
            f'{keyword} {place} professional services contact',
            f'{keyword} {place} local business listing',
        ]
        for q in ddg_qs:
            queries.append((q, "duckduckgo"))

        return queries

    # ---- Google HTTP Search --------------------------------------------

    def _google_search(self, query: str, num_pages: int = 3) -> list[dict]:
        """
        Search Google via plain HTTP.  Parses redirect-style /url?q= links
        that Google sends in the non-JavaScript version of its results page.
        Falls back to div.g containers if present.
        """
        results: list[dict] = []
        seen: set[str] = set()

        for page in range(num_pages):
            if self._should_stop:
                break

            start = page * 10
            try:
                resp = self._http_session.get(
                    "https://www.google.com/search",
                    params={
                        "q": query, "start": str(start),
                        "hl": "en", "num": "10",
                    },
                    headers=self._random_ua_headers(
                        "https://www.google.com/"
                    ),
                    timeout=12,
                )
                if resp.status_code != 200:
                    logger.warning("Google HTTP %d", resp.status_code)
                    continue

                lower = resp.text.lower()
                if "captcha" in lower or "unusual traffic" in lower:
                    logger.warning("Google CAPTCHA on HTTP — backing off")
                    time.sleep(10 + random.uniform(5, 10))
                    resp = self._http_session.get(
                        "https://www.google.com/search",
                        params={
                            "q": query, "start": str(start),
                            "hl": "en", "num": "10",
                        },
                        headers=self._random_ua_headers(
                            "https://www.google.com/"
                        ),
                        timeout=12,
                    )
                    lower2 = resp.text.lower()
                    if "captcha" in lower2 or "unusual traffic" in lower2:
                        logger.error("Still blocked by Google — skipping.")
                        break

                soup = BeautifulSoup(resp.text, "lxml")

                # Strategy 1: /url?q= redirect links (non-JS Google)
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"]
                    if "/url?q=" not in href:
                        continue
                    actual = unquote(
                        href.split("/url?q=")[1].split("&")[0]
                    )
                    if not self._is_valid_result_url(actual):
                        continue
                    if actual in seen:
                        continue
                    seen.add(actual)

                    title = a_tag.get_text(strip=True)[:150]
                    snippet = ""
                    parent = a_tag.find_parent()
                    if parent:
                        nxt = parent.find_next_sibling()
                        if nxt:
                            snippet = nxt.get_text(strip=True)[:300]

                    results.append({
                        "url": actual, "title": title,
                        "snippet": snippet,
                    })

                # Strategy 2: div.g containers (JS-rendered, if any)
                for div in soup.select("div.g"):
                    a_tag = div.find("a", href=True)
                    if not a_tag:
                        continue
                    href = a_tag.get("href", "")
                    if "/url?q=" in href:
                        href = unquote(
                            href.split("/url?q=")[1].split("&")[0]
                        )
                    if not self._is_valid_result_url(href):
                        continue
                    if href in seen:
                        continue
                    seen.add(href)

                    title = ""
                    h3 = div.find("h3")
                    if h3:
                        title = h3.get_text(strip=True)[:150]
                    snippet = ""
                    for sel in (
                        "div.VwiC3b", "div[data-sncf]", "span.st"
                    ):
                        s = div.select_one(sel)
                        if s:
                            snippet = s.get_text(strip=True)[:300]
                            break
                    if not snippet:
                        snippet = div.get_text(strip=True)[:300]

                    results.append({
                        "url": href, "title": title,
                        "snippet": snippet,
                    })

            except Exception as e:
                logger.error("Google HTTP error: %s", e)
                continue

            time.sleep(2.0 + random.uniform(1.0, 3.0))

        return results

    # ---- Bing HTTP Search ----------------------------------------------

    def _bing_search(self, query: str, num_pages: int = 3) -> list[dict]:
        """Search Bing via plain HTTP requests."""
        results: list[dict] = []
        seen: set[str] = set()

        for page in range(num_pages):
            if self._should_stop:
                break

            first = page * 10 + 1
            try:
                resp = self._http_session.get(
                    "https://www.bing.com/search",
                    params={
                        "q": query, "first": str(first), "count": "10",
                    },
                    headers=self._random_ua_headers(
                        "https://www.bing.com/"
                    ),
                    timeout=12,
                )
                if resp.status_code != 200:
                    logger.warning("Bing HTTP %d", resp.status_code)
                    continue

                soup = BeautifulSoup(resp.text, "lxml")

                for item in soup.select("li.b_algo"):
                    a_tag = item.select_one("h2 a")
                    if not a_tag:
                        a_tag = item.find("a", href=True)
                    if not a_tag:
                        continue

                    href = a_tag.get("href", "")
                    if not self._is_valid_result_url(href):
                        continue
                    if href in seen:
                        continue
                    seen.add(href)

                    title = a_tag.get_text(strip=True)[:150]
                    snippet = ""
                    cap = item.select_one("div.b_caption p")
                    if cap:
                        snippet = cap.get_text(strip=True)[:300]
                    else:
                        p = item.find("p")
                        if p:
                            snippet = p.get_text(strip=True)[:300]

                    results.append({
                        "url": href, "title": title,
                        "snippet": snippet,
                    })

                if not soup.select_one("a.sb_pagN"):
                    break

            except Exception as e:
                logger.error("Bing HTTP error: %s", e)
                continue

            time.sleep(1.5 + random.uniform(0.5, 2.0))

        return results

    # ---- DuckDuckGo Search (via ddgs library) ---------------------------

    def _duckduckgo_search(
        self, query: str, num_pages: int = 3,
    ) -> list[dict]:
        """
        Search DuckDuckGo using the ``ddgs`` library which handles
        browser impersonation and anti-bot measures automatically.
        """
        results: list[dict] = []
        seen: set[str] = set()
        max_results = num_pages * 10  # ~10 results per "page"

        try:
            ddg_results = DDGS().text(
                query, max_results=max_results,
            )
            for item in ddg_results:
                if self._should_stop:
                    break
                href = item.get("href", "")
                if not self._is_valid_result_url(href):
                    continue
                if href in seen:
                    continue
                seen.add(href)

                results.append({
                    "url": href,
                    "title": (item.get("title", "") or "")[:150],
                    "snippet": (item.get("body", "") or "")[:300],
                })
        except Exception as e:
            logger.error("DDG search error: %s", e)

        return results

    # ---- Website deep scraping -----------------------------------------

    def _scrape_website(self, url: str) -> WebLead | None:
        """
        Visit a business website and extract all contact info:
        emails, phones, social links, address, description.
        """
        lead = WebLead()
        lead.website = url
        lead.source = urlparse(url).netloc

        if not url.startswith("http"):
            url = "https://" + url

        pages_to_check = [url]
        for path in ["/contact", "/contact-us", "/about", "/about-us"]:
            pages_to_check.append(urljoin(url, path))

        all_emails: set[str] = set()
        all_phones: set[str] = set()
        found_socials: dict[str, str] = {k: "" for k in SOCIAL_PATTERNS}

        for page_url in pages_to_check:
            if self._should_stop:
                break
            try:
                resp = self._http_session.get(
                    page_url, timeout=8, allow_redirects=True,
                )
                if resp.status_code != 200:
                    continue

                text = resp.text
                soup = BeautifulSoup(text, "lxml")

                # Business name from <title> tag
                if not lead.business_name:
                    title_tag = soup.find("title")
                    if title_tag and title_tag.string:
                        name = title_tag.string.strip()
                        for sep in [" | ", " - ", " \u2014 ", " \u2013 "]:
                            if sep in name:
                                name = name.split(sep)[0].strip()
                        if name and len(name) < 100:
                            lead.business_name = name

                # Description from meta
                if not lead.description:
                    meta_desc = soup.find(
                        "meta", attrs={"name": "description"}
                    )
                    if meta_desc and meta_desc.get("content"):
                        lead.description = (
                            meta_desc["content"].strip()[:200]
                        )

                # Emails from mailto: links
                for a in soup.find_all("a", href=True):
                    href_val = a["href"]
                    if href_val.startswith("mailto:"):
                        email = (
                            href_val.replace("mailto:", "")
                            .split("?")[0].strip()
                        )
                        if self._is_valid_email(email):
                            all_emails.add(email.lower())

                # Emails from page text
                for match in EMAIL_RE.findall(text):
                    if self._is_valid_email(match):
                        all_emails.add(match.lower())

                # Phones from tel: links
                for a in soup.find_all("a", href=True):
                    href_val = a["href"]
                    if href_val.startswith("tel:"):
                        phone = href_val.replace("tel:", "").strip()
                        phone = re.sub(r"[^\d+\-() ]", "", phone)
                        if len(phone) >= 7:
                            all_phones.add(phone)

                # Phones from page text (likely sections only)
                for el in soup.find_all(
                    ["p", "span", "div", "a", "li"],
                    string=re.compile(
                        r"(?:phone|tel|call|mobile|whatsapp|contact)",
                        re.I,
                    ),
                ):
                    parent_text = el.get_text()
                    for m in PHONE_RE.findall(parent_text):
                        cleaned = re.sub(
                            r"[^\d+\-() ]", "", m,
                        ).strip()
                        if 7 <= len(cleaned) <= 20:
                            all_phones.add(cleaned)

                # Social links
                for a in soup.find_all("a", href=True):
                    href_val = a["href"]
                    for platform, pattern in SOCIAL_PATTERNS.items():
                        if not found_socials[platform]:
                            mt = pattern.match(href_val)
                            if mt:
                                found_socials[platform] = mt.group(0)

                # Social links from raw source (JS-embedded)
                for platform, pattern in SOCIAL_PATTERNS.items():
                    if not found_socials[platform]:
                        mt = pattern.search(text)
                        if mt:
                            found_socials[platform] = mt.group(0)

                # Address from structured data
                if not lead.address:
                    for script in soup.find_all(
                        "script", type="application/ld+json",
                    ):
                        try:
                            data = json.loads(script.string or "")
                            if isinstance(data, dict):
                                addr = data.get("address", {})
                                if isinstance(addr, dict):
                                    parts = [
                                        addr.get("streetAddress", ""),
                                        addr.get("addressLocality", ""),
                                        addr.get("addressRegion", ""),
                                        addr.get("postalCode", ""),
                                        addr.get("addressCountry", ""),
                                    ]
                                    full = ", ".join(
                                        p for p in parts if p
                                    )
                                    if full:
                                        lead.address = full[:200]
                        except Exception:
                            pass

                # Early exit if we have everything
                if (all_emails and all_phones
                        and all(found_socials.values())):
                    break

            except requests.RequestException:
                continue
            except Exception as e:
                logger.debug("Error scraping %s: %s", page_url, e)
                continue

        # Assign to lead
        if all_emails:
            lead.email = "; ".join(sorted(all_emails))
        if all_phones:
            lead.phone = "; ".join(sorted(all_phones)[:3])
        for platform, url_val in found_socials.items():
            setattr(lead, platform, url_val)

        # Only return if we found something useful
        has_contact = lead.email or lead.phone
        has_name = (
            lead.business_name and lead.business_name != "Unknown"
        )
        if has_contact or has_name:
            return lead
        return None

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        if not email or "@" not in email:
            return False
        domain = email.split("@")[1].lower()
        if domain in EMAIL_BLACKLIST:
            return False
        if domain.endswith(
            (".png", ".jpg", ".gif", ".svg", ".webp", ".js", ".css")
        ):
            return False
        return True

    # ---- Quick snippet-based lead extraction ---------------------------

    def _extract_lead_from_snippet(
        self, result: dict, keyword: str, place: str,
    ) -> WebLead | None:
        """
        Try to extract basic lead info directly from search result
        snippets without visiting the website (fast path).
        """
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        url = result.get("url", "")
        combined = f"{title} {snippet}"

        emails: set[str] = set()
        for m in EMAIL_RE.findall(combined):
            if self._is_valid_email(m):
                emails.add(m.lower())

        phones: set[str] = set()
        for m in PHONE_RE.findall(combined):
            cleaned = re.sub(r"[^\d+\-() ]", "", m).strip()
            if 7 <= len(cleaned) <= 20:
                phones.add(cleaned)

        if not emails and not phones:
            return None

        lead = WebLead()
        lead.website = url
        lead.source = urlparse(url).netloc if url else "search"
        lead.email = "; ".join(sorted(emails))
        lead.phone = "; ".join(sorted(phones)[:3])

        if title:
            name = title
            for sep in [" | ", " - ", " \u2014 ", " \u2013 "]:
                if sep in name:
                    name = name.split(sep)[0].strip()
            lead.business_name = name[:100]

        if snippet:
            lead.description = snippet[:200]

        return lead

    # ---- Main scrape method -------------------------------------------

    def scrape(
        self,
        keyword: str,
        place: str,
        max_pages: int = 3,
    ) -> list[dict]:
        """
        Main scraping entry point.

        Searches Google + Bing + DuckDuckGo via HTTP, then deep-scrapes
        the found websites in parallel for emails, phones, and socials.

        Args:
            keyword: Business type (e.g., "real estate", "plumber")
            place: Location (e.g., "Dubai", "New York")
            max_pages: Result pages per query per engine

        Returns:
            List of WebLead dicts
        """
        self._should_stop = False
        self._partial_leads = []

        try:
            self._report_progress("Building search queries...", 2)

            queries = self._build_queries(keyword, place)
            total_queries = len(queries)
            all_search_results: list[dict] = []
            snippet_leads: list[WebLead] = []
            self._scrape_stats["total_queries"] = total_queries
            self._scrape_stats["phase"] = "searching"

            # Phase 1: Search engines (HTTP - no browser needed)
            for qi, (query, engine) in enumerate(queries):
                if self._should_stop:
                    break

                pct = 3 + int((qi / total_queries) * 40)
                self._report_progress(
                    f"{engine.title()} search "
                    f"({qi + 1}/{total_queries})...",
                    pct,
                )

                if engine == "google":
                    results = self._google_search(
                        query, num_pages=max_pages,
                    )
                elif engine == "bing":
                    results = self._bing_search(
                        query, num_pages=max_pages,
                    )
                else:
                    results = self._duckduckgo_search(
                        query, num_pages=max_pages,
                    )

                all_search_results.extend(results)
                self._scrape_stats["queries_completed"] = qi + 1

                # Quick snippet extraction
                for r in results:
                    snippet_lead = self._extract_lead_from_snippet(
                        r, keyword, place,
                    )
                    if snippet_lead:
                        snippet_leads.append(snippet_lead)
                        self._partial_leads.append(asdict(snippet_lead))
                        self._scrape_stats["leads_found"] = len(
                            self._partial_leads
                        )

                # Delay between queries
                if qi < total_queries - 1 and not self._should_stop:
                    if engine == "google":
                        time.sleep(random.uniform(2, 4))
                    elif engine == "bing":
                        time.sleep(random.uniform(1, 2))
                    else:
                        time.sleep(random.uniform(0.5, 1.5))

            # Phase 2: Deduplicate and deep-scrape websites
            seen_domains: set[str] = set()
            unique_urls: list[str] = []
            for r in all_search_results:
                url = r["url"]
                domain = urlparse(url).netloc.lower()
                if domain not in seen_domains:
                    seen_domains.add(domain)
                    unique_urls.append(url)

            total_urls = len(unique_urls)
            self._scrape_stats["total_websites"] = total_urls
            self._scrape_stats["phase"] = "deep_scraping"
            self._report_progress(
                f"Found {total_urls} unique websites + "
                f"{len(snippet_leads)} snippet leads. "
                f"Deep-scraping websites...",
                45,
            )

            deep_leads: list[WebLead] = []
            if unique_urls and not self._should_stop:
                with ThreadPoolExecutor(max_workers=15) as executor:
                    futures = {
                        executor.submit(self._scrape_website, url): url
                        for url in unique_urls
                    }
                    done_count = 0
                    for future in as_completed(futures):
                        done_count += 1
                        self._scrape_stats["websites_scanned"] = (
                            done_count
                        )
                        if self._should_stop:
                            executor.shutdown(
                                wait=False, cancel_futures=True,
                            )
                            break
                        try:
                            lead = future.result(timeout=8)
                            if lead:
                                deep_leads.append(lead)
                                self._partial_leads.append(
                                    asdict(lead)
                                )
                                self._scrape_stats["leads_found"] = (
                                    len(self._partial_leads)
                                )
                        except Exception as e:
                            logger.debug(
                                "Website scrape error: %s", e,
                            )

                        if (done_count % 5 == 0 or
                                done_count == total_urls):
                            pct = 45 + int(
                                (done_count / total_urls) * 50
                            )
                            self._report_progress(
                                f"Scraped {done_count}/{total_urls} "
                                f"websites "
                                f"({len(deep_leads)} leads found)...",
                                min(pct, 95),
                            )

            # Phase 3: Merge snippet + deep leads, deduplicate
            all_leads: list[WebLead] = []
            seen_keys: set[str] = set()

            for lead in deep_leads:
                key = lead.website or lead.business_name
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    all_leads.append(lead)

            for lead in snippet_leads:
                key = lead.website or lead.business_name
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    all_leads.append(lead)

            leads = [asdict(l) for l in all_leads]
            self._partial_leads = list(leads)
            self._scrape_stats["leads_found"] = len(leads)
            self._scrape_stats["phase"] = "done"

            self._report_progress(
                f"Done! Found {len(leads)} leads from "
                f"{total_queries} queries across "
                f"Google, Bing & DuckDuckGo.",
                100,
            )

        except Exception as e:
            logger.error("Web crawler failed: %s", e)
            self._report_progress(
                f"Error: {e}. Saved "
                f"{len(self._partial_leads)} partial leads.",
                -1,
            )
            raise

        return leads


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def clean_web_leads(leads: list[dict]) -> list[dict]:
    """Clean and deduplicate web crawler leads."""
    cleaned: list[dict] = []
    seen: set[str] = set()

    for lead in leads:
        website = lead.get("website", "").strip()
        name = lead.get("business_name", "").strip()
        domain = (
            urlparse(website).netloc.lower() if website else ""
        )

        key = domain or name.lower()
        if not key or key in seen:
            continue
        seen.add(key)

        phone = lead.get("phone", "")
        if phone:
            phone = re.sub(r"[^\d+\-();, ]", "", phone).strip()

        cleaned.append({
            "business_name": name or "N/A",
            "phone": phone or "N/A",
            "email": lead.get("email", "N/A") or "N/A",
            "website": website or "N/A",
            "address": lead.get("address", "N/A") or "N/A",
            "description": lead.get("description", "N/A") or "N/A",
            "source": lead.get("source", "N/A") or "N/A",
            "facebook": lead.get("facebook", "N/A") or "N/A",
            "instagram": lead.get("instagram", "N/A") or "N/A",
            "twitter": lead.get("twitter", "N/A") or "N/A",
            "linkedin": lead.get("linkedin", "N/A") or "N/A",
            "youtube": lead.get("youtube", "N/A") or "N/A",
        })

    return cleaned
