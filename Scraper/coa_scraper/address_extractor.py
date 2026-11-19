"""
address_extractor.py — Parse COA search result HTML for address data.

Confirmed result table structure (from search_architectResult.php):
  <table class="table table-bordered custm-table">
    <thead>
      <tr>  ← header title row
      <tr>  ← column headers: S.No | Architect Name | Registration Number |
                               Disciplinary Action | Address | Mobile | Email ID
    </thead>
    <tbody>
      <tr class="GridRow">  ← data row (one per result)
        <td>1</td>                       ← index 0: S.No
        <td>Mr. Jasdesh Singh Sodhi</td> ← index 1: Name
        <td>CA/1975/00001</td>           ← index 2: Reg No.
        <td>No</td>                      ← index 3: Disciplinary Action
        <td>Block H - 51/A, Saket…</td> ← index 4: Address  ← WE WANT THIS
        <td>9818130441</td>              ← index 5: Mobile
        <td>jds@sodhis.org</td>         ← index 6: Email
      </tr>
    </tbody>
  </table>
"""

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ─── Known response patterns ──────────────────────────────────────────────────

# Phrases that appear when CAPTCHA is wrong
CAPTCHA_INVALID_MARKERS = [
    "invalid captcha",
    "wrong captcha",
    "captcha mismatch",
    "security code",
    "incorrect security",
    "please enter the security code",
]

# Phrases that appear when no architect record exists
NOT_FOUND_MARKERS = [
    "no record found",
    "no records found",
    "no architect found",
    "record not found",
    "not found",
]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", text).strip()


def is_captcha_invalid(html: str) -> bool:
    """
    Return True if the response HTML indicates an invalid CAPTCHA submission.
    Checks both text content and common PHP redirect patterns.
    """
    lower = html.lower()
    for marker in CAPTCHA_INVALID_MARKERS:
        if marker in lower:
            logger.debug("CAPTCHA invalid marker found: %r", marker)
            return True
    return False


def is_not_found(html: str) -> bool:
    """
    Return True if the response HTML indicates no architect record was found
    for the given registration number.
    """
    lower = html.lower()
    for marker in NOT_FOUND_MARKERS:
        if marker in lower:
            logger.debug("Not-found marker found: %r", marker)
            return True
    return False


def is_redirected_away(html: str) -> bool:
    """
    Return True if the page was redirected to message.php
    (happens when the session has expired or the request looks like a bot).
    """
    lower = html.lower()
    return "message.php" in lower or "location.href='message.php'" in lower


# ─── Main extraction ─────────────────────────────────────────────────────────

def extract_address(html: str) -> Optional[str]:
    """
    Parse the search result HTML and return the address string.

    Extraction strategy (in order):
      1. Find <table class contains 'custm-table'>
      2. Find <tr class='GridRow'> (data row)
      3. Return the text of the 5th <td> (index 4) = Address column
      4. Fallback: search for a <th>Address</th> and read its sibling <td>

    Returns:
        Clean address string, or None if not parseable.
    """
    soup = BeautifulSoup(html, "html.parser")

    # ── Strategy 1: Find the result table by class ────────────────────────────
    result_table = soup.find("table", class_="custm-table")
    if not result_table:
        # Fallback: any table with 'table-bordered' class
        result_table = soup.find("table", class_="table-bordered")

    if result_table:
        # Find the data row (GridRow)
        data_row = result_table.find("tr", class_="GridRow")
        if data_row:
            cells = data_row.find_all("td")
            if len(cells) >= 5:
                address_text = _normalise(cells[4].get_text())
                logger.debug("Address extracted from GridRow td[4]: %r", address_text)
                if address_text and address_text not in ("—", "-", "", "N/A"):
                    return address_text
                else:
                    return None  # Address is genuinely empty on site

        # Fallback: find <th>Address</th> and get next sibling td
        addr_th = result_table.find("th", string=re.compile(r"^address$", re.I))
        if addr_th:
            col_idx = list(addr_th.parent.find_all("th")).index(addr_th)
            for row in result_table.find_all("tr", class_="GridRow"):
                cells = row.find_all("td")
                if len(cells) > col_idx:
                    address_text = _normalise(cells[col_idx].get_text())
                    logger.debug("Address from th-index fallback: %r", address_text)
                    return address_text if address_text else None

    # ── Strategy 2: Scan all tables for an Address column ────────────────────
    logger.debug("custm-table not found — scanning all tables")
    for table in soup.find_all("table"):
        headers = [_normalise(th.get_text()).lower() for th in table.find_all("th")]
        if "address" in headers:
            addr_idx = headers.index("address")
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) > addr_idx:
                    address_text = _normalise(cells[addr_idx].get_text())
                    if address_text:
                        logger.debug("Address from table scan: %r", address_text)
                        return address_text

    logger.warning("Could not extract address from HTML (len=%d)", len(html))
    return None


def extract_all_fields(html: str) -> dict:
    """
    Extract all available fields from the COA search result table:
      - Architect Name
      - Registration Number
      - Disciplinary Action
      - Address
      - Mobile
      - Email ID

    Returns dict with keys: name, registration_no, disciplinary_action,
                             address, mobile, email
    """
    soup = BeautifulSoup(html, "html.parser")
    result_table = soup.find("table", class_="custm-table")
    if not result_table:
        result_table = soup.find("table", class_="table-bordered")
    if not result_table:
        for t in soup.find_all("table"):
            text_preview = t.get_text().lower()
            if "architect name" in text_preview or "disciplinary action" in text_preview:
                result_table = t
                break

    result = {}
    if result_table:
        # Check for GridRow, GridAltRow, or any tr with td
        data_row = result_table.find("tr", class_=re.compile(r"Grid(Alt)?Row", re.I))
        if not data_row:
            for r in result_table.find_all("tr"):
                if r.find_all("td"):
                    data_row = r
                    break

        if data_row:
            cells = data_row.find_all("td")
            field_names = [
                "s_no", "name", "registration_no",
                "disciplinary_action", "address", "mobile", "email"
            ]
            for i, name in enumerate(field_names):
                if i < len(cells):
                    val = _normalise(cells[i].get_text())
                    if val in ("—", "-", "N/A", "None", "null"):
                        val = ""
                    result[name] = val

    return result
