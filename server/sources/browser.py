"""Headless Chromium, driven by Playwright.

This replaced Selenium for two reasons that matter here.

**Throughput.** Playwright talks to the browser over a persistent CDP
connection rather than Selenium's HTTP-per-command protocol, and it waits for
elements automatically instead of polling. Published scraping benchmarks put it
at roughly twice Selenium's pages-per-minute.

**Memory.** The Selenium version ran a *pool of separate Chrome processes* to
get concurrency - five browsers, five times the startup cost and RAM. Playwright
opens many pages inside one browser, so concurrency costs a tab rather than a
process.

Images, fonts, media and stylesheets are aborted at the network layer. We only
ever read the DOM, and blocking them roughly halves page-load time.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .. import settings

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

# Nothing here is ever read from, and each one costs a request.
BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet", "imageset"}

LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-notifications",
    "--mute-audio",
    "--lang=en-US",
    "--disable-blink-features=AutomationControlled",
]


async def _block_assets(route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCES:
        await route.abort()
    else:
        await route.continue_()


class BrowserSession:
    """One Chromium instance, shared by every page a job needs."""

    def __init__(self, headless: bool | None = None):
        self._headless = settings.GMAPS_HEADLESS if headless is None else headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        launch: dict = {"headless": self._headless, "args": LAUNCH_ARGS}
        if settings.CHROME_BINARY:
            launch["executable_path"] = settings.CHROME_BINARY
        self._browser = await self._playwright.chromium.launch(**launch)
        self._context = await self._browser.new_context(
            user_agent=_UA,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            java_script_enabled=True,
            service_workers="block",
        )
        self._context.set_default_timeout(settings.GMAPS_PAGE_TIMEOUT * 1000)
        self._context.set_default_navigation_timeout(settings.GMAPS_PAGE_TIMEOUT * 1000)
        await self._context.route("**/*", _block_assets)

    async def new_page(self) -> Page:
        if self._context is None:
            raise RuntimeError("BrowserSession.start() was not awaited")
        return await self._context.new_page()

    async def close(self) -> None:
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception as exc:  # noqa: BLE001 - shutdown must never raise
                log.debug("browser shutdown step failed: %s", exc)
        self._context = self._browser = self._playwright = None


@asynccontextmanager
async def browser_session(headless: bool | None = None):
    """`async with browser_session() as session:` - always tears down."""
    session = BrowserSession(headless=headless)
    await session.start()
    try:
        yield session
    finally:
        await session.close()
