"""Headless Chrome helpers shared by the scraping sources."""
from __future__ import annotations

import logging

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from .. import settings

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)


def build_driver(headless: bool | None = None) -> webdriver.Chrome:
    """Create a Chrome driver tuned for scraping speed.

    Images, fonts and stylesheets are blocked: we only ever read the DOM, and
    skipping them cuts page-load time roughly in half.
    """
    opts = Options()
    if headless if headless is not None else settings.GMAPS_HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--mute-audio")
    opts.add_argument(f"--user-agent={_UA}")
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 2,
            "profile.managed_default_content_settings.stylesheets": 1,
            "profile.default_content_setting_values.notifications": 2,
            "profile.managed_default_content_settings.plugins": 2,
        },
    )
    if settings.CHROME_BINARY:
        opts.binary_location = settings.CHROME_BINARY

    if settings.CHROMEDRIVER_PATH:
        driver = webdriver.Chrome(service=Service(executable_path=settings.CHROMEDRIVER_PATH), options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(settings.GMAPS_PAGE_TIMEOUT)
    driver.implicitly_wait(0)
    return driver


def quit_driver(driver) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception as exc:  # noqa: BLE001 - shutdown must never raise
        log.debug("driver quit failed: %s", exc)
