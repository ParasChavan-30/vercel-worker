"""
main.py — High-Performance Orchestrator for the COA Address Scraper.

Optimized architecture:
  - Multi-threaded parallel processing (ThreadPoolExecutor with MAX_WORKERS)
  - Fast local OCR with ddddocr engine (thread-safe, in-memory)
  - Session reuse per worker task
  - Batch DB/API write operations for maximum throughput
"""

import argparse
import logging
import random
import sys
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, Dict

import mysql.connector

import address_extractor
import captcha_solver
import config
import db
import session_manager

# ─────────────────────────────────────────────────────────────────────────────
# Logging Setup
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging():
    """Configure root logger with both console and file handlers."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root.addHandler(console)

    # File handler
    try:
        file_handler = logging.FileHandler("coa_scraper.log", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
        root.addHandler(file_handler)
    except OSError as e:
        logging.warning("Could not open log file: %s", e)


logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Per-Record Worker Execution
# ─────────────────────────────────────────────────────────────────────────────

def process_record_worker(record: dict) -> Tuple[Dict, str]:
    """
    Worker function to process a record independently in its own HTTP session.

    Returns:
      (scraped_data_dict, status_code)
      status_code is one of: 'updated', 'not_found', 'failed', 'error'
    """
    reg_no = record["registration_no"]
    logger.info("→ Processing: %s", reg_no)

    for attempt in range(1, config.MAX_CAPTCHA_RETRIES + 1):
        logger.debug("  [%s] Attempt %d/%d", reg_no, attempt, config.MAX_CAPTCHA_RETRIES)

        # Step 1: Fresh session per attempt/worker
        session = session_manager.create_session()
        ok = session_manager.initialize_session(session)
        if not ok:
            logger.warning("  [%s] Session init failed on attempt %d", reg_no, attempt)
            time.sleep(0.5)
            continue

        # Step 2: Download CAPTCHA image directly to memory
        image_bytes = session_manager.download_captcha(session)
        if image_bytes is None:
            logger.warning("  [%s] CAPTCHA download failed on attempt %d", reg_no, attempt)
            time.sleep(0.5)
            continue

        # Step 3: Solve CAPTCHA via local ddddocr engine
        captcha_answer = captcha_solver.solve_captcha(image_bytes)
        if captcha_answer is None:
            logger.warning("  [%s] CAPTCHA solve failed on attempt %d", reg_no, attempt)
            time.sleep(0.5)
            continue

        logger.info("  [%s] CAPTCHA solved: %s", reg_no, captcha_answer)

        # Step 4: Submit search form
        response = session_manager.submit_search(session, reg_no, captcha_answer)
        if response is None:
            logger.warning("  [%s] POST failed on attempt %d", reg_no, attempt)
            time.sleep(0.5)
            continue

        html = response.text

        # Step 5: Classify response
        if address_extractor.is_redirected_away(html):
            logger.warning("  [%s] Session redirected away on attempt %d — retrying", reg_no, attempt)
            time.sleep(0.5)
            continue

        if address_extractor.is_captcha_invalid(html):
            logger.warning("  [%s] ❌ Invalid CAPTCHA on attempt %d", reg_no, attempt)
            if attempt < config.MAX_CAPTCHA_RETRIES:
                time.sleep(0.2)
            continue

        if address_extractor.is_not_found(html):
            logger.info("  [%s] 🔍 Not found on COA site", reg_no)
            return (
                {
                    "registration_no": reg_no,
                    "address": "NOT_FOUND",
                    "status": 3
                },
                "not_found"
            )

        # Step 6: Extract ALL fields (Name, Registration No, Address, Mobile, Email)
        fields = address_extractor.extract_all_fields(html)
        address = fields.get("address") or address_extractor.extract_address(html) or ""
        name = fields.get("name", "")
        mobile = fields.get("mobile", "")
        email = fields.get("email", "")

        if address or name or mobile or email:
            logger.info("  [%s] ✅ Scraped: %s | Mobile: %s | Email: %s | Addr: %s",
                        reg_no, name, mobile, email, address[:40])
            return (
                {
                    "registration_no": reg_no,
                    "name": name,
                    "email": email,
                    "mobile": mobile,
                    "address": address,
                    "status": 2
                },
                "updated"
            )
        else:
            logger.warning("  [%s] Address/details field empty on site", reg_no)
            return (
                {
                    "registration_no": reg_no,
                    "address": "NO_ADDRESS",
                    "status": 2
                },
                "updated"
            )

    logger.error("  [%s] ❌ Failed after %d attempts", reg_no, config.MAX_CAPTCHA_RETRIES)
    return (
        {
            "registration_no": reg_no,
            "address": "CAPTCHA_FAILED",
            "status": 3
        },
        "failed"
    )


def process_record(conn, record: dict) -> str:
    """
    Single record processor wrapper for testing / backward compatibility.
    """
    scraped_data, status = process_record_worker(record)
    db.update_scraped_batch(conn, [scraped_data])
    return status


# ─────────────────────────────────────────────────────────────────────────────
# Main Loop (Sequential 1-by-1 Execution)
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="COA Sequential Scraper")
    parser.add_argument("--start-year", type=int, default=None, help="Start year filter (e.g. 2026)")
    parser.add_argument("--end-year", type=int, default=None, help="End year filter (e.g. 2026)")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay (seconds) between sequential requests (default: 0.3s)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of records to process in this run (default: unlimited)")
    args = parser.parse_args()

    setup_logging()

    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("COA Sequential Scraper (1-by-1 Execution) started at %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Mode: USE_REMOTE_API=%s | API: %s", config.USE_REMOTE_API, config.REMOTE_API_URL)
    if args.start_year or args.end_year:
        logger.info("Year Filter: %s to %s", args.start_year or "earliest", args.end_year or "latest")
    logger.info("Execution: Strictly Sequential | Delay between items: %.2fs", args.delay)
    if args.limit:
        logger.info("Run Limit: %d records", args.limit)
    logger.info("=" * 60)

    # Connect to DB / Remote API
    conn = None
    try:
        conn = db.get_connection()
        logger.info("Database/API connection setup complete")
    except Exception as e:
        logger.error("Failed to setup connection: %s", e)
        sys.exit(1)

    stats = {
        "updated":   0,
        "not_found": 0,
        "failed":    0,
        "error":     0,
        "total":     0,
    }

    try:
        while True:
            if args.limit and stats["total"] >= args.limit:
                logger.info("Reached specified limit of %d records. Stopping.", args.limit)
                break

            conn = db.ensure_connected(conn)

            # Fetch exactly 1 pending record from the central API
            records = db.get_pending_records(
                conn,
                batch_size=1,
                start_year=args.start_year,
                end_year=args.end_year
            )

            if not records:
                logger.info("No more pending records found. Queue complete!")
                break

            record = records[0]
            reg_no = record.get("registration_no", "Unknown")
            stats["total"] += 1
            current_count = stats["total"]

            logger.info("─" * 50)
            logger.info("[%d] Processing: %s (Year: %s, Serial: %s)", current_count, reg_no, record.get("year"), record.get("serial"))

            try:
                scraped_data, result_status = process_record_worker(record)
            except Exception as e:
                logger.exception("Unexpected error processing %s: %s", reg_no, e)
                scraped_data = {
                    "registration_no": reg_no,
                    "address": "ERROR",
                    "status": 3
                }
                result_status = "error"

            # Immediately save this single record to the database
            db.update_scraped_batch(conn, [scraped_data])
            stats[result_status] = stats.get(result_status, 0) + 1

            # Real-time status update
            elapsed = (datetime.now() - start_time).total_seconds()
            rate = stats["total"] / elapsed if elapsed > 0 else 0
            logger.info(
                "  ↳ Saved! Status: %s | Total: %d (✅ %d | 🔍 %d | ❌ %d) | Rate: %.1f rec/min",
                result_status.upper(), stats["total"], stats["updated"],
                stats["not_found"], stats["failed"] + stats["error"], rate * 60
            )

            # Polite delay between sequential requests
            if args.delay > 0:
                time.sleep(args.delay)

    except KeyboardInterrupt:
        logger.info("\nScraping stopped by user (Ctrl+C). Current record was saved.")
    finally:
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info("Sequential run complete in %.1f seconds (%.2f minutes)", elapsed, elapsed / 60)
        logger.info("  ✅ Successfully Scraped: %d", stats["updated"])
        logger.info("  🔍 Not Found on Site:   %d", stats["not_found"])
        logger.info("  ❌ Failed (CAPTCHA):     %d", stats["failed"])
        logger.info("  ⚠️  Errors:               %d", stats["error"])
        logger.info("  📊 Total Processed:      %d", stats["total"])
        logger.info("=" * 60)

        if conn and not config.USE_REMOTE_API and conn.is_connected():
            conn.close()
            logger.info("Database connection closed")


if __name__ == "__main__":
    main()
