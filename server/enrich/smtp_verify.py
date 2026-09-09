"""Email verification over SMTP, plus pattern guessing.

This is the step that separates a scraped list from a usable contact database.
Guessing ``contact@devsinc.com`` is free; knowing whether that mailbox actually
exists is the valuable part, and only the domain's own mail server can answer it.

How it works, per domain:

1. Resolve the domain's MX records.
2. Open one SMTP session to the best MX and hold it open for every address on
   that domain - one connection, many ``RCPT TO`` probes.
3. Probe a random mailbox that cannot exist. If the server accepts it, the
   domain is **catch-all** and accepts anything, so no individual answer from it
   means anything. Everything on that domain is reported ``catch_all``.
4. Otherwise ``RCPT TO`` each candidate and read the code: 250/251 means the
   mailbox exists, 550/551/553 means it does not, 4xx means greylisted or
   throttled and is reported ``unknown`` rather than guessed at.

The session never issues ``DATA``, so no mail is ever sent.

Two practical warnings, both handled here:

* **Outbound port 25 is blocked on most residential ISPs and on AWS/GCP by
  default.** The verifier probes once, caches the answer, and degrades to
  "unknown" instead of hanging on every domain.
* Aggressive probing gets an IP rate-limited or blacklisted. Candidates per
  domain are capped, connections are reused, and domains are processed with
  bounded concurrency.
"""
from __future__ import annotations

import asyncio
import logging
import random
import string
from dataclasses import dataclass

from .. import settings

log = logging.getLogger(__name__)

# Role mailboxes worth trying when a site publishes no address. Ordered by how
# likely a business is to read them.
DEFAULT_PATTERNS = (
    # Ordered by how often a business actually reads the mailbox, because the
    # candidate cap cuts the tail off this list.
    "info", "contact", "hello", "admin", "sales", "support", "help", "hr",
    "office", "enquiries", "reception", "bookings", "mail", "team",
    "careers", "jobs", "accounts", "marketing",
)

VERIFIED = "verified"
INVALID = "invalid"
CATCH_ALL = "catch_all"
UNKNOWN = "unknown"
NO_MX = "no_mx"
BLOCKED = "blocked"


@dataclass
class VerifyResult:
    address: str
    status: str
    detail: str = ""

    @property
    def usable(self) -> bool:
        """Safe to store as a contact."""
        return self.status == VERIFIED


def _random_mailbox() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


def candidate_addresses(domain: str, owner_name: str = "") -> list[str]:
    """Role-mailbox guesses for a domain, plus owner-derived ones.

    These are hypotheses. They are only worth storing once SMTP has confirmed
    the mailbox exists, which is exactly what this module is for.
    """
    if not domain or "." not in domain:
        return []
    patterns = list(settings.SMTP_PATTERNS or DEFAULT_PATTERNS)

    name = (owner_name or "").strip().lower()
    parts = [
        p for p in "".join(c if c.isalpha() or c.isspace() else " " for c in name).split()
        if len(p) > 1
    ]
    personal: list[str] = []
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        personal = [
            "%s.%s@%s" % (first, last, domain),
            "%s@%s" % (first, domain),
            "%s%s@%s" % (first[0], last, domain),
        ]
    elif len(parts) == 1:
        personal = ["%s@%s" % (parts[0], domain)]

    # A named person reaches someone directly, so those go after the few
    # highest-value role mailboxes but ahead of the long tail - otherwise the
    # candidate cap would drop them entirely.
    out = ["%s@%s" % (mailbox, domain) for mailbox in patterns[:3]]
    out += personal
    out += ["%s@%s" % (mailbox, domain) for mailbox in patterns[3:]]

    seen, unique = set(), []
    for address in out:
        if address not in seen:
            seen.add(address)
            unique.append(address)
    return unique[: max(1, settings.SMTP_MAX_CANDIDATES)]


class SmtpVerifier:
    """Verifies mailboxes by asking the domain's mail server."""

    def __init__(self) -> None:
        self._mx_cache: dict[str, list[str]] = {}
        self._port_open: bool | None = None
        self._lock = asyncio.Lock()
        self.stats = {"domains": 0, "probed": 0, "verified": 0, "invalid": 0,
                      "catch_all": 0, "unknown": 0}

    # -- DNS -----------------------------------------------------------------

    async def mx_hosts(self, domain: str) -> list[str]:
        if domain in self._mx_cache:
            return self._mx_cache[domain]
        hosts: list[str] = []
        try:
            import dns.asyncresolver  # noqa: PLC0415

            answers = await dns.asyncresolver.resolve(
                domain, "MX", lifetime=settings.SMTP_DNS_TIMEOUT
            )
            hosts = [
                str(r.exchange).rstrip(".")
                for r in sorted(answers, key=lambda r: r.preference)
            ]
        except ImportError:
            log.warning("dnspython is not installed; SMTP verification unavailable")
        except Exception as exc:  # noqa: BLE001 - NXDOMAIN, timeout, no answer
            log.debug("no MX for %s: %s", domain, exc)
        self._mx_cache[domain] = hosts
        return hosts

    # -- SMTP conversation ---------------------------------------------------

    @staticmethod
    async def _read_reply(reader: asyncio.StreamReader, timeout: float) -> tuple[int, str]:
        """Read one SMTP reply, following multi-line continuations."""
        lines: list[str] = []
        while True:
            raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            lines.append(line)
            # "250-text" continues; "250 text" ends.
            if len(line) >= 4 and line[3] == " ":
                break
            if len(line) < 4:
                break
        if not lines:
            return 0, ""
        try:
            code = int(lines[-1][:3])
        except ValueError:
            code = 0
        return code, " | ".join(lines)

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, command: str) -> None:
        writer.write((command + "\r\n").encode())
        await writer.drain()

    async def _probe_domain(
        self, host: str, port: int, addresses: list[str]
    ) -> dict[str, VerifyResult]:
        """Run one SMTP session and probe every address on it."""
        timeout = settings.SMTP_TIMEOUT
        results: dict[str, VerifyResult] = {}
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            code, _ = await self._read_reply(reader, timeout)
            if code != 220:
                return {a: VerifyResult(a, UNKNOWN, "banner %s" % code) for a in addresses}

            await self._send(writer, "EHLO %s" % settings.SMTP_HELO_HOST)
            code, _ = await self._read_reply(reader, timeout)
            if code != 250:
                await self._send(writer, "HELO %s" % settings.SMTP_HELO_HOST)
                code, _ = await self._read_reply(reader, timeout)
                if code != 250:
                    return {a: VerifyResult(a, UNKNOWN, "ehlo %s" % code) for a in addresses}

            await self._send(writer, "MAIL FROM:<%s>" % settings.SMTP_MAIL_FROM)
            code, text = await self._read_reply(reader, timeout)
            if code != 250:
                return {a: VerifyResult(a, UNKNOWN, "mail-from %s" % code) for a in addresses}

            # Catch-all probe: if a random mailbox is accepted, nothing this
            # server says about a specific mailbox is meaningful.
            domain = addresses[0].split("@")[-1]
            await self._send(writer, "RCPT TO:<%s@%s>" % (_random_mailbox(), domain))
            code, _ = await self._read_reply(reader, timeout)
            if code in (250, 251):
                return {
                    a: VerifyResult(a, CATCH_ALL, "domain accepts all recipients")
                    for a in addresses
                }

            for address in addresses:
                await self._send(writer, "RSET")
                await self._read_reply(reader, timeout)
                await self._send(writer, "MAIL FROM:<%s>" % settings.SMTP_MAIL_FROM)
                await self._read_reply(reader, timeout)
                await self._send(writer, "RCPT TO:<%s>" % address)
                code, text = await self._read_reply(reader, timeout)
                self.stats["probed"] += 1

                if code in (250, 251):
                    results[address] = VerifyResult(address, VERIFIED, text[:120])
                elif code in (550, 551, 553, 501, 502):
                    results[address] = VerifyResult(address, INVALID, text[:120])
                else:
                    # 450/451/452 and anything else: greylisting or throttling.
                    results[address] = VerifyResult(address, UNKNOWN, "code %s" % code)
                await asyncio.sleep(settings.SMTP_PROBE_DELAY)

            await self._send(writer, "QUIT")
        except (asyncio.TimeoutError, OSError) as exc:
            for address in addresses:
                results.setdefault(address, VerifyResult(address, UNKNOWN, type(exc).__name__))
        finally:
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
        for address in addresses:
            results.setdefault(address, VerifyResult(address, UNKNOWN, "no reply"))
        return results

    # -- availability --------------------------------------------------------

    async def port_reachable(self) -> bool:
        """Check once whether outbound SMTP is possible at all.

        Residential ISPs and the big clouds block port 25 by default. Without
        this check every domain would burn a full timeout before failing.
        """
        async with self._lock:
            if self._port_open is not None:
                return self._port_open
            host = settings.SMTP_PROBE_HOST
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, settings.SMTP_PORT),
                    timeout=settings.SMTP_TIMEOUT,
                )
                writer.close()
                await writer.wait_closed()
                self._port_open = True
            except (asyncio.TimeoutError, OSError) as exc:
                log.warning(
                    "outbound port %s appears blocked (%s) - SMTP verification "
                    "will report 'unknown'. Run from a host that allows outbound "
                    "port 25 to use it.",
                    settings.SMTP_PORT, type(exc).__name__,
                )
                self._port_open = False
            return self._port_open

    # -- entry point ---------------------------------------------------------

    async def verify_domain(
        self, domain: str, addresses: list[str]
    ) -> dict[str, VerifyResult]:
        """Verify a set of addresses that all share one domain."""
        addresses = [a for a in addresses if a and "@" in a]
        if not addresses:
            return {}
        if not await self.port_reachable():
            return {a: VerifyResult(a, BLOCKED, "outbound port 25 blocked") for a in addresses}

        hosts = await self.mx_hosts(domain)
        if not hosts:
            self.stats["domains"] += 1
            return {a: VerifyResult(a, NO_MX, "domain has no MX records") for a in addresses}

        self.stats["domains"] += 1
        results: dict[str, VerifyResult] = {}
        for host in hosts[: settings.SMTP_MX_ATTEMPTS]:
            results = await self._probe_domain(host, settings.SMTP_PORT, addresses)
            if any(r.status not in (UNKNOWN,) for r in results.values()):
                break

        for result in results.values():
            if result.status == VERIFIED:
                self.stats["verified"] += 1
            elif result.status == INVALID:
                self.stats["invalid"] += 1
            elif result.status == CATCH_ALL:
                self.stats["catch_all"] += 1
            else:
                self.stats["unknown"] += 1
        return results

    async def verify_many(
        self, by_domain: dict[str, list[str]]
    ) -> dict[str, VerifyResult]:
        """Verify addresses grouped by domain, a few domains at a time."""
        semaphore = asyncio.Semaphore(max(1, settings.SMTP_CONCURRENCY))
        merged: dict[str, VerifyResult] = {}
        lock = asyncio.Lock()

        async def one(domain: str, addresses: list[str]) -> None:
            async with semaphore:
                found = await self.verify_domain(domain, addresses)
            async with lock:
                merged.update(found)

        await asyncio.gather(*(one(d, a) for d, a in by_domain.items()))
        return merged


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _domain_of(address: str) -> str:
    return address.split("@")[-1].lower().strip()


async def verify_existing(
    leads: list[dict], verifier: SmtpVerifier, on_progress=None
) -> dict[str, int]:
    """Confirm the addresses already on these leads actually accept mail.

    Scraped addresses are real strings taken off a real site, but a site can
    advertise a mailbox that was closed years ago. Verification records that
    distinction on the lead instead of leaving it implied.
    """
    by_domain: dict[str, list[str]] = {}
    for lead in leads:
        for address in lead.get("emails") or ([lead["email"]] if lead.get("email") else []):
            by_domain.setdefault(_domain_of(address), []).append(address)
    if not by_domain:
        return {}

    if on_progress:
        on_progress(
            "Verifying %d address(es) across %d domain(s) over SMTP..."
            % (sum(len(v) for v in by_domain.values()), len(by_domain)),
            -1,
        )

    results = await verifier.verify_many(
        {d: sorted(set(a)) for d, a in by_domain.items()}
    )

    tally: dict[str, int] = {}
    for lead in leads:
        addresses = lead.get("emails") or ([lead["email"]] if lead.get("email") else [])
        statuses = [results[a].status for a in addresses if a in results]
        if not statuses:
            continue
        # Keep the strongest signal available for this lead.
        for preferred in (VERIFIED, CATCH_ALL, UNKNOWN, BLOCKED, NO_MX, INVALID):
            if preferred in statuses:
                lead["email_status"] = preferred
                tally[preferred] = tally.get(preferred, 0) + 1
                break
        # Drop addresses the server explicitly rejected.
        still_good = [a for a in addresses if results.get(a) is None
                      or results[a].status != INVALID]
        if still_good != addresses:
            lead["emails"] = still_good
            lead["email"] = still_good[0] if still_good else ""
            if not still_good:
                lead["email_status"] = ""
                lead["email_source"] = ""
    return tally


async def discover_by_pattern(
    leads: list[dict], verifier: SmtpVerifier, on_progress=None
) -> int:
    """Guess role mailboxes for leads with no email, and keep only real ones.

    This is the step that makes guessing worthwhile: ``contact@<domain>`` is a
    free hypothesis, and the domain's own mail server settles it.
    """
    targets = []
    for lead in leads:
        if (lead.get("email") or "").strip():
            continue
        website = (lead.get("website") or "").strip()
        if not website:
            continue
        from .emails import registered_domain  # local import avoids a cycle

        domain = registered_domain(website)
        if domain:
            targets.append((lead, domain))
    if not targets:
        return 0

    by_domain: dict[str, list[str]] = {}
    for lead, domain in targets:
        by_domain.setdefault(domain, candidate_addresses(domain, lead.get("owner_name", "")))

    if on_progress:
        on_progress(
            "Testing %d candidate mailbox(es) across %d domain(s) that published no address..."
            % (sum(len(v) for v in by_domain.values()), len(by_domain)),
            -1,
        )

    results = await verifier.verify_many(by_domain)

    found = 0
    for lead, domain in targets:
        confirmed = [
            address
            for address in by_domain.get(domain, [])
            if results.get(address) is not None and results[address].usable
        ]
        if not confirmed:
            continue
        lead["emails"] = confirmed
        lead["email"] = confirmed[0]
        lead["email_source"] = "pattern"
        lead["email_status"] = VERIFIED
        lead["enriched"] = 1
        found += 1
    return found
