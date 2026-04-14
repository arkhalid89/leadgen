"""
Geo-Partitioning Engine for Lead Generation
=============================================
Provides adaptive spatial partitioning using quadtree algorithm
and geocoding via Nominatim (OpenStreetMap).

Key capabilities:
  - Convert city names → bounding boxes via Nominatim
  - Convert frontend map selections → bounding boxes
  - Adaptive quadtree subdivision based on result density
  - Zoom-level calculation for Google Maps viewport targeting
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Callable

import requests

logger = logging.getLogger(__name__)

THRESHOLD = 100  # Results threshold to trigger subdivision
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_HEADERS = {
    "User-Agent": "LeadGen/1.0 (lead generation tool)",
    "Accept": "application/json",
}

# Cache geocoding results in-memory to avoid repeated API calls
_geocode_cache: dict[str, "BoundingBox | None"] = {}


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned bounding box in lat/lng coordinates."""
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float

    def center(self) -> tuple[float, float]:
        return (
            (self.min_lat + self.max_lat) / 2.0,
            (self.min_lng + self.max_lng) / 2.0,
        )

    def lat_span(self) -> float:
        return abs(self.max_lat - self.min_lat)

    def lng_span(self) -> float:
        return abs(self.max_lng - self.min_lng)

    def area_sq_degrees(self) -> float:
        return self.lat_span() * self.lng_span()

    def pad(self, factor: float = 0.05) -> "BoundingBox":
        """Expand the box by a percentage factor on each side."""
        lat_pad = self.lat_span() * factor
        lng_pad = self.lng_span() * factor
        return BoundingBox(
            min_lat=self.min_lat - lat_pad,
            max_lat=self.max_lat + lat_pad,
            min_lng=self.min_lng - lng_pad,
            max_lng=self.max_lng + lng_pad,
        )


def subdivide(box: BoundingBox) -> list[BoundingBox]:
    """Split a bounding box into 4 quadrants."""
    mid_lat, mid_lng = box.center()
    return [
        BoundingBox(mid_lat, box.max_lat, box.min_lng, mid_lng),  # NW
        BoundingBox(mid_lat, box.max_lat, mid_lng, box.max_lng),  # NE
        BoundingBox(box.min_lat, mid_lat, box.min_lng, mid_lng),  # SW
        BoundingBox(box.min_lat, mid_lat, mid_lng, box.max_lng),  # SE
    ]


def should_subdivide(results_count: int, threshold: int = THRESHOLD) -> bool:
    return int(results_count) >= int(threshold)


def build_cells(
    box: BoundingBox,
    probe_count_fn: Callable[[BoundingBox], int] | None = None,
    max_depth: int = 4,
    depth: int = 0,
) -> list[BoundingBox]:
    """
    Adaptive quadtree partitioning.

    If probe_count_fn is provided:
      - Runs a lightweight probe per cell
      - Subdivides only when probe count reaches threshold
      - Stops at max_depth

    If probe_count_fn is None:
      - Uses area-based heuristic to decide subdivision depth
      - Larger areas get more subdivisions automatically
    """
    if probe_count_fn is not None:
        # Probe-driven mode
        results_count = int(probe_count_fn(box))
        if depth >= max_depth or not should_subdivide(results_count):
            return [box]
        cells: list[BoundingBox] = []
        for child in subdivide(box):
            cells.extend(
                build_cells(child, probe_count_fn=probe_count_fn,
                            max_depth=max_depth, depth=depth + 1)
            )
        return cells
    else:
        # Area-based heuristic mode (no probing needed)
        # Large cities (~0.1 sq degrees or more) get subdivided deeper
        area = box.area_sq_degrees()
        if depth >= max_depth or area < 0.001:
            return [box]

        # For areas > ~0.01 sq degrees, always subdivide (that's roughly 1km x 1km)
        if area > 0.01:
            cells = []
            for child in subdivide(box):
                cells.extend(
                    build_cells(child, probe_count_fn=None,
                                max_depth=max_depth, depth=depth + 1)
                )
            return cells
        else:
            return [box]


def build_cells_for_area(
    box: BoundingBox,
    target_cell_count: int = 16,
) -> list[BoundingBox]:
    """
    Build geo cells for an area with a target number of cells.

    Automatically determines the right subdivision depth based on
    the area size and target cell count.
    """
    area = box.area_sq_degrees()

    # Determine depth: 4^depth = cell_count
    if target_cell_count <= 1:
        return [box]
    depth = max(1, min(4, int(math.log(target_cell_count, 4)) + 1))

    # For very small areas, limit depth
    if area < 0.0001:
        depth = min(depth, 1)
    elif area < 0.001:
        depth = min(depth, 2)
    elif area < 0.01:
        depth = min(depth, 3)

    return build_cells(box, probe_count_fn=None, max_depth=depth)


def zoom_for_bbox(box: BoundingBox) -> int:
    """
    Calculate appropriate Google Maps zoom level for a bounding box.

    Google Maps zoom levels:
      - 10 = city-wide (~50km)
      - 12 = neighborhood (~10km)
      - 14 = streets (~2km)
      - 16 = blocks (~500m)
      - 18 = buildings (~100m)
    """
    lat_span = box.lat_span()
    lng_span = box.lng_span()
    span = max(lat_span, lng_span)

    if span > 0.5:
        return 10
    elif span > 0.2:
        return 11
    elif span > 0.1:
        return 12
    elif span > 0.05:
        return 13
    elif span > 0.02:
        return 14
    elif span > 0.01:
        return 15
    elif span > 0.005:
        return 16
    elif span > 0.002:
        return 17
    else:
        return 18


def bbox_from_place(place_name: str) -> BoundingBox | None:
    """
    Geocode a place name to a bounding box using Nominatim.

    Returns None if geocoding fails.
    Results are cached in memory.
    """
    cache_key = place_name.strip().lower()
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={
                "q": place_name,
                "format": "json",
                "limit": "1",
                "addressdetails": "0",
            },
            headers=_NOMINATIM_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("Nominatim returned %d for '%s'", resp.status_code, place_name)
            _geocode_cache[cache_key] = None
            return None

        results = resp.json()
        if not results:
            logger.warning("No geocoding results for '%s'", place_name)
            _geocode_cache[cache_key] = None
            return None

        item = results[0]
        # Nominatim returns boundingbox as [south_lat, north_lat, west_lng, east_lng]
        bb = item.get("boundingbox")
        if bb and len(bb) == 4:
            box = BoundingBox(
                min_lat=float(bb[0]),
                max_lat=float(bb[1]),
                min_lng=float(bb[2]),
                max_lng=float(bb[3]),
            )
            # Pad slightly to ensure edge areas are covered
            box = box.pad(0.02)
            _geocode_cache[cache_key] = box
            logger.info(
                "Geocoded '%s' → bbox(%.4f,%.4f,%.4f,%.4f)",
                place_name, box.min_lat, box.max_lat, box.min_lng, box.max_lng,
            )
            return box

        # Fallback: use lat/lon with a default radius
        lat = float(item.get("lat", 0))
        lon = float(item.get("lon", 0))
        if lat and lon:
            # Create a ~10km box around the point
            box = BoundingBox(
                min_lat=lat - 0.05,
                max_lat=lat + 0.05,
                min_lng=lon - 0.05,
                max_lng=lon + 0.05,
            )
            _geocode_cache[cache_key] = box
            return box

        _geocode_cache[cache_key] = None
        return None

    except Exception as e:
        logger.error("Geocoding error for '%s': %s", place_name, e)
        _geocode_cache[cache_key] = None
        return None


def bbox_from_map_selection(bounds: dict) -> BoundingBox | None:
    """
    Create a BoundingBox from frontend map selection bounds.

    Expected format:
    {
        "north": float,
        "south": float,
        "east": float,
        "west": float
    }
    """
    try:
        north = float(bounds.get("north", 0))
        south = float(bounds.get("south", 0))
        east = float(bounds.get("east", 0))
        west = float(bounds.get("west", 0))

        if north == 0 and south == 0 and east == 0 and west == 0:
            return None

        return BoundingBox(
            min_lat=min(south, north),
            max_lat=max(south, north),
            min_lng=min(west, east),
            max_lng=max(west, east),
        )
    except (TypeError, ValueError) as e:
        logger.error("Invalid map selection bounds: %s", e)
        return None


def bbox_from_coordinates(lat: float, lng: float, radius_km: float = 5.0) -> BoundingBox:
    """
    Create a BoundingBox from a center point and radius.

    Args:
        lat: Center latitude
        lng: Center longitude
        radius_km: Radius in kilometers (default 5km)
    """
    # 1 degree of latitude ≈ 111km
    lat_delta = radius_km / 111.0
    # 1 degree of longitude varies by latitude
    lng_delta = radius_km / (111.0 * math.cos(math.radians(lat)))

    return BoundingBox(
        min_lat=lat - lat_delta,
        max_lat=lat + lat_delta,
        min_lng=lng - lng_delta,
        max_lng=lng + lng_delta,
    )
