"""
Keyword Expansion Engine for Lead Generation
=============================================
Maps business categories → related search terms so that scrapers
can cast a wider net.  Used by Google Maps and Web Crawler.

Usage:
    from utils.keyword_expander import expand_keywords
    terms = expand_keywords("restaurants", max_variants=5)
    # → ["restaurants", "restaurant", "dining", "cafe", "food"]
"""

from __future__ import annotations

import re

# Comprehensive synonym map — lowercase keys
_SYNONYM_MAP: dict[str, list[str]] = {
    # Food & Beverage
    "restaurant": ["restaurants", "dining", "food", "cafe", "eatery", "bistro", "grill"],
    "restaurants": ["restaurant", "dining", "food", "cafe", "eatery", "bistro"],
    "cafe": ["coffee shop", "coffee", "bakery", "tea house", "cafe"],
    "coffee": ["coffee shop", "cafe", "espresso", "barista"],
    "bar": ["pub", "lounge", "nightclub", "tavern", "brewery"],
    "bakery": ["pastry", "cake shop", "confectionery", "patisserie"],
    "fast food": ["burger", "pizza", "takeaway", "drive-through"],
    "catering": ["event catering", "food catering", "banquet"],

    # Real Estate
    "real estate": ["property", "realtor", "broker", "homes", "realty", "housing"],
    "property": ["real estate", "properties", "housing", "apartments", "villas"],
    "realtor": ["real estate agent", "property agent", "broker"],

    # Technology
    "technology": ["tech", "IT", "software", "developer", "startup"],
    "software": ["SaaS", "app development", "web development", "technology"],
    "IT": ["information technology", "tech support", "IT services"],

    # Health & Medical
    "doctor": ["physician", "clinic", "medical", "healthcare", "hospital"],
    "dentist": ["dental", "dental clinic", "orthodontist", "dental care"],
    "hospital": ["medical center", "clinic", "healthcare", "health center"],
    "pharmacy": ["drugstore", "chemist", "medical store"],
    "gym": ["fitness", "workout", "training", "health club", "fitness center"],
    "yoga": ["yoga studio", "meditation", "wellness", "pilates"],
    "spa": ["wellness", "massage", "beauty spa", "relaxation"],

    # Professional Services
    "lawyer": ["attorney", "law firm", "legal", "advocate", "solicitor"],
    "accountant": ["accounting", "tax", "CPA", "bookkeeping", "audit"],
    "consultant": ["consulting", "advisory", "strategy", "management consulting"],
    "marketing": ["digital marketing", "advertising", "branding", "SEO", "agency"],
    "insurance": ["insurance agent", "insurance broker", "coverage", "policy"],

    # Home Services
    "plumber": ["plumbing", "plumbing services", "pipe repair", "drain"],
    "electrician": ["electrical", "electrical services", "wiring", "power"],
    "contractor": ["construction", "builder", "renovation", "remodeling"],
    "cleaning": ["cleaning services", "maid", "janitorial", "housekeeping"],
    "painter": ["painting", "house painting", "wall painting"],
    "landscaping": ["lawn care", "garden", "gardener", "yard maintenance"],
    "hvac": ["air conditioning", "heating", "ventilation", "AC repair"],
    "roofing": ["roof repair", "roofer", "roof installation"],
    "pest control": ["exterminator", "pest removal", "fumigation"],
    "locksmith": ["lock repair", "key cutting", "security locks"],
    "moving": ["movers", "relocation", "moving company", "packers"],

    # Automotive
    "auto repair": ["car repair", "mechanic", "garage", "auto shop"],
    "car dealer": ["car dealership", "auto dealer", "vehicle sales"],
    "car wash": ["auto wash", "car detailing", "vehicle cleaning"],
    "towing": ["tow truck", "roadside assistance", "vehicle towing"],

    # Education
    "school": ["academy", "institute", "college", "educational"],
    "tutor": ["tutoring", "coaching", "private lessons", "teaching"],
    "training": ["courses", "workshops", "certification", "classes"],

    # Beauty
    "salon": ["hair salon", "beauty salon", "hairdresser", "barber"],
    "beauty": ["cosmetics", "skincare", "makeup", "aesthetics"],
    "nail": ["nail salon", "manicure", "pedicure", "nail art"],

    # Photography
    "photographer": ["photography", "photo studio", "wedding photographer"],
    "videographer": ["videography", "video production", "cinematography"],

    # Pet Services
    "veterinarian": ["vet", "animal clinic", "pet hospital", "animal doctor"],
    "pet": ["pet store", "pet shop", "pet grooming", "pet care"],

    # Travel & Hospitality
    "hotel": ["resort", "motel", "inn", "lodge", "accommodation"],
    "travel": ["travel agency", "tour", "tourism", "vacation", "holiday"],

    # Finance
    "bank": ["banking", "financial", "credit union", "finance"],
    "investment": ["wealth management", "financial advisor", "portfolio"],

    # Retail
    "store": ["shop", "retail", "boutique", "outlet", "mart"],
    "supermarket": ["grocery", "grocery store", "hypermarket", "food store"],
    "clothing": ["apparel", "fashion", "garments", "wear"],
    "jewelry": ["jeweler", "gems", "watches", "accessories"],
    "electronics": ["gadgets", "appliances", "tech store", "computer store"],
    "furniture": ["home decor", "interior", "furnishing", "home goods"],

    # Entertainment
    "event": ["event planning", "event management", "party planning"],
    "dj": ["music", "entertainment", "live music", "sound"],
    "wedding": ["wedding planner", "bridal", "marriage", "ceremony"],

    # Logistics
    "logistics": ["shipping", "freight", "cargo", "delivery", "courier"],
    "warehouse": ["storage", "distribution", "fulfillment"],

    # Construction
    "construction": ["building", "contractor", "builder", "civil engineering"],
    "architect": ["architecture", "architectural firm", "design studio"],
    "interior design": ["interior designer", "home design", "decor"],
}


def expand_keywords(keyword: str, max_variants: int = 6) -> list[str]:
    """
    Expand a keyword into related search terms.

    Args:
        keyword: The original search term
        max_variants: Maximum number of variants to return (including original)

    Returns:
        List of search terms, always starting with the original keyword
    """
    kw_lower = keyword.strip().lower()
    if not kw_lower:
        return [keyword]

    variants: list[str] = [keyword]
    seen: set[str] = {kw_lower}

    # Direct match
    synonyms = _SYNONYM_MAP.get(kw_lower, [])

    # Partial match — check if any key is contained in the keyword or vice versa
    if not synonyms:
        for key, syns in _SYNONYM_MAP.items():
            if key in kw_lower or kw_lower in key:
                synonyms = syns
                break

    # Plural/singular fallback
    if not synonyms:
        if kw_lower.endswith("s"):
            synonyms = _SYNONYM_MAP.get(kw_lower[:-1], [])
        else:
            synonyms = _SYNONYM_MAP.get(kw_lower + "s", [])

    for syn in synonyms:
        if len(variants) >= max_variants:
            break
        syn_lower = syn.strip().lower()
        if syn_lower not in seen and syn_lower != kw_lower:
            seen.add(syn_lower)
            variants.append(syn)

    return variants
