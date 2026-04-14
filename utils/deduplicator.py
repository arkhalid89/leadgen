"""
Deduplication Engine for Lead Generation
=========================================
Provides exact hash and fuzzy matching to deduplicate leads
collected from multiple geo cells, keyword variants, and
search queries.

Methods:
  - Exact hash: name + lat/lng combo
  - Fuzzy name: Levenshtein-like similarity with article stripping
  - Phone match: same phone = same business
  - Address match: normalized address comparison
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher


# Articles and common prefixes to strip for name comparison
_STRIP_PREFIXES = re.compile(
    r'^(?:the|a|an|le|la|el|al|das|die|der)\s+',
    re.I,
)

# Normalize address: remove punctuation, multiple spaces, common abbreviations
_ADDR_NORMALIZE = {
    "street": "st", "avenue": "ave", "boulevard": "blvd",
    "drive": "dr", "road": "rd", "lane": "ln",
    "court": "ct", "place": "pl", "suite": "ste",
    "building": "bldg", "floor": "fl", "apartment": "apt",
}


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _norm_name(name: str) -> str:
    """Normalize business name for comparison (strip articles, punctuation)."""
    n = _norm(name)
    # Strip common articles
    n = _STRIP_PREFIXES.sub("", n)
    # Remove punctuation
    n = re.sub(r'[^\w\s]', '', n)
    # Collapse whitespace
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def _norm_address(address: str) -> str:
    """Normalize address for comparison."""
    a = _norm(address)
    if not a or a == "n/a":
        return ""
    # Replace common abbreviations
    for full, abbr in _ADDR_NORMALIZE.items():
        a = re.sub(r'\b' + full + r'\b', abbr, a)
    # Remove punctuation
    a = re.sub(r'[^\w\s]', '', a)
    # Collapse whitespace
    a = re.sub(r'\s+', ' ', a).strip()
    return a


def _norm_phone(phone: str) -> str:
    """Normalize phone for comparison — digits only."""
    p = _norm(phone)
    if not p or p == "n/a":
        return ""
    # Keep only digits
    digits = re.sub(r'\D', '', p)
    # Strip leading country codes (1 for US, 91 for India, etc.)
    if len(digits) > 10:
        # Try stripping common country codes
        for prefix_len in [1, 2, 3]:
            if len(digits) - prefix_len >= 7:
                return digits[prefix_len:]
    return digits


def exact_hash(lead: dict) -> str:
    """Generate a hash for exact deduplication based on name + coordinates."""
    key = f"{_norm_name(lead.get('business_name', ''))}|{lead.get('latitude', '')}|{lead.get('longitude', '')}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def fuzzy_same(a: dict, b: dict, name_threshold: float = 0.85) -> bool:
    """
    Check if two leads represent the same business using multiple signals.

    Returns True if any of:
      - Same phone number (digits only)
      - Same normalized address
      - Similar business name (above threshold)
    """
    # Phone match (strongest signal)
    phone_a = _norm_phone(a.get("phone", ""))
    phone_b = _norm_phone(b.get("phone", ""))
    if phone_a and phone_b and len(phone_a) >= 7 and phone_a == phone_b:
        return True

    # Name comparison
    name_a = _norm_name(a.get("business_name", ""))
    name_b = _norm_name(b.get("business_name", ""))
    if not name_a or not name_b:
        return False

    # Exact name match after normalization
    if name_a == name_b:
        return True

    # Fuzzy name match
    name_score = SequenceMatcher(None, name_a, name_b).ratio()
    if name_score >= name_threshold:
        return True

    # Address match (if names are somewhat similar)
    if name_score >= 0.6:
        addr_a = _norm_address(a.get("address", ""))
        addr_b = _norm_address(b.get("address", ""))
        if addr_a and addr_b and addr_a == addr_b:
            return True

    return False


def deduplicate(leads: list[dict], name_threshold: float = 0.85) -> list[dict]:
    """
    Remove duplicate leads using exact hash + fuzzy matching.

    The fuzzy check is O(n²) worst case but uses early exit and
    is bounded by the typical lead count (~hundreds, not thousands).
    """
    if not leads:
        return []

    seen_hashes: set[str] = set()
    unique: list[dict] = []

    for lead in leads:
        # Skip obviously empty leads
        name = (lead.get("business_name") or "").strip()
        if not name or name.lower() == "unknown":
            continue

        # Exact hash check
        h = exact_hash(lead)
        if h in seen_hashes:
            continue

        # Fuzzy check against existing unique leads
        is_duplicate = False
        for existing in unique:
            if fuzzy_same(lead, existing, name_threshold=name_threshold):
                is_duplicate = True
                break

        if is_duplicate:
            continue

        seen_hashes.add(h)
        unique.append(lead)

    return unique
