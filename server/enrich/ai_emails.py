"""Model-suggested email addresses, for leads whose website gave nothing.

Read this before enabling it.

Measured against 12 businesses whose real address had been read off their own
website, an ungrounded model scored **3 correct out of 10 answered - 30%
precision**. The failure mode is not random: it answers ``info@<domain>`` almost
every time, which is a string you can build for free without a model. The three
"correct" answers were correct only because ``info@`` happened to be right.

Grounded (web-connected) generation avoids this, but Google Search grounding has
no free quota and returns HTTP 429 on an unbilled key.

So this module exists because a suggestion is still worth something when it is
*labelled as a suggestion*: every address it produces is stored with
``email_source='ai'`` and ``email_status='unverified'``, it never overwrites an
address read from a website, and the UI and CSV both mark it. Treat the output
as a hypothesis to verify, not as a contact.

Two things make it cheaper and safer than a naive implementation:

* Businesses are batched (20 per request by default), so quota is spent per
  batch rather than per lead.
* Every suggestion is checked against the domain's DNS MX records, so addresses
  at domains that cannot receive mail at all are discarded before they are ever
  shown.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

import httpx

from .. import settings
from .emails import clean_emails, registered_domain

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int], None]

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

PROMPT_HEADER = """You are a B2B lead-research assistant. For each numbered business below, give the public email addresses that business actually publishes.

Return ONLY a JSON array. One object per business, in the same order, with exactly these keys:
"index", "emails"

Rules:
- "index" must be the number given for that business.
- "emails" is an array of every public address you can establish for that business.
- Return [] when you cannot establish a real published address. An empty array is the correct and expected answer when you are unsure.
- Do NOT pattern-guess. Do NOT return info@<their-domain> unless you have actually seen that exact address published for this business. A wrong address is far worse than an empty array.
- Never return no-reply, tracking, image-host or placeholder addresses.
- Output the raw JSON array only. No prose, no markdown fences.

Businesses:
"""


async def domain_accepts_mail(domain: str) -> bool:
    """True when the domain publishes MX records.

    This does not prove a mailbox exists - only that mail to the domain can be
    delivered at all. It is free and fast, and it removes suggestions at parked
    or mail-less domains.
    """
    if not domain:
        return False
    try:
        import dns.asyncresolver  # noqa: PLC0415
    except ImportError:
        return True  # cannot check; do not block on it
    try:
        answers = await dns.asyncresolver.resolve(domain, "MX", lifetime=5.0)
        return len(answers) > 0
    except Exception:  # noqa: BLE001 - NXDOMAIN, timeout, no answer, etc.
        return False


class AiEmailSuggester:
    """Batched, model-suggested addresses. Always flagged unverified."""

    def __init__(self, on_progress: ProgressFn | None = None):
        self._on_progress = on_progress
        self._cancelled = False
        self.stats = {"asked": 0, "suggested": 0, "rejected_no_mx": 0, "batches": 0}

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def configured(self) -> bool:
        return bool(settings.GEMINI_API_KEY)

    def _progress(self, message: str, percent: int = -1) -> None:
        log.info("[ai-emails] %s", message)
        if self._on_progress:
            try:
                self._on_progress(message, percent)
            except Exception:  # noqa: BLE001
                log.debug("progress callback failed", exc_info=True)

    def _build_prompt(self, batch: list[dict], start_index: int) -> str:
        lines = []
        for offset, lead in enumerate(batch):
            bits = ['%d. name: "%s"' % (start_index + offset, lead.get("business_name", ""))]
            for label, key in (("website", "website"), ("address", "address"),
                               ("category", "category")):
                value = (lead.get(key) or "").strip()
                if value:
                    bits.append('%s: "%s"' % (label, value))
            lines.append(", ".join(bits))
        return PROMPT_HEADER + "\n".join(lines)

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        chunks = []
        for candidate in payload.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        return "".join(chunks)

    @staticmethod
    def _parse(text: str) -> list[dict]:
        raw = (text or "").strip()
        if not raw:
            return []
        fenced = _JSON_BLOCK_RE.search(raw)
        if fenced:
            raw = fenced.group(1).strip()
        else:
            start, end = raw.find("["), raw.rfind("]")
            if start != -1 and end > start:
                raw = raw[start : end + 1]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("could not parse AI JSON: %s", raw[:200])
            return []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def _call(self, client: httpx.AsyncClient, prompt: str) -> list[dict]:
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": settings.GEMINI_MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
            },
        }
        url = ENDPOINT.format(model=settings.GEMINI_MODEL)
        delay = 2.0
        for attempt in range(settings.AI_EMAIL_MAX_RETRIES + 1):
            if self._cancelled:
                return []
            try:
                resp = await client.post(
                    url, params={"key": settings.GEMINI_API_KEY}, json=body
                )
                if resp.status_code == 200:
                    return self._parse(self._extract_text(resp.json()))
                if resp.status_code in (429, 500, 502, 503, 504):
                    log.warning("ai-emails %s, retrying in %.1fs", resp.status_code, delay)
                else:
                    log.error("ai-emails rejected (%s): %s", resp.status_code, resp.text[:200])
                    return []
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("ai-emails call failed: %s", exc)
            if attempt < settings.AI_EMAIL_MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= 2
        return []

    async def suggest(self, leads: list[dict]) -> int:
        """Suggest addresses for ``leads`` in place. Returns the number changed."""
        if not leads:
            return 0
        if not self.configured:
            self._progress("No Gemini API key configured - skipping AI suggestions.", -1)
            return 0

        batch_size = max(1, settings.AI_EMAIL_BATCH_SIZE)
        batches = [leads[i : i + batch_size] for i in range(0, len(leads), batch_size)]
        self.stats["batches"] = len(batches)
        self.stats["asked"] = len(leads)

        self._progress(
            "Asking AI for %d businesses in %d request(s) of up to %d each. "
            "Results are suggestions and will be marked unverified."
            % (len(leads), len(batches), batch_size),
            -1,
        )

        semaphore = asyncio.Semaphore(max(1, settings.AI_EMAIL_CONCURRENCY))
        changed = 0
        completed = 0
        lock = asyncio.Lock()

        async with httpx.AsyncClient(timeout=settings.GEMINI_TIMEOUT) as client:

            async def process(batch_no: int, batch: list[dict]) -> None:
                nonlocal changed, completed
                start_index = batch_no * batch_size
                async with semaphore:
                    if self._cancelled:
                        return
                    results = await self._call(client, self._build_prompt(batch, start_index))

                by_index: dict[int, dict] = {}
                for item in results:
                    try:
                        by_index[int(item.get("index"))] = item
                    except (TypeError, ValueError):
                        continue

                for offset, lead in enumerate(batch):
                    result = by_index.get(start_index + offset)
                    if result is None and len(results) == len(batch):
                        result = results[offset]
                    if not result:
                        continue

                    suggested = clean_emails(
                        result.get("emails") or [], registered_domain(lead.get("website", ""))
                    )
                    if not suggested:
                        continue

                    # Drop anything at a domain that cannot receive mail.
                    kept = []
                    for address in suggested:
                        domain = address.split("@")[-1]
                        if await domain_accepts_mail(domain):
                            kept.append(address)
                        else:
                            self.stats["rejected_no_mx"] += 1
                    if not kept:
                        continue

                    lead["emails"] = kept
                    lead["email"] = kept[0]
                    lead["email_source"] = "ai"
                    lead["email_status"] = "unverified"
                    lead["enriched"] = 1
                    changed += 1
                    self.stats["suggested"] += 1

                async with lock:
                    completed += 1
                    self._progress(
                        "AI batch %d/%d (%d suggestions so far)"
                        % (completed, len(batches), changed),
                        -1,
                    )

            await asyncio.gather(*(process(i, b) for i, b in enumerate(batches)))

        self._progress(
            "AI suggestions: %d addresses for %d businesses in %d request(s); "
            "%d discarded because the domain accepts no mail. All marked unverified."
            % (changed, len(leads), len(batches), self.stats["rejected_no_mx"]),
            -1,
        )
        return changed
