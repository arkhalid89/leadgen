"""Google Maps lead source.

Three things make this materially faster than the previous implementation:

1. The result feed already carries name, rating, review count, category and
   street address, and the listing href encodes lat/lng and the place id. The
   old code opened a detail page for every business just to read fields that
   were already on screen. We read them from the feed in a single JS call.
2. Only the two fields the feed genuinely lacks - phone and canonical website -
   need a detail page, and those are fetched by a *pool* of browsers instead of
   one browser walking the list sequentially.
3. Images and fonts are blocked at the browser level.
"""
from __future__ import annotations

import logging
import math
import queue
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import httpx
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .. import settings
from .browser import build_driver, quit_driver

log = logging.getLogger(__name__)

COORDS_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
PLACE_ID_RE = re.compile(r"!19s([A-Za-z0-9_\-]+)")
PRIVATE_USE_RE = re.compile(r"[-]")
RATING_LABEL_RE = re.compile(r"([\d.]+)\s*stars?\s+([\d,]+)\s*review", re.I)

ProgressFn = Callable[[str, int], None]

# Feed scraping runs entirely in the page so we pay for one round trip, not one
# per field per card.
_FEED_JS = """
const feed = document.querySelector('div[role="feed"]');
if (!feed) { return []; }
const out = [];
const seen = new Set();
feed.querySelectorAll('a[href*="/maps/place/"]').forEach(function (a) {
  const href = a.href;
  if (!href || seen.has(href)) return;
  seen.add(href);
  const card = a.closest('div[jsaction]') || a.parentElement;
  if (!card) return;
  const pick = function (sel) {
    const e = card.querySelector(sel);
    return e ? e.textContent.trim() : '';
  };
  const blocks = Array.prototype.slice
    .call(card.querySelectorAll('.W4Efsd'))
    .map(function (e) { return e.textContent.trim(); });
  // "4.9 stars 896 Reviews" - an aria-label, so far more stable than the
  // obfuscated class names Google rotates.
  var ratingLabel = '';
  Array.prototype.slice.call(card.querySelectorAll('[aria-label]')).some(function (e) {
    const label = e.getAttribute('aria-label') || '';
    if (/star/i.test(label)) { ratingLabel = label; return true; }
    return false;
  });
  out.push({
    href: href,
    name: pick('.qBF1Pd') || a.getAttribute('aria-label') || '',
    rating: pick('span.MW4etd'),
    ratingLabel: ratingLabel,
    blocks: blocks
  });
});
return out;
"""

_DETAIL_JS = """
const res = {phones: [], website: '', address: '', name: '', category: ''};
const nodes = document.querySelectorAll('button[data-item-id], a[data-item-id]');
Array.prototype.forEach.call(nodes, function (el) {
  const id = el.getAttribute('data-item-id') || '';
  const label = el.getAttribute('aria-label') || '';
  if (id.indexOf('phone') === 0) {
    // A listing can carry several numbers, each its own phone:tel:<number>
    // node. The id holds the canonical E.164 form; the label is prettier.
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
const cat = document.querySelector('button[jsaction*="category"]') || document.querySelector('span.DkEaL');
if (cat) { res.category = cat.textContent.trim(); }
return res;
"""


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_GEOCODE_CACHE: dict[str, tuple[float, float, float, float] | None] = {}


def geocode_bbox(place: str) -> tuple[float, float, float, float] | None:
    """Bounding box for a place name, via OpenStreetMap. Free, no key.

    Returns (south, north, west, east), or None when the place is unknown.
    """
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
    """Map a cell's width in degrees to a Google Maps zoom level.

    Maps shows roughly 360/2^z degrees across the viewport; the +3 calibrates
    for the portion of the map the results panel leaves visible, and matches
    what was measured to work for city-sized cells.
    """
    if lon_span <= 0:
        return settings.GMAPS_CELL_ZOOM
    zoom = math.log2(360.0 / lon_span) + 3
    return max(11, min(18, int(round(zoom))))


def split_cell(cell: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """Quarter a bounding box."""
    south, north, west, east = cell
    mid_lat = (south + north) / 2
    mid_lng = (west + east) / 2
    return [
        (south, mid_lat, west, mid_lng),
        (south, mid_lat, mid_lng, east),
        (mid_lat, north, west, mid_lng),
        (mid_lat, north, mid_lng, east),
    ]


def grid_cells(
    bbox: tuple[float, float, float, float], cells: int
) -> list[tuple[float, float, float, float]]:
    """Split a bounding box into a grid of sub-boxes, densest area first."""
    south, north, west, east = bbox
    per_side = max(1, int(math.ceil(math.sqrt(cells))))
    lat_step = (north - south) / per_side
    lng_step = (east - west) / per_side
    out = []
    for row in range(per_side):
        for col in range(per_side):
            out.append(
                (
                    south + lat_step * row,
                    south + lat_step * (row + 1),
                    west + lng_step * col,
                    west + lng_step * (col + 1),
                )
            )
    centre_lat = (south + north) / 2
    centre_lng = (west + east) / 2
    out.sort(
        key=lambda c: ((c[0] + c[1]) / 2 - centre_lat) ** 2
        + ((c[2] + c[3]) / 2 - centre_lng) ** 2
    )
    return out


def grid_points(
    bbox: tuple[float, float, float, float], cells: int
) -> list[tuple[float, float]]:
    """Centre points of a roughly square grid covering the bounding box."""
    south, north, west, east = bbox
    per_side = max(1, int(math.ceil(math.sqrt(cells))))
    lat_step = (north - south) / per_side
    lng_step = (east - west) / per_side
    points = []
    for row in range(per_side):
        for col in range(per_side):
            points.append(
                (south + lat_step * (row + 0.5), west + lng_step * (col + 0.5))
            )
    # Work outwards from the middle: city centres are densest, so if the run is
    # cut short by the lead limit the best areas are already covered.
    centre_lat = (south + north) / 2
    centre_lng = (west + east) / 2
    points.sort(key=lambda p: (p[0] - centre_lat) ** 2 + (p[1] - centre_lng) ** 2)
    return points


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
    return PRIVATE_USE_RE.sub("", text or "").replace(" ", " ").strip(" ·\t\n")


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

    # The feed renders "<category> - <price/icon> - <street address>" in one of
    # the .W4Efsd blocks. Pick the block that splits into the most parts and is
    # not the opening-hours line.
    best: list[str] = []
    for block in card.get("blocks", []):
        cleaned = _clean(block)
        if not cleaned or "·" not in cleaned:
            continue
        low = cleaned.lower()
        if low.startswith(("open", "closed", "closes", "opens", "temporarily", "permanently")):
            continue
        parts = [p for p in (_clean(p) for p in cleaned.split("·")) if p]
        if len(parts) >= 2 and len(parts) > len(best):
            best = parts
    if best:
        lead.category = best[0]
        lead.address = best[-1]

    return lead


class GoogleMapsSource:
    """Scrapes Google Maps search results for business leads."""

    def __init__(self, on_progress: ProgressFn | None = None, on_batch=None):
        self._on_progress = on_progress
        # Called with the running lead list so long runs can be persisted
        # incrementally instead of only at the end.
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

    # -- phase 1: discover ---------------------------------------------------

    def discover(self, keyword: str, location: str, max_leads: int) -> list[MapsLead]:
        """Search Maps and read every result card straight off the feed.

        A single Maps search stops returning new results at roughly 100-120
        entries no matter how far the feed is scrolled. When more than that is
        wanted, the area is geocoded and searched tile by tile instead, which
        returns largely distinct businesses per tile.
        """
        query = f"{keyword} in {location}".strip() if location else keyword
        driver = None
        try:
            driver = build_driver()

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
                return self._discover_tiled(driver, keyword, location, bbox, max_leads)
            return self._discover_single(driver, query, max_leads or 0)
        finally:
            quit_driver(driver)

    def _discover_single(self, driver, query: str, max_leads: int) -> list[MapsLead]:
        url = "https://www.google.com/maps/search/" + query.replace(" ", "+") + "?hl=en"
        self._progress("Searching Google Maps for: " + query, 5)
        driver.get(url)
        self._dismiss_consent(driver)
        if not self._await_feed(driver):
            self._progress("No results feed appeared for this search.", 30)
            return []
        collected: dict[str, MapsLead] = {}
        self._scroll_and_collect(driver, collected, max_leads, settings.GMAPS_SCROLL_ROUNDS)
        leads = list(collected.values())[:max_leads] if max_leads else list(collected.values())
        self._progress("Discovered %d businesses." % len(leads), 35)
        return leads

    def _discover_tiled(
        self, driver, keyword: str, location: str, bbox, max_leads: int
    ) -> list[MapsLead]:
        """Search an area cell by cell, splitting cells that look saturated.

        Google Maps stops returning new results at roughly 120 per search. A
        fixed grid gets past that, but a uniform grid is wrong for real cities:
        the centre holds far more businesses per square kilometre than the
        edges. So a cell that comes back at or above the saturation threshold
        is assumed to be hiding more, and is quartered and searched again.

        With ``max_leads`` of 0 the run is unlimited: it continues until every
        cell is exhausted, the search ceiling is hit, or the user stops it.
        """
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
        queue_cells: list[tuple[tuple[float, float, float, float], int]] = [
            (cell, 0) for cell in grid_cells(bbox, initial)
        ]

        self._progress(
            "Searching %s%s. Starting with %d map areas, splitting any that look "
            "busy - one Maps search only ever returns about 120 results."
            % (location, "" if unlimited else " for up to %d leads" % target,
               len(queue_cells)),
            5,
        )

        collected: dict[str, MapsLead] = {}
        searches = 0
        consecutive_empty = 0

        while queue_cells:
            if self._stop.is_set():
                self._progress("Stopped - keeping the %d found so far." % len(collected), 33)
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

            cell, depth = queue_cells.pop(0)
            south, north, west, east = cell
            lat, lng = (south + north) / 2, (west + east) / 2
            zoom = zoom_for_span(east - west)

            before = len(collected)
            url = (
                "https://www.google.com/maps/search/%s/@%.6f,%.6f,%dz?hl=en"
                % (keyword.replace(" ", "+"), lat, lng, zoom)
            )
            try:
                driver.get(url)
                if searches == 0:
                    self._dismiss_consent(driver)
                if self._await_feed(driver):
                    self._scroll_and_collect(
                        driver, collected, target, settings.GMAPS_CELL_SCROLLS
                    )
            except WebDriverException as exc:
                log.warning("area search failed: %s", exc)
            searches += 1

            gained = len(collected) - before
            # A busy cell is hiding more behind the per-search cap: split it.
            if (
                gained >= settings.GMAPS_SATURATION
                and depth < settings.GMAPS_MAX_DEPTH
                and (unlimited or len(collected) < target)
            ):
                queue_cells = [(c, depth + 1) for c in split_cell(cell)] + queue_cells
                note = " - busy, splitting into 4"
            else:
                note = ""

            consecutive_empty = consecutive_empty + 1 if gained == 0 else 0

            if unlimited:
                pct = min(30, 5 + int(searches / 3))
            else:
                pct = min(33, 5 + int((len(collected) / max(1, target)) * 28))
            self._progress(
                "Search %d: +%d new (%d found, %d areas queued)%s"
                % (searches, gained, len(collected), len(queue_cells), note),
                pct,
            )

            if self._on_batch and gained:
                # Hand partial results up so they are saved as we go and the
                # run can be stopped at any point without losing anything.
                try:
                    self._on_batch(list(collected.values()))
                except Exception:  # noqa: BLE001
                    log.debug("batch callback failed", exc_info=True)

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

    def _await_feed(self, driver) -> bool:
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="feed"]'))
            )
            return True
        except TimeoutException:
            return False

    def _dismiss_consent(self, driver) -> None:
        try:
            driver.find_element(
                By.XPATH,
                "//button[contains(., 'Accept all') or contains(., 'Reject all') or contains(., 'I agree')]",
            ).click()
            time.sleep(1.0)
        except WebDriverException:
            pass

    def _scroll_and_collect(
        self,
        driver,
        collected: dict[str, MapsLead],
        max_leads: int,
        max_rounds: int,
    ) -> None:
        """Scroll the current feed, adding what it finds to ``collected``."""
        try:
            feed = driver.find_element(By.CSS_SELECTOR, 'div[role="feed"]')
        except WebDriverException:
            return

        stagnant = 0
        last_count = len(collected)

        for round_no in range(max_rounds):
            if self._stop.is_set():
                return

            try:
                cards = driver.execute_script(_FEED_JS) or []
            except WebDriverException as exc:
                log.warning("feed read failed: %s", exc)
                return

            for card in cards:
                lead = _parse_card(card)
                if lead is None:
                    continue
                key = lead.place_id or lead.source_url or lead.business_name.lower()
                collected.setdefault(key, lead)

            if max_leads and len(collected) >= max_leads:
                return

            try:
                feed_text = (feed.get_attribute("innerText") or "").lower()
                if "reached the end of the list" in feed_text:
                    return
            except WebDriverException:
                pass

            if len(collected) == last_count:
                stagnant += 1
                if stagnant >= 4:
                    return
            else:
                stagnant = 0
                last_count = len(collected)
                if max_rounds >= settings.GMAPS_SCROLL_ROUNDS:
                    self._progress(
                        "Loading results... %d found" % len(collected),
                        min(33, 6 + round_no),
                    )

            try:
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight", feed
                )
            except WebDriverException:
                return
            time.sleep(0.55)

    # -- phase 2: details ----------------------------------------------------

    def fetch_details(self, leads: list[MapsLead], workers: int | None = None) -> None:
        """Fill in phone and canonical website using a pool of browsers.

        Mutates ``leads`` in place. Each worker owns one browser for its whole
        run, so Chrome's start-up cost is paid ``workers`` times rather than
        once per business.
        """
        pending = [ld for ld in leads if ld.source_url]
        if not pending:
            return

        worker_count = max(1, min(workers or settings.GMAPS_DETAIL_WORKERS, len(pending)))
        task_q: queue.Queue = queue.Queue()
        for lead in pending:
            task_q.put(lead)

        total = len(pending)
        finished = threading.Semaphore(0)
        counter = {"n": 0}
        counter_lock = threading.Lock()

        self._progress(
            "Reading contact details for %d businesses (%d parallel browsers)..."
            % (total, worker_count),
            36,
        )

        def worker() -> None:
            driver = None
            try:
                driver = build_driver()
            except Exception as exc:  # noqa: BLE001
                log.error("detail worker could not start a browser: %s", exc)
                finished.release()
                return
            try:
                while not self._stop.is_set():
                    try:
                        lead = task_q.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        self._extract_detail(driver, lead)
                    except WebDriverException as exc:
                        log.debug("detail failed for %s: %s", lead.business_name, exc)
                        quit_driver(driver)
                        try:
                            driver = build_driver()
                        except Exception:  # noqa: BLE001
                            return
                    finally:
                        with counter_lock:
                            counter["n"] += 1
                            done_n = counter["n"]
                        if done_n % 5 == 0 or done_n == total:
                            pct = 36 + int((done_n / total) * 29)
                            self._progress("Contact details %d/%d" % (done_n, total), pct)
            finally:
                quit_driver(driver)
                finished.release()

        threads = [
            threading.Thread(target=worker, daemon=True, name="gmaps-detail-%d" % i)
            for i in range(worker_count)
        ]
        for t in threads:
            t.start()
        for _ in threads:
            finished.acquire()

        self._progress("Contact details complete for %d businesses." % counter["n"], 65)

    def _extract_detail(self, driver, lead: MapsLead) -> None:
        driver.get(lead.source_url)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-item-id], h1"))
            )
        except TimeoutException:
            return

        data = driver.execute_script(_DETAIL_JS) or {}

        found = [_clean(p) for p in (data.get("phones") or [])]
        found = [p for p in found if p]
        if found:
            lead.phones = found
            lead.phone = found[0]
        if data.get("website"):
            lead.website = data["website"].strip()
        if data.get("address"):
            lead.address = _clean(data["address"])
        if data.get("category") and not lead.category:
            lead.category = _clean(data["category"])
        if data.get("name") and not lead.business_name:
            lead.business_name = _clean(data["name"])

    # -- orchestration -------------------------------------------------------

    def run(
        self, keyword: str, location: str, max_leads: int, skip_details: bool = False
    ) -> list[MapsLead]:
        leads = self.discover(keyword, location, max_leads)
        if leads and not skip_details and not self._stop.is_set():
            self.fetch_details(leads)
        return leads


def lead_to_dict(lead: MapsLead) -> dict:
    return asdict(lead)
