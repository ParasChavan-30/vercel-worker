from http.server import BaseHTTPRequestHandler
import json
import os
import re
import base64
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

COA_BASE_URL    = "https://coa.gov.in"
FORM_PAGE_URL   = f"{COA_BASE_URL}/search_arch2.php?lang=1&level=1&linkid=&lid=289&searCat=3"
POST_URL        = f"{COA_BASE_URL}/search_architectResult.php?lang=1&level=1&linkid=&lid=289"
CAPTCHA_IMG_URL = f"{COA_BASE_URL}/app/admin/captcha/php_captcha.php"
AJAX_CODE_URL   = f"{COA_BASE_URL}/app/admin/captcha/ajax_captcha.php"

BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

def call_server_api(payload):
    api_url = os.environ.get("REMOTE_API_URL")
    api_key = os.environ.get("REMOTE_API_KEY")
    resp = requests.post(
        api_url,
        json=payload,
        headers={"Content-Type": "application/json", "X-API-Key": api_key, "User-Agent": BROWSER_USER_AGENT},
        timeout=10
    )
    return resp.json()

def solve_captcha_remote(img_bytes):
    solver_url = os.environ.get("CAPTCHA_SOLVER_URL")
    if not solver_url:
        return None
    b64_img = base64.b64encode(img_bytes).decode('utf-8')
    resp = requests.post(solver_url, json={"image_base64": b64_img}, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        code = data.get("captcha_text") or data.get("text") or ""
        clean = re.sub(r'[^A-Za-z0-9]', '', code).strip()
        return clean[:6] if len(clean) >= 5 else None
    return None

def extract_fields(html):
    soup = BeautifulSoup(html, 'html.parser')
    row = soup.find('tr', class_=lambda c: c and 'grid' in c.lower())
    if not row:
        for tr in soup.find_all('tr'):
            if 'CA/' in tr.text and len(tr.find_all('td')) >= 4:
                row = tr
                break
    if not row:
        return {}

    cells = [td.get_text(separator=' ', strip=True) for td in row.find_all('td')]
    if len(cells) < 3:
        return {}

    reg_no, email, mobile, address, name = "", "", "", "", ""
    for c in cells:
        m = re.search(r'\bCA/\d{4}/\d+\b', c, re.I)
        if m and not reg_no: reg_no = m.group(0)

        em = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', c)
        if em and not email: email = em.group(0)

        digits = re.sub(r'[^0-9]', '', c)
        if len(digits) in (10, 11, 12) and '/' not in c and ',' not in c and not mobile:
            mobile = digits

        if not address and c not in (reg_no, email, mobile):
            if re.search(r'\b\d{6}\b', c) or (',' in c and len(c) > 20):
                address = c

    if not address and len(cells) >= 5:
        address = cells[4]

    for idx, c in enumerate(cells):
        if 'CA/' in c and idx > 0 and cells[idx-1] != '1':
            name = cells[idx-1]
            break
    if not name and len(cells) > 1:
        name = cells[1]

    return {"registration_no": reg_no, "name": name, "email": email, "mobile": mobile, "address": address}

def scrape_record(reg_no):
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_USER_AGENT})

    # Step 1: Prime session
    session.get(COA_BASE_URL, timeout=8)
    session.headers.update({"Referer": COA_BASE_URL + "/"})
    session.get(FORM_PAGE_URL, timeout=8)

    # Step 2: Captcha ajax & image
    session.get(f"{AJAX_CODE_URL}?sid=0.123", headers={"X-Requested-With": "XMLHttpRequest"}, timeout=8)
    img_resp = session.get(CAPTCHA_IMG_URL, timeout=8)
    if img_resp.status_code != 200:
        return {"status": "error", "message": "Failed to load captcha"}

    # Step 3: Solve captcha via Railway
    solved = solve_captcha_remote(img_resp.content)
    if not solved:
        return {"status": "failed", "message": "Captcha solver returned empty"}

    # Step 4: Submit search form
    post_data = {
        "val_sub": "1",
        "searCat": "3",
        "arc_reg": reg_no,
        "cap_code": "",
        "T3": solved,
        "submit": "Search"
    }
    resp = session.post(POST_URL, data=post_data, headers={"Origin": COA_BASE_URL, "Referer": FORM_PAGE_URL}, timeout=10)
    html = resp.text

    if "invalid captcha" in html.lower() or "wrong captcha" in html.lower():
        return {"status": "retry", "message": "Invalid captcha from COA"}

    if "no record found" in html.lower():
        call_server_api({"action": "save_scraped", "data": [{"registration_no": reg_no, "address": "NOT_FOUND", "status": 3}]})
        return {"status": "completed", "registration_no": reg_no, "result": "NOT_FOUND"}

    fields = extract_fields(html)
    addr = fields.get("address") or "NO_ADDRESS"
    call_server_api({
        "action": "save_scraped",
        "worker_id": os.environ.get("WORKER_ID", "vercel_platform4"),
        "data": [{
            "registration_no": reg_no,
            "name": fields.get("name", ""),
            "mobile": fields.get("mobile", ""),
            "email": fields.get("email", ""),
            "address": addr,
            "status": 2
        }]
    })
    return {"status": "completed", "registration_no": reg_no, "data": fields}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        action = query.get("action", [""])[0]
        reg = query.get("reg", [""])[0]

        # 1. Scrape specific registration: ?reg=CA/1996/133798
        if reg:
            result = scrape_record(reg.strip())
            return self._respond(result)

        # 2. Scrape next pending from DB: ?action=scrape
        if action == "scrape":
            worker_id = os.environ.get("WORKER_ID", "vercel_platform4")
            pending_resp = call_server_api({"action": "get_pending", "limit": 1, "worker_id": worker_id})
            records = pending_resp.get("data") or pending_resp.get("records") or []

            if not records:
                return self._respond({"status": "idle", "message": "No pending records found in DB"})

            target_reg = records[0].get("registration_no")
            result = scrape_record(target_reg)
            return self._respond({"worker": worker_id, "processed": target_reg, "result": result})

        # 3. Default: Health & Queue Status
        total_info = call_server_api({"action": "get_total_pending"})
        self._respond({
            "status": "online",
            "platform": "platform-4-vercel",
            "worker_id": os.environ.get("WORKER_ID", "vercel_platform4"),
            "total_pending_in_db": total_info.get("total_pending", "unknown"),
            "actions": {
                "scrape_next": "/api?action=scrape",
                "scrape_single": "/api?reg=CA/1996/133798"
            }
        })

    def _respond(self, data, status_code=200):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))
