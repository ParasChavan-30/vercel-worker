"""
session_manager.py — HTTP session lifecycle for COA scraper.

Manages:
  - requests.Session creation with browser headers
  - Homepage hit (sets PHPSESSID cookie)
  - Form page hit (primes PHP session for captcha)
  - AJAX cap_code fetch (always returns '' but must be called to prime session)
  - CAPTCHA image download (raw bytes)
"""

import logging
import random
from typing import Optional, Tuple

import requests

import config

logger = logging.getLogger(__name__)


def create_session() -> requests.Session:
    """
    Create a new requests.Session with browser-like headers and optional proxy rotation.
    Does NOT make any HTTP requests yet.
    """
    session = requests.Session()
    session.headers.update(config.BROWSER_HEADERS)

    if config.PROXIES:
        proxy = random.choice(config.PROXIES)
        session.proxies = {
            "http": proxy,
            "https": proxy,
        }
        safe_proxy = proxy.split("@")[-1] if "@" in proxy else proxy
        logger.debug("Session assigned proxy: %s", safe_proxy)

    return session


def initialize_session(session: requests.Session) -> bool:
    """
    Prime the PHP session by hitting the homepage then the form page.

    Steps:
      1. GET https://coa.gov.in/              → sets PHPSESSID cookie
      2. GET search_arch2.php form page       → loads form, readies captcha session
      3. GET ajax_captcha.php (AJAX call)     → primes server-side captcha hash

    Returns:
        True on success, False on any network error.
    """
    try:
        # Step 1: Homepage
        r1 = session.get(
            config.BASE_URL + "/",
            timeout=config.SESSION_TIMEOUT
        )
        r1.raise_for_status()
        phpsessid = session.cookies.get("PHPSESSID", "N/A")
        logger.debug("Homepage OK | PHPSESSID=%s", phpsessid)

        # Step 2: Form page
        r2 = session.get(
            config.FORM_PAGE_URL,
            headers={"Referer": config.BASE_URL + "/"},
            timeout=config.SESSION_TIMEOUT
        )
        r2.raise_for_status()
        logger.debug("Form page OK | len=%d", len(r2.text))

        # Step 3: AJAX cap_code (must fire to match browser behaviour)
        r3 = session.get(
            config.AJAX_CODE_URL + f"?sid={random.random()}",
            headers={
                "Referer": config.FORM_PAGE_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=config.SESSION_TIMEOUT
        )
        r3.raise_for_status()
        logger.debug("AJAX cap_code OK | value=%r", r3.text.strip())

        return True

    except requests.RequestException as e:
        logger.error("initialize_session failed: %s", e)
        return False


def download_captcha(session: requests.Session) -> Optional[bytes]:
    """
    Download the CAPTCHA image from the server.

    IMPORTANT: Must be called with the SAME session used in initialize_session()
    so the PHPSESSID cookie is shared and the server maps the image to the session.

    Returns:
        Raw image bytes (JPEG) on success, None on failure.
    """
    try:
        url = config.CAPTCHA_IMG_URL + f"?sid={random.random()}"
        r = session.get(
            url,
            headers={"Referer": config.FORM_PAGE_URL},
            timeout=config.SESSION_TIMEOUT,
        )
        r.raise_for_status()

        content_type = r.headers.get("Content-Type", "")
        if "image" not in content_type:
            logger.warning("Unexpected Content-Type for captcha: %s", content_type)
            return None

        logger.debug("CAPTCHA downloaded: %d bytes, type=%s", len(r.content), content_type)
        return r.content

    except requests.RequestException as e:
        logger.error("download_captcha failed: %s", e)
        return None


def submit_search(
    session: requests.Session,
    registration_no: str,
    captcha_answer: str,
    cap_code: str = ""
) -> Optional[requests.Response]:
    """
    POST the search form with the registration number and solved CAPTCHA.

    Form fields (confirmed via DevTools inspection):
      - val_sub  = '1'
      - searCat  = '3'
      - arc_reg  = registration_no
      - cap_code = '' (server-side session holds the real hash)
      - T3       = captcha_answer (the 6-char string from Groq)
      - submit   = 'Search'

    Returns:
        requests.Response on success, None on network error.
    """
    payload = {
        "val_sub":  "1",
        "searCat":  "3",
        "arc_reg":  registration_no,
        "cap_code": cap_code,
        "T3":       captcha_answer,
        "submit":   "Search",
    }
    try:
        r = session.post(
            config.POST_URL,
            data=payload,
            headers={
                "Referer": config.FORM_PAGE_URL,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": config.BASE_URL,
            },
            timeout=config.SESSION_TIMEOUT,
        )
        r.raise_for_status()
        logger.debug(
            "POST %s -> %d | len=%d | url=%s",
            registration_no, r.status_code, len(r.text), r.url
        )
        return r

    except requests.RequestException as e:
        logger.error("submit_search failed for %s: %s", registration_no, e)
        return None
