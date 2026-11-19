# COA Address Scraper — Configuration

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────── Database & Remote Server API ───────────────────
USE_REMOTE_API  = os.getenv("USE_REMOTE_API", "true").lower() in ("true", "1", "yes")
REMOTE_API_URL  = os.getenv("REMOTE_API_URL", "https://site40243-dn3p3f.scloudsite101.com/Architecture/api.php")
REMOTE_API_KEY  = os.getenv("REMOTE_API_KEY", "coa_worker_secret_key_2026")
WORKER_ID       = os.getenv("WORKER_ID", "local_worker_paras")

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "3307"))
DB_NAME     = os.getenv("DB_NAME", "coa_scraper")
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Groq is kept for text tasks; vision CAPTCHA solving now uses Google Gemini
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")  # Optional — not used for vision anymore

# ─────────────────── Google Gemini (Vision / CAPTCHA solver) ───────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.5-flash"  # Vision-capable Gemini model

# ─────────────────── COA URLs ───────────────────
BASE_URL       = "https://coa.gov.in"
FORM_PAGE_URL  = f"{BASE_URL}/search_arch2.php?lang=1&level=1&linkid=&lid=289&searCat=3"
POST_URL       = f"{BASE_URL}/search_architectResult.php?lang=1&level=1&linkid=&lid=289"
CAPTCHA_IMG_URL = f"{BASE_URL}/app/admin/captcha/php_captcha.php"
AJAX_CODE_URL  = f"{BASE_URL}/app/admin/captcha/ajax_captcha.php"

# ─────────────────── Scraper Settings ───────────────────
MAX_WORKERS            = int(os.getenv("MAX_WORKERS", "10"))  # Concurrent worker threads
MAX_CAPTCHA_RETRIES    = 3      # Max retries per record on CAPTCHA failure
BATCH_SIZE             = 200    # Records fetched per DB query cycle
BATCH_WRITE_SIZE       = 20     # Records buffered before flushing bulk updates to DB/API
SLEEP_MIN              = float(os.getenv("SLEEP_MIN", "0.1"))  # Seconds delay between worker tasks (min)
SLEEP_MAX              = float(os.getenv("SLEEP_MAX", "0.3"))  # Seconds delay between worker tasks (max)
SESSION_TIMEOUT        = 20     # HTTP request timeout (seconds)
GROQ_MAX_TOKENS        = 20     # Max tokens for Groq CAPTCHA response

# ─────────────────── Proxy Settings ───────────────────
PROXIES_FILE = os.getenv("PROXIES_FILE", "proxies.txt")
PROXIES = []

if os.path.exists(PROXIES_FILE):
    with open(PROXIES_FILE, "r", encoding="utf-8") as f:
        PROXIES = [line.strip() for line in f if line.strip() and not line.startswith("#")]

# ─────────────────── Browser Headers ───────────────────
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}
