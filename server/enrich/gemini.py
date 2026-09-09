"""Gemini, used only to draft outreach copy.

Contact details are deliberately not sourced from a model. Writing a cold email
is generative and has no ground truth to get wrong; finding a business's email
address is a lookup, and a model with no live web access can only invent one.
Email discovery therefore lives in ``server/enrich/emails.py``, which reads the
business's own website.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from .. import settings

log = logging.getLogger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _extract_text(payload: dict) -> str:
    chunks = []
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks)


async def generate_email_copy(
    lead: dict, sender: dict, outreach_type: str
) -> tuple[str, str] | None:
    """Ask Gemini for a personalised cold email. Returns (subject, body)."""
    if not settings.GEMINI_API_KEY:
        return None

    prompt = f"""Write a short, professional cold outreach email.

Sender: {sender.get('name', '')} from {sender.get('company', '')}
Sender website: {sender.get('website', '')}
What the sender offers: {sender.get('services', '')}
Sender type: {outreach_type}

Recipient business: {lead.get('business_name', '')}
Their industry: {lead.get('category') or lead.get('keyword', '')}
Their location: {lead.get('address') or lead.get('location', '')}
Their website: {lead.get('website', '')}
Contact name (may be empty): {lead.get('owner_name', '')}

Requirements:
- Under 130 words in the body.
- Open with something specific to the recipient's business, not a generic compliment.
- One clear call to action.
- No placeholders, no square brackets, no "[Your Name]".
- If the contact name is empty, open with "Hi there,".
- Sign off as the sender.

Return ONLY JSON: {{"subject": "...", "body": "..."}}"""

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": settings.GEMINI_MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        },
    }
    url = ENDPOINT.format(model=settings.GEMINI_MODEL)
    try:
        async with httpx.AsyncClient(timeout=settings.GEMINI_TIMEOUT) as client:
            resp = await client.post(url, params={"key": settings.GEMINI_API_KEY}, json=body)
            if resp.status_code != 200:
                log.warning("gemini email generation failed: %s", resp.status_code)
                return None
            text = _extract_text(resp.json())
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("gemini email generation error: %s", exc)
        return None

    raw = text.strip()
    fenced = _JSON_BLOCK_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    subject, message = data.get("subject"), data.get("body")
    if isinstance(subject, str) and isinstance(message, str) and subject and message:
        return subject.strip(), message.strip()
    return None
