from __future__ import annotations

from typing import Callable

from geo.quadtree import BoundingBox
from scraper import GoogleMapsScraper, clean_leads
from utils.deduplicator import deduplicate


JobInput = dict
ProgressCallback = Callable[[str, int, dict | None], None]
ShouldStopCallback = Callable[[], bool]


def run_scraper_job(
    payload: JobInput,
    progress_callback: ProgressCallback | None = None,
    should_stop: ShouldStopCallback | None = None,
) -> dict:
    """
    Stateless worker entry point for Google Maps scraping.

    Expected payload schema:
    {
      "job_id": str,
      "keyword": str,
      "place": str,
      "geo_cell": {
        "lat": float,
        "lng": float,
        "zoom": int
      },
      "map_selection": {
        "center": {"lat": float, "lng": float},
        "bounds": {"north": float, "south": float, "east": float, "west": float}
      }
    }

    Returns structured JSON only (no CSV side effects):
    {
      "job_id": str,
      "keyword": str,
      "place": str,
      "geo_cell": dict,
      "status": "COMPLETED"|"PARTIAL",
      "leads": list[dict],
      "lead_count": int,
      "area_stats": dict
    }
    """
    job_id = str(payload.get("job_id", ""))
    keyword = str(payload.get("keyword", "")).strip()
    geo_cell = payload.get("geo_cell") if isinstance(payload.get("geo_cell"), dict) else {}
    map_selection = payload.get("map_selection") if isinstance(payload.get("map_selection"), dict) else None

    lat = geo_cell.get("lat")
    lng = geo_cell.get("lng")
    geo_cell_bounds = payload.get("geo_cell_bounds") if isinstance(payload.get("geo_cell_bounds"), dict) else None
    forced_geo_cells: list[BoundingBox] | None = None

    if geo_cell_bounds:
        try:
            forced_geo_cells = [
                BoundingBox(
                    min_lat=float(geo_cell_bounds.get("min_lat")),
                    max_lat=float(geo_cell_bounds.get("max_lat")),
                    min_lng=float(geo_cell_bounds.get("min_lng")),
                    max_lng=float(geo_cell_bounds.get("max_lng")),
                )
            ]
        except (TypeError, ValueError):
            forced_geo_cells = None

    place = str(payload.get("place", "")).strip()
    if not place and lat is not None and lng is not None:
        place = f"{lat}, {lng}"

    scraper = GoogleMapsScraper(headless=True)
    last_reported_results_count = -1

    def _progress(message: str, percent: int):
        nonlocal last_reported_results_count
        if should_stop and should_stop():
            scraper.stop()

        snapshot: dict = {}
        area_stats = scraper.area_stats
        partial_leads = scraper.get_partial_leads()
        partial_count = len(partial_leads)

        snapshot["area_stats"] = area_stats
        snapshot["results_count"] = partial_count
        snapshot["total_cells"] = int(
            area_stats.get("geo_cells_total")
            or area_stats.get("total_areas")
            or 1
        )
        snapshot["completed_cells"] = int(
            area_stats.get("geo_cells_completed")
            or area_stats.get("completed_areas")
            or 0
        )
        snapshot["lead_count"] = partial_count

        if partial_count != last_reported_results_count:
            snapshot["results"] = partial_leads
            last_reported_results_count = partial_count

        if progress_callback:
            progress_callback(message, percent, snapshot)

    scraper.set_progress_callback(_progress)

    try:
        # Pass map_selection through to scraper for proper bounds targeting
        raw_leads = scraper.scrape(
            keyword,
            place,
            map_selection=map_selection,
            forced_geo_cells=forced_geo_cells,
            force_primary_keyword_only=bool(forced_geo_cells),
        )
        cleaned = clean_leads(raw_leads)

        # Final deduplication pass
        cleaned = deduplicate(cleaned)

        status = "PARTIAL" if should_stop and should_stop() else "COMPLETED"

        return {
            "job_id": job_id,
            "keyword": keyword,
            "place": place,
            "geo_cell": geo_cell,
            "status": status,
            "leads": cleaned,
            "lead_count": len(cleaned),
            "area_stats": scraper.area_stats,
        }
    finally:
        scraper._close_driver()
