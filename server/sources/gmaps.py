"""Google Maps lead source, driven by Playwright.

What makes this fast:

1. **The feed already has most of it.** Name, rating, review count, category and
   street address are on the result card, and the listing href encodes lat/lng
   and the place id. All of it is read in a single JS call per scroll, not one
   page load per business.
2. **Only phone and canonical website need a detail page**, and those are opened
   as concurrent *tabs in one browser* rather than a pool of separate Chrome
   processes.
3. **Images, fonts and stylesheets never load** - blocked at the network layer.
4. **Adaptive grid.** One Maps search stops yielding at roughly 120 results, so
   a wide area is searched cell by cell, and any cell that comes back saturated
   is split into four and searched again. That is what makes an unlimited run
   possible.

Both phases report partial results through ``on_batch``, so a long run is saved
as it goes and can be stopped at any point without losing work.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Callable

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .. import settings
from .browser import browser_session

log = logging.getLogger(__name__)

COORDS_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
PLACE_ID_RE = re.compile(r"!19s([A-Za-z0-9_\-]+)")
# Google pads card text with private-use glyphs (icons). Built from
# codepoints rather than literal characters, because writing the range
# inline is easy to mangle - and a mangled "[-]" silently strips every
# hyphen from phone numbers and business names.
PRIVATE_USE_RE = re.compile("[%s-%s]" % (chr(0xE000), chr(0xF8FF)))
NBSP = chr(0x00A0)
MIDDOT = chr(0x00B7)
RATING_LABEL_RE = re.compile(r"([\d.]+)\s*stars?\s+([\d,]+)\s*review", re.I)
# Google alternates feed layouts. Some renders put the count in the aria-label
# ("4.9 stars 896 Reviews"), some inline as "4.9(896)", and some omit it
# entirely - so all three are handled and a blank means Google did not show it.
REVIEW_INLINE_RE = re.compile(r"\d[\d.]*\s*\((\d[\d,]*)\)")
REVIEW_COUNT_RE = re.compile(r"^\(?([\d,]+)\)?$")

ProgressFn = Callable[[str, int], None]

FEED_SELECTOR = 'div[role="feed"]'

# Read every visible card in one round trip.
_FEED_JS = """() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) { return []; }
  const out = [];
  const seen = new Set();
  feed.querySelectorAll('a[href*="/maps/place/"]').forEach((a) => {
    const href = a.href;
    if (!href || seen.has(href)) return;
    seen.add(href);
    const card = a.closest('div[jsaction]') || a.parentElement;
    if (!card) return;
    const pick = (sel) => {
      const e = card.querySelector(sel);
      return e ? e.textContent.trim() : '';
    };
    const blocks = Array.from(card.querySelectorAll('.W4Efsd')).map(
      (e) => e.textContent.trim()
    );
    // "4.9 stars 896 Reviews" - an aria-label, far more stable than the
    // obfuscated class names Google rotates.
    let ratingLabel = '';
    for (const e of card.querySelectorAll('[aria-label]')) {
      const label = e.getAttribute('aria-label') || '';
      if (/star/i.test(label)) { ratingLabel = label; break; }
    }
    out.push({
      href: href,
      name: pick('.qBF1Pd') || a.getAttribute('aria-label') || '',
      rating: pick('span.MW4etd'),
      ratingLabel: ratingLabel,
      reviewText: pick('span.UY7F9') || pick('span[aria-label$="reviews"]') || '',
      text: (card.innerText || '').slice(0, 300),
      blocks: blocks
    });
  });
  return out;
}"""

_DETAIL_JS = """() => {
  const res = {phones: [], website: '', address: '', name: '', category: ''};
  document.querySelectorAll('button[data-item-id], a[data-item-id]').forEach((el) => {
    const id = el.getAttribute('data-item-id') || '';
    const label = el.getAttribute('aria-label') || '';
    if (id.indexOf('phone') === 0) {
      // A listing can carry several numbers, each its own phone:tel:<number>
      // node. The id holds the canonical form; the label is prettier.
      const fromId = id.split('tel:')[1] || '';
      const fromLabel = label.split(':').slice(1).join(':').trim();
      const value = fromLabel || fromId;
      if (value && res.phones.indexOf(value) === -1) { res.phones.push(value); }
    } else if (id.indexOf('address') === 0) {
      res.address = label.split(':').slice(1).join(':').trim() || label.trim();
    } else if (id === 'authority') {
      res.website = el.getAttribute('href') || '';
    }
  });
  const h1 = document.querySelector('h1.DUwDvf') || document.querySelector('h1');
  if (h1) { res.name = h1.textContent.trim(); }
  const cat = document.querySelector('button[jsaction*="category"]')
           || document.querySelector('span.DkEaL');
  if (cat) { res.category = cat.textContent.trim(); }
  return res;
}"""

_SCROLL_JS = """() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) { return {height: 0, atEnd: true}; }
  feed.scrollTop = feed.scrollHeight;
  return {
    height: feed.scrollHeight,
    atEnd: (feed.innerText || '').toLowerCase().includes("reached the end of the list")
  };
}"""


# ---------------------------------------------------------------- geocoding
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_GEOCODE_CACHE: dict[str, tuple[float, float, float, float] | None] = {}


def geocode_bbox(place: str) -> tuple[float, float, float, float] | None:
    """Bounding box for a place name, via OpenStreetMap. Free, no key."""
    key = (place or "").strip().lower()
    if not key:
        return None
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]
    box = None
    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": settings.GEOCODE_USER_AGENT},
            timeout=20.0,
        )
        if response.status_code == 200:
            payload = response.json()
            if payload:
                south, north, west, east = (float(v) for v in payload[0]["boundingbox"])
                if north > south and east > west:
                    box = (south, north, west, east)
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        log.warning("geocoding failed for %r: %s", place, exc)
    _GEOCODE_CACHE[key] = box
    return box


def zoom_for_span(lon_span: float) -> int:
    """Map a cell's width in degrees to a Maps zoom level."""
    if lon_span <= 0:
        return settings.GMAPS_CELL_ZOOM
    return max(11, min(18, int(round(math.log2(360.0 / lon_span) + 3))))


def split_cell(
    cell: tuple[float, float, float, float]
) -> list[tuple[float, float, float, float]]:
    """Quarter a bounding box."""
    south, north, west, east = cell
    mid_lat, mid_lng = (south + north) / 2, (west + east) / 2
    return [
        (south, mid_lat, west, mid_lng),
        (south, mid_lat, mid_lng, east),
        (mid_lat, north, west, mid_lng),
        (mid_lat, north, mid_lng, east),
    ]


def grid_cells(
    bbox: tuple[float, float, float, float], cells: int
) -> list[tuple[float, float, float, float]]:
    """Split a bounding box into a grid, densest area first."""
    south, north, west, east = bbox
    per_side = max(1, int(math.ceil(math.sqrt(cells))))
    lat_step, lng_step = (north - south) / per_side, (east - west) / per_side
    out = [
        (
            south + lat_step * row,
            south + lat_step * (row + 1),
            west + lng_step * col,
            west + lng_step * (col + 1),
        )
        for row in range(per_side)
        for col in range(per_side)
    ]
    centre_lat, centre_lng = (south + north) / 2, (west + east) / 2
    out.sort(
        key=lambda c: ((c[0] + c[1]) / 2 - centre_lat) ** 2
        + ((c[2] + c[3]) / 2 - centre_lng) ** 2
    )
    return out


# ---------------------------------------------------------------- model
@dataclass
class MapsLead:
    business_name: str = ""
    owner_name: str = ""
    phone: str = ""
    website: str = ""
    email: str = ""
    phones: list = field(default_factory=list)
    emails: list = field(default_factory=list)
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


def _clean(text: str) -> str:
    stripped = PRIVATE_USE_RE.sub("", text or "").replace(NBSP, " ")
    return stripped.strip(" " + MIDDOT + chr(9) + chr(10))


def _parse_card(card: dict) -> MapsLead | None:
    name = _clean(card.get("name", ""))
    if not name:
        return None

    href = card.get("href", "")
    lead = MapsLead(business_name=name, source_url=href)

    coords = COORDS_RE.search(href)
    if coords:
        lead.latitude, lead.longitude = coords.group(1), coords.group(2)
    place = PLACE_ID_RE.search(href)
    if place:
        lead.place_id = place.group(1)

    lead.rating = _clean(card.get("rating", ""))
    label_match = RATING_LABEL_RE.search(card.get("ratingLabel", "") or "")
    if label_match:
        lead.rating = lead.rating or label_match.group(1)
        lead.reviews = label_match.group(2).replace(",", "")
    if not lead.reviews:
        direct = REVIEW_COUNT_RE.match(_clean(card.get("reviewText", "")))
        if direct:
            lead.reviews = direct.group(1).replace(",", "")
    if not lead.reviews:
        inline = REVIEW_INLINE_RE.search(card.get("text", "") or "")
        if inline:
            lead.reviews = inline.group(1).replace(",", "")

    # The feed renders "<category> - <price/icon> - <street address>" in one of
    # the .W4Efsd blocks. Take the block that splits into the most parts and is
    # not the opening-hours line.
    best: list[str] = []
    for block in card.get("blocks", []):
        cleaned = _clean(block)
        if not cleaned or "·" not in cleaned:
            continue
        if cleaned.lower().startswith(
            ("open", "closed", "closes", "opens", "temporarily", "permanently")
        ):
            continue
        parts = [p for p in (_clean(p) for p in cleaned.split("·")) if p]
        if len(parts) >= 2 and len(parts) > len(best):
            best = parts
    if best:
        lead.category, lead.address = best[0], best[-1]

    return lead


# ---------------------------------------------------------------- source
class GoogleMapsSource:
    """Scrapes Google Maps for business leads."""

    def __init__(self, on_progress: ProgressFn | None = None, on_batch=None):
        self._on_progress = on_progress
        # Called with the running lead list so long runs persist as they go.
        self._on_batch = on_batch
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _progress(self, message: str, percent: int = -1) -> None:
        log.info("[gmaps] %s", message)
        if self._on_progress:
            try:
                self._on_progress(message, percent)
            except Exception:  # noqa: BLE001 - progress must never break scraping
                log.debug("progress callback failed", exc_info=True)

    def _emit_batch(self, leads: list[MapsLead]) -> None:
        if not self._on_batch:
            return
        try:
            self._on_batch(list(leads))
        except Exception:  # noqa: BLE001
            log.debug("batch callback failed", exc_info=True)

    # -- page helpers --------------------------------------------------------

    async def _dismiss_consent(self, page: Page) -> None:
        for label in ("Accept all", "Reject all", "I agree"):
            try:
                button = page.get_by_role("button", name=label)
                if await button.count():
                    await button.first.click(timeout=3000)
                    await page.wait_for_timeout(700)
                    return
            except PlaywrightError:
                continue

    async def _await_feed(self, page: Page) -> bool:
        try:
            await page.wait_for_selector(FEED_SELECTOR, timeout=18000)
            return True
        except PlaywrightTimeout:
            return False

    async def _collect(
        self, page: Page, collected: dict[str, MapsLead], max_leads: int, max_rounds: int
    ) -> None:
        """Scroll the current feed, adding what it finds to ``collected``."""
        stagnant = 0
        last_count = len(collected)

        for _ in range(max_rounds):
            if self._stop.is_set():
                return
            try:
                cards = await page.evaluate(_FEED_JS)
            except PlaywrightError as exc:
                log.warning("feed read failed: %s", exc)
                return

            for card in cards or []:
                lead = _parse_card(card)
                if lead is None:
                    continue
                key = lead.place_id or lead.source_url or lead.business_name.lower()
                collected.setdefault(key, lead)

            if max_leads and len(collected) >= max_leads:
                return

            try:
                state = await page.evaluate(_SCROLL_JS)
            except PlaywrightError:
                return
            if state and state.get("atEnd"):
                return

            if len(collected) == last_count:
                stagnant += 1
                if stagnant >= 4:
                    return
            else:
                stagnant = 0
                last_count = len(collected)

            await page.wait_for_timeout(550)

    # -- phase 1: discovery --------------------------------------------------

    async def _discover_single(
        self, page: Page, query: str, max_leads: int
    ) -> list[MapsLead]:
        url = "https://www.google.com/maps/search/" + query.replace(" ", "+") + "?hl=en"
        self._progress("Searching Google Maps for: " + query, 5)
        await page.goto(url, wait_until="domcontentloaded")
        await self._dismiss_consent(page)
        if not await self._await_feed(page):
            self._progress("No results feed appeared for this search.", 30)
            return []

        collected: dict[str, MapsLead] = {}
        await self._collect(page, collected, max_leads, settings.GMAPS_SCROLL_ROUNDS)
        leads = list(collected.values())
        if max_leads:
            leads = leads[:max_leads]
        self._progress("Discovered %d businesses." % len(leads), 35)
        self._emit_batch(leads)
        return leads

    async def _discover_tiled(
        self, page: Page, keyword: str, location: str, bbox, max_leads: int
    ) -> list[MapsLead]:
        """Search an area cell by cell, splitting cells that look saturated."""
        unlimited = not max_leads
        target = max_leads or 0

        initial = (
            settings.GMAPS_MAX_CELLS
            if unlimited
            else min(
                settings.GMAPS_MAX_CELLS,
                max(4, int(math.ceil(target / settings.GMAPS_LEADS_PER_CELL))),
            )
        )
        queue: list[tuple[tuple[float, float, float, float], int]] = [
            (cell, 0) for cell in grid_cells(bbox, initial)
        ]

        self._progress(
            "Searching %s%s. Starting with %d map areas, splitting any that look busy - "
            "one Maps search only ever returns about 120 results."
            % (location, "" if unlimited else " for up to %d leads" % target, len(queue)),
            5,
        )

        collected: dict[str, MapsLead] = {}
        searches = 0
        consecutive_empty = 0

        while queue:
            if self._stop.is_set():
                self._progress(
                    "Stopped - keeping the %d found so far." % len(collected), 33
                )
                break
            if not unlimited and len(collected) >= target:
                self._progress("Reached the %d-lead limit." % target, 33)
                break
            if searches >= settings.GMAPS_MAX_SEARCHES:
                self._progress(
                    "Reached the %d-search ceiling for one run."
                    % settings.GMAPS_MAX_SEARCHES,
                    33,
                )
                break

            cell, depth = queue.pop(0)
            south, north, west, east = cell
            lat, lng = (south + north) / 2, (west + east) / 2
            zoom = zoom_for_span(east - west)

            before = len(collected)
            url = "https://www.google.com/maps/search/%s/@%.6f,%.6f,%dz?hl=en" % (
                keyword.replace(" ", "+"),
                lat,
                lng,
                zoom,
            )
            try:
                await page.goto(url, wait_until="domcontentloaded")
                if searches == 0:
                    await self._dismiss_consent(page)
                if await self._await_feed(page):
                    await self._collect(
                        page, collected, target, settings.GMAPS_CELL_SCROLLS
                    )
            except PlaywrightError as exc:
                log.warning("area search failed: %s", exc)
            searches += 1

            gained = len(collected) - before
            # A busy cell is hiding more behind the per-search cap: split it.
            if (
                gained >= settings.GMAPS_SATURATION
                and depth < settings.GMAPS_MAX_DEPTH
                and (unlimited or len(collected) < target)
            ):
                queue = [(c, depth + 1) for c in split_cell(cell)] + queue
                note = " - busy, splitting into 4"
            else:
                note = ""

            consecutive_empty = consecutive_empty + 1 if gained == 0 else 0
            pct = (
                min(30, 5 + int(searches / 3))
                if unlimited
                else min(33, 5 + int((len(collected) / max(1, target)) * 28))
            )
            self._progress(
                "Search %d: +%d new (%d found, %d areas queued)%s"
                % (searches, gained, len(collected), len(queue), note),
                pct,
            )

            if gained:
                self._emit_batch(list(collected.values()))
            if consecutive_empty >= 6:
                self._progress("Nothing new in the last few areas - stopping.", 33)
                break

        leads = list(collected.values())
        if not unlimited and target:
            leads = leads[:target]
        self._progress(
            "Discovered %d businesses from %d searches." % (len(leads), searches), 35
        )
        return leads

    # -- phase 2: detail -----------------------------------------------------

    async def _extract_detail(self, page: Page, lead: MapsLead) -> None:
        await page.goto(lead.source_url, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector("button[data-item-id], h1", timeout=9000)
        except PlaywrightTimeout:
            return

        data = await page.evaluate(_DETAIL_JS) or {}

        phones = [_clean(p) for p in (data.get("phones") or []) if _clean(p)]
        if phones:
            lead.phones = phones
            lead.phone = phones[0]
        if data.get("website"):
            lead.website = data["website"].strip()
        if data.get("address"):
            lead.address = _clean(data["address"])
        if data.get("category") and not lead.category:
            lead.category = _clean(data["category"])
        if data.get("name") and not lead.business_name:
            lead.business_name = _clean(data["name"])

    async def fetch_details(self, session, leads: list[MapsLead]) -> None:
        """Fill in phone and website, several tabs at a time, in one browser."""
        pending = [ld for ld in leads if ld.source_url]
        if not pending:
            return

        workers = max(1, min(settings.GMAPS_DETAIL_WORKERS, len(pending)))
        total = len(pending)
        self._progress(
            "Reading contact details for %d businesses (%d tabs)..." % (total, workers),
            36,
        )

        queue: asyncio.Queue = asyncio.Queue()
        for lead in pending:
            queue.put_nowait(lead)

        done = 0
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal done
            page = await session.new_page()
            try:
                while not self._stop.is_set():
                    try:
                        lead = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        await self._extract_detail(page, lead)
                    except PlaywrightError as exc:
                        log.debug("detail failed for %s: %s", lead.business_name, exc)
                    finally:
                        async with lock:
                            done += 1
                            current = done
                        if current % 5 == 0 or current == total:
                            self._progress(
                                "Contact details %d/%d" % (current, total),
                                36 + int((current / total) * 29),
                            )
                        # Persist as details land, so stopping mid-phase keeps
                        # the phone numbers already gathered.
                        if current % settings.GMAPS_DETAIL_BATCH == 0:
                            self._emit_batch(leads)
            finally:
                try:
                    await page.close()
                except PlaywrightError:
                    pass

        await asyncio.gather(*(worker() for _ in range(workers)))
        self._emit_batch(leads)
        self._progress("Contact details complete for %d businesses." % done, 65)

    # -- orchestration -------------------------------------------------------

    async def run_async(
        self, keyword: str, location: str, max_leads: int
    ) -> list[MapsLead]:
        query = f"{keyword} in {location}".strip() if location else keyword
        async with browser_session() as session:
            page = await session.new_page()

            bbox = None
            if (
                settings.GMAPS_TILING
                and location
                and (not max_leads or max_leads > settings.GMAPS_SINGLE_SEARCH_YIELD)
            ):
                self._progress("Looking up the area for %s..." % location, 3)
                bbox = geocode_bbox(location)
                if bbox is None:
                    self._progress(
                        "Could not map that location, so falling back to one search "
                        "(about %d results)." % settings.GMAPS_SINGLE_SEARCH_YIELD,
                        4,
                    )

            if bbox is not None:
                leads = await self._discover_tiled(
                    page, keyword, location, bbox, max_leads
                )
            else:
                leads = await self._discover_single(page, query, max_leads or 0)

            await page.close()

            if leads and not self._stop.is_set():
                await self.fetch_details(session, leads)
            return leads

    def run(self, keyword: str, location: str, max_leads: int) -> list[MapsLead]:
        """Blocking entry point - the job runner owns a thread, not a loop."""
        return asyncio.run(self.run_async(keyword, location, max_leads))


def lead_to_dict(lead: MapsLead) -> dict:
    return asdict(lead)
