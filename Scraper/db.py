"""
db.py — Database layer for COA Address Scraper.

Handles all read/write operations for the coa_registrations table,
supporting both direct MariaDB/MySQL connection and Remote HTTP Server API mode.
"""

import logging
import requests
import mysql.connector
from mysql.connector import InterfaceError, OperationalError, Error
from typing import List, Dict, Optional, Any

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Remote API Helper
# ─────────────────────────────────────────────────────────

def _make_api_request(method: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Send an HTTP request to the remote server API (api.php).
    """
    headers = {
        "X-API-Key": config.REMOTE_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": config.BROWSER_HEADERS["User-Agent"]
    }
    try:
        response = requests.request(
            method=method,
            url=config.REMOTE_API_URL,
            params=params,
            json=json_data,
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        res_json = response.json()
        if not res_json.get("success", False) and "error" in res_json:
            logger.error("Remote API Error: %s", res_json.get("error"))
            raise RuntimeError(f"Remote API returned error: {res_json.get('error')}")
        return res_json
    except requests.RequestException as e:
        logger.error("HTTP request to Remote API failed (%s %s): %s", method, config.REMOTE_API_URL, e)
        raise


# ─────────────────────────────────────────────────────────
# Connection Management
# ─────────────────────────────────────────────────────────

def get_connection() -> Optional[mysql.connector.MySQLConnection]:
    """
    Create and return a new database connection or None if using Remote API.
    """
    if config.USE_REMOTE_API:
        logger.info("Using Remote API mode via bridge: %s", config.REMOTE_API_URL)
        return None

    return mysql.connector.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        autocommit=False,
        connection_timeout=10,
        charset="utf8mb4",
    )


def ensure_connected(conn: Optional[mysql.connector.MySQLConnection]) -> Optional[mysql.connector.MySQLConnection]:
    """
    Reconnect if the connection is lost or closed (Local MySQL mode only).
    """
    if config.USE_REMOTE_API:
        return None

    try:
        if conn is None or not conn.is_connected():
            logger.info("DB connection lost — reconnecting…")
            conn = get_connection()
    except (InterfaceError, OperationalError):
        logger.warning("Reconnecting to database…")
        conn = get_connection()
    return conn


# ─────────────────────────────────────────────────────────
# Read Operations
# ─────────────────────────────────────────────────────────

def get_pending_records(
    conn: Optional[mysql.connector.MySQLConnection] = None,
    batch_size: int = config.BATCH_SIZE,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None
) -> List[Dict]:
    """
    Fetch a batch of pending records (status=0) from Remote API or Local DB.

    Returns:
        List of dicts: [{"id": int, "registration_no": str, ...}, …]
    """
    if config.USE_REMOTE_API:
        payload = {
            "action": "get_pending",
            "batch_size": batch_size,
            "worker_id": getattr(config, "WORKER_ID", "local_worker_paras")
        }
        if start_year is not None:
            payload["year"] = start_year
        res = _make_api_request("POST", json_data=payload)
        rows = res.get("records") or res.get("data") or []

        # Filter locally if remote API didn't filter by year range
        if rows and (start_year or end_year):
            filtered = []
            for r in rows:
                parts = r.get("registration_no", "").split("/")
                if len(parts) >= 2 and parts[1].isdigit():
                    yr = int(parts[1])
                    if start_year and yr < start_year:
                        continue
                    if end_year and yr > end_year:
                        continue
                filtered.append(r)
            rows = filtered

        logger.info("Fetched %d pending records from Remote API (Status moved to 1)", len(rows))
        return rows

    conn = ensure_connected(conn)
    cursor = conn.cursor(dictionary=True)
    try:
        where_clauses = ["status = 0"]
        params = []
        if start_year is not None:
            where_clauses.append("year >= %s")
            params.append(start_year)
        if end_year is not None:
            where_clauses.append("year <= %s")
            params.append(end_year)

        where_sql = " AND ".join(where_clauses)
        params.append(batch_size)

        query = f"""
            SELECT id, year, serial, registration_no
            FROM coa_registrations
            WHERE {where_sql}
            ORDER BY year ASC, serial ASC
            LIMIT %s
        """
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        logger.debug("Fetched %d pending records from Local DB", len(rows))
        return rows
    except Error as e:
        logger.error("get_pending_records failed: %s", e)
        raise
    finally:
        cursor.close()


def get_total_pending(
    conn: Optional[mysql.connector.MySQLConnection] = None,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None
) -> int:
    """Return the count of records still needing processing."""
    if config.USE_REMOTE_API:
        try:
            payload = {"action": "get_total_pending"}
            if start_year is not None:
                payload["year"] = start_year
            res = _make_api_request("POST", json_data=payload)
            return res.get("total_pending", 0)
        except Exception as e:
            logger.debug("get_total_pending returned: %s. Defaulting to 999999.", e)
            return 999999

    conn = ensure_connected(conn)
    cursor = conn.cursor()
    try:
        where_clauses = ["status = 0"]
        params = []
        if start_year is not None:
            where_clauses.append("year >= %s")
            params.append(start_year)
        if end_year is not None:
            where_clauses.append("year <= %s")
            params.append(end_year)

        where_sql = " AND ".join(where_clauses)
        cursor.execute(f"SELECT COUNT(*) FROM coa_registrations WHERE {where_sql}", tuple(params))
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def get_total_failed(conn: Optional[mysql.connector.MySQLConnection] = None) -> int:
    """Return the count of records that failed CAPTCHA during the main run."""
    if config.USE_REMOTE_API:
        res = _make_api_request("GET", params={"action": "get_total_failed"})
        return res.get("total_failed", 0)

    conn = ensure_connected(conn)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM coa_registrations WHERE address = 'CAPTCHA_FAILED'")
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def reset_failed_records(conn: Optional[mysql.connector.MySQLConnection] = None) -> int:
    """Reset all CAPTCHA_FAILED records back to NULL for a second pass."""
    if config.USE_REMOTE_API:
        res = _make_api_request("POST", json_data={"action": "reset_failed"})
        return res.get("reset_count", 0)

    conn = ensure_connected(conn)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE coa_registrations SET address = NULL WHERE address = 'CAPTCHA_FAILED'")
        conn.commit()
        return cursor.rowcount
    except Error as e:
        conn.rollback()
        logger.error("reset_failed_records failed: %s", e)
        raise
    finally:
        cursor.close()


# ─────────────────────────────────────────────────────────
# Write Operations
# ─────────────────────────────────────────────────────────

def update_scraped_batch(
    conn: Optional[mysql.connector.MySQLConnection],
    updates: List[Dict[str, Any]]
) -> int:
    """
    Batch update scraped records (name, email, mobile, dob, address, status) on Remote API or Local DB.

    Args:
        updates: List of dicts [
            {"registration_no": "CA/...", "name": "...", "email": "...", "mobile": "...", "address": "...", "status": 2},
            ...
        ]

    Returns:
        Number of rows updated.
    """
    if not updates:
        return 0

    if config.USE_REMOTE_API:
        payload = {
            "action": "save_scraped",
            "worker_id": getattr(config, "WORKER_ID", "local_worker_paras"),
            "data": updates
        }
        res = _make_api_request("POST", json_data=payload)
        summary = res.get("summary", {})
        count = summary.get("updated", len(updates))
        logger.info(
            "Saved %d records on Remote Database (success: %d, failed: %d)",
            count, summary.get("scraped_success", 0), summary.get("scraped_failed", 0)
        )
        return count

    conn = ensure_connected(conn)
    cursor = conn.cursor()
    try:
        data = []
        for item in updates:
            reg_no = item.get("registration_no", "")
            name = item.get("name", "")
            email = item.get("email", "")
            mobile = item.get("mobile", "")
            dob = item.get("dob", "")
            addr = item.get("address")
            st = item.get("status", 2)
            data.append((name, name, email, email, mobile, mobile, dob, dob, addr, st, reg_no))

        cursor.executemany(
            """UPDATE coa_registrations 
               SET name = CASE WHEN %s != '' THEN %s ELSE name END,
                   email = CASE WHEN %s != '' THEN %s ELSE email END,
                   mobile = CASE WHEN %s != '' THEN %s ELSE mobile END,
                   dob = CASE WHEN %s != '' THEN %s ELSE dob END,
                   address = %s,
                   status = %s,
                   scraped_at = NOW()
               WHERE registration_no = %s""",
            data
        )
        conn.commit()
        count = cursor.rowcount
        logger.info("Batch updated %d records in local DB", count)
        return count
    except Error as e:
        conn.rollback()
        logger.error("update_scraped_batch failed: %s", e)
        raise
    finally:
        cursor.close()


def update_addresses_batch(
    conn: Optional[mysql.connector.MySQLConnection],
    updates: List[Dict[str, Any]]
) -> int:
    """
    Wrapper for backward compatibility. Adapts records to include status and all fields.
    """
    formatted = []
    for item in updates:
        if "status" not in item:
            addr = item.get("address", "")
            st = 3 if addr in ("CAPTCHA_FAILED", "NOT_FOUND") else 2
            formatted.append({
                "registration_no": item.get("registration_no"),
                "name": item.get("name", ""),
                "email": item.get("email", ""),
                "mobile": item.get("mobile", ""),
                "address": addr,
                "status": st
            })
        else:
            formatted.append(item)
    return update_scraped_batch(conn, formatted)


def update_address(
    conn: Optional[mysql.connector.MySQLConnection],
    registration_no: str,
    address: str
) -> bool:
    """
    Update a single registration's address / status.
    """
    st = 3 if address in ("CAPTCHA_FAILED", "NOT_FOUND") else 2
    count = update_scraped_batch(conn, [{
        "registration_no": registration_no,
        "address": address,
        "status": st
    }])
    return count > 0


def mark_not_found(
    conn: Optional[mysql.connector.MySQLConnection],
    registration_no: str
) -> bool:
    """
    Mark a registration number as NOT_FOUND so it is excluded from future runs.
    """
    logger.info("Marking NOT_FOUND for %s", registration_no)
    return update_address(conn, registration_no, "NOT_FOUND")


def mark_failed(
    conn: Optional[mysql.connector.MySQLConnection],
    registration_no: str
) -> bool:
    """
    Mark a record as CAPTCHA_FAILED after exhausting all retries.
    """
    logger.warning("Marking CAPTCHA_FAILED for %s", registration_no)
    return update_address(conn, registration_no, "CAPTCHA_FAILED")
