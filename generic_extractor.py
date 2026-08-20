import json
import time
import sys
import re
import requests
import base64
import datetime
import os
import gc  # Added for strict memory management
import threading # Added for dummy server
import collections # Added for log memory buffer
from urllib.parse import urlparse, parse_qs # Added to check ?logs=logs
from http.server import BaseHTTPRequestHandler, HTTPServer # Added for dummy server
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==================== LOG CAPTURE SYSTEM ====================
# Memory buffer saving last 500 log lines
log_buffer = collections.deque(maxlen=500)

class OutputCapturer:
    def __init__(self, original_stream):
        self.original_stream = original_stream

    def write(self, text):
        log_buffer.append(text)

    def flush(self):
        self.original_stream.flush()

# Redirect standard output and errors to custom capturer
sys.stdout = OutputCapturer(sys.stdout)
sys.stderr = OutputCapturer(sys.stderr)
# ==============================================================

# ==================== GITHUB CONFIGURATION ====================
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    print("[-] Error: GITHUB_TOKEN environment variable is not set. Please set it before running.")
    sys.exit(1)

REPO_OWNER = "st3084907-jpg"
REPO_NAME = "sdata"
FILE_NAME = "source.json"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{FILE_NAME}"
RAW_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/{FILE_NAME}"
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}
# ==============================================================

GLOBAL_SEEN_M3U8 = set()

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        if query_params.get('logs') == ['logs']:
            logs_text = "".join(log_buffer)
            html = f"<html><body style='background:#121212;color:#00FF00;font-family:monospace;padding:20px;'><pre>{logs_text}</pre></body></html>"
            self.wfile.write(html.encode('utf-8'))
        else:
            self.wfile.write(b"Renewer Service is Running")
            
    def log_message(self, format, *args):
        pass

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"[*] Started dummy web server on port {port} to satisfy Render.")
    server.serve_forever()

def read_current_data_from_github():
    """Reads the existing source.json from GitHub."""
    try:
        response = requests.get(RAW_URL)
        if response.status_code == 200:
            try:
                return response.json()
            except requests.exceptions.JSONDecodeError:
                print("[-] Error: GitHub file exists but is not valid JSON.")
                return None
        else:
            print(f"[-] Could not find or read {FILE_NAME} on GitHub.")
            return None
    except Exception as e:
        print(f"[-] Error reading from GitHub: {e}")
        return None

def update_links_in_github(new_data_dict):
    """Updates the target JSON file in the GitHub repository."""
    response = requests.get(API_URL, headers=HEADERS)
    sha = None
    if response.status_code == 200:
        sha = response.json()['sha']

    json_string = json.dumps(new_data_dict, indent=4)
    encoded_bytes = base64.b64encode(json_string.encode('utf-8'))
    encoded_content = encoded_bytes.decode('utf-8')

    payload = {
        "message": "Auto-Renewed expiring m3u8 links via Smart Python Scraper",
        "content": encoded_content
    }
    if sha:
        payload["sha"] = sha

    update_response = requests.put(API_URL, headers=HEADERS, json=payload)
    if update_response.status_code in [200, 201]:
        print("[+] Successfully Updated! Urgent links saved to GitHub.")
        return True
    else:
        print(f"[-] Update failed: {update_response.status_code}")
        return False

def create_driver():
    """Initializes Chrome webdriver with extreme low-memory configuration for Render."""
    options = Options()
    
    options.page_load_strategy = 'eager'
    
    # --- RENDER / SERVER HEADLESS OPTIONS ---
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--mute-audio")
    
    # --- EXTREME MEMORY SAVING FLAGS ---
    options.add_argument("--disable-extensions")
    options.add_argument("--no-zygote")
    options.add_argument("--disable-site-isolation-trials")
    options.add_argument("--js-flags=--max-old-space-size=128")
    
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheet": 2,
        "profile.managed_default_content_settings.fonts": 2,
        "profile.managed_default_content_settings.popups": 2
    }
    options.add_experimental_option("prefs", prefs)

    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=options)
    driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    try: driver.execute_cdp_cmd("Network.enable", {})
    except: pass
    return driver

JS_INJECT = """
if (!window._cw_m3u8) {
    window._cw_m3u8 = [];
    const _origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(m, url) {
        const u = (url||'').toString();
        if (u.includes('.m3u8')) window._cw_m3u8.push(u);
        return _origOpen.apply(this, arguments);
    };
    const _origFetch = window.fetch;
    window.fetch = function(res, init) {
        const u = (res||'').toString();
        if (u.includes('.m3u8')) window._cw_m3u8.push(u);
        return _origFetch.apply(this, arguments);
    };
}
"""

def inject(driver):
    try: driver.execute_script(JS_INJECT)
    except: pass

def read_js(driver):
    try: return driver.execute_script("return window._cw_m3u8 || [];") or []
    except: return []

def clear_js(driver):
    try: driver.execute_script("window._cw_m3u8 = [];")
    except: pass

def read_perf(driver):
    found = []
    try:
        for e in driver.get_log("performance"):
            msg = json.loads(e["message"])["message"]
            if msg.get("method") in ("Network.requestWillBeSent", "Network.responseReceived"):
                p = msg.get("params", {})
                url = (p.get("request") or p.get("response") or {}).get("url","")
                if ".m3u8" in url:
                    found.append(url)
    except: pass
    return found

def get_all_m3u8(driver):
    urls = []
    urls.extend(read_js(driver))
    urls.extend(read_perf(driver))
    return list(set(urls))

def extract_expiry_timestamp(url):
    """Finds the 10-digit UNIX timestamp in the URL (e.g., ,1786914000/ or exp=1786914000)."""
    if not url:
        return 0
        
    # Standard xHamster path pattern: ,1786914000/
    match = re.search(r',(\d{10})/', url)
    if match:
        return int(match.group(1))
        
    # Query parameters pattern: exp=1786914000, expires=1786914000, t=1786914000
    match = re.search(r'[?&_,-](?:exp|expires|expiry|valid|token|t)?=?([1-9]\d{9})(?:[\/,=_&?-]|$)', url, re.IGNORECASE)
    if match:
        return int(match.group(1))
        
    # Generic UNIX timestamp fallback (10 digits starting with 16, 17, 18, 19)
    match = re.search(r'(?:^|[^0-9])(1[6-9]\d{8})(?:[^0-9]|$)', url)
    if match:
        return int(match.group(1))
        
    return 0 # 0 means invalid or missing timestamp

def main():
    print("=" * 70)
    print("  Smart M3U8 Renewer - Priority Expiry Checker (Render Optimized)")
    print("=" * 70)

    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()

    wait_sec = 10
    
    while True:
        gc.collect()
        
        print("\n" + "=" * 50)
        print(f"Checking for expiring links at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        existing_data = read_current_data_from_github()
        if not existing_data or "data" not in existing_data:
            print("[-] No data found on GitHub. Waiting 1 minute...")
            time.sleep(5)
            continue

        all_videos = existing_data["data"]
        current_time = int(time.time())
        thirty_min_limit = current_time + 1800 # 30 minutes window

        expired_queue = []       # Stage 1: Already expired / invalid / missing links
        expiring_soon_queue = [] # Stage 2: Valid now, but expiring in 0-30 minutes

        for idx, item in enumerate(all_videos):
            # CLEANUP: Remove ads domains from existing data before checking
            raw_m3u8_links = item.get("m3u8_links", [])
            m3u8_links = [link for link in raw_m3u8_links if "svacdn.tsyndicate.com" not in link]
            item["m3u8_links"] = m3u8_links # Update item to permanently remove ads
            
            earliest_expiry = float('inf')
            
            if not m3u8_links:
                earliest_expiry = 0
            else:
                for link in m3u8_links:
                    exp = extract_expiry_timestamp(link)
                    if exp < earliest_expiry:
                        earliest_expiry = exp
            
            if earliest_expiry == float('inf'):
                earliest_expiry = 0

            # Priority Categorization Logic
            if earliest_expiry <= current_time:
                # Expired or Invalid link
                expired_queue.append({
                    "array_index": idx,
                    "expiry": earliest_expiry,
                    "item_data": item
                })
            elif earliest_expiry <= thirty_min_limit:
                # Expiring in 0 - 30 minutes
                expiring_soon_queue.append({
                    "array_index": idx,
                    "expiry": earliest_expiry,
                    "item_data": item
                })

        # Sort both queues by lowest expiry time
        expired_queue.sort(key=lambda x: x["expiry"])
        expiring_soon_queue.sort(key=lambda x: x["expiry"])

        # Decide which queue to process
        if expired_queue:
            print(f"[!] FOUND EXPIRED LINKS: {len(expired_queue)} videos have EXPIRED or INVALID links. Processing EXPIRED queue first!")
            urgent_queue = expired_queue
        elif expiring_soon_queue:
            print(f"[!] NO EXPIRED LINKS: Found {len(expiring_soon_queue)} videos expiring within 0-30 minutes. Processing 0-30m queue!")
            urgent_queue = expiring_soon_queue
        else:
            print("[+] All links are healthy and have > 30 mins of lifetime left.")
            print("[ZzZ] Sleeping for 20 minutes before checking again...")
            time.sleep(1200)
            continue

        GLOBAL_SEEN_M3U8.clear()
        updates_made_in_batch = 0
        total_renewed = 0

        for priority_num, urgent_item in enumerate(urgent_queue, 1):
            idx = urgent_item["array_index"]
            video = urgent_item["item_data"]
            expiry = urgent_item["expiry"]
            vurl = video.get("source_page")
            
            if not vurl:
                continue

            time_left_mins = (expiry - current_time) // 60
            if expiry == 0:
                time_status = "NO VALID LINK / UNKNOWN"
            elif time_left_mins < 0:
                time_status = f"EXPIRED {abs(time_left_mins)} mins ago"
            else:
                time_status = f"Expires in {time_left_mins} mins"

            print(f"\n[{priority_num}/{len(urgent_queue)}] Priority Target: {time_status}")
            print(f"Opening: {vurl}")
            
            driver = None
            try:
                driver = create_driver()
                
                driver.get(vurl)
                time.sleep(2)
                clear_js(driver)
                inject(driver)
                
                end_t = time.time() + wait_sec
                round_found = []
                while time.time() < end_t:
                    for u in get_all_m3u8(driver):
                        if "svacdn.tsyndicate.com" in u:
                            continue # Ignore fast-expiring ad links
                        if u not in GLOBAL_SEEN_M3U8:
                            GLOBAL_SEEN_M3U8.add(u)
                            round_found.append(u)
                    time.sleep(1)
                
                if round_found:
                    print(f"    [+] RENEWED! Got {len(round_found)} fresh m3u8 URL(s).")
                    all_videos[idx]["m3u8_links"] = round_found
                    updates_made_in_batch += 1
                    total_renewed += 1
                else:
                    print("    [-] Failed to renew link (no stream captured).")

            except Exception as e:
                print(f"    [-] Error processing page: {e}")
                
            finally:
                if driver:
                    try: driver.quit()
                    except: pass
                gc.collect()

            if priority_num % 10 == 0:
                if updates_made_in_batch > 0:
                    print(f"\n[+] 10 links processed! Direct GitHub update triggered ({updates_made_in_batch} renewed in this batch)...")
                    existing_data["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    existing_data["data"] = all_videos
                    update_links_in_github(existing_data)
                    updates_made_in_batch = 0
                else:
                    print("\n[!] 10 links processed (no new renewals captured in this batch).")

                if priority_num < len(urgent_queue):
                    print("[ZzZ] Resting for 1 minute (60 seconds) before processing next 10 links...")
                    time.sleep(60)

        if updates_made_in_batch > 0:
            print(f"\n[+] Finalizing upload... Saving remaining {updates_made_in_batch} renewals to GitHub.")
            existing_data["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            existing_data["data"] = all_videos
            update_links_in_github(existing_data)
        elif total_renewed == 0:
            print("\n[-] No successful renewals in this cycle.")

        print(f"\n[!] Renewal cycle complete. Total URLs renewed: {total_renewed}")
        print("[ZzZ] Resting for 20 minutes (1200 seconds) before next check...")
        time.sleep(1200)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Renewer stopped by user.")
        sys.exit(0)
