import json
import time
import sys
import re
import datetime
import os
import gc  
import threading 
import collections 
import traceback
from urllib.parse import urlparse, parse_qs 
from http.server import BaseHTTPRequestHandler, HTTPServer 

# ==================== CRASH CATCHER ====================
def global_crash_handler(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(f"\n[CRITICAL BOT CRASH]\n{error_msg}")
    sys.__stdout__.flush()
    sys.__stderr__.flush()

sys.excepthook = global_crash_handler
# =======================================================

print("Starting script execution... (Logging fixed for Render)")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from webdriver_manager.chrome import ChromeDriverManager
    print("Selenium imported successfully.")
except ImportError as e:
    print(f"Error importing selenium: {e}")
    sys.exit(1)

try:
    import pymongo
    from pymongo import MongoClient
    print("pymongo imported successfully.")
except ImportError as e:
    print(f"Error importing pymongo: {e}")
    sys.exit(1)

# ==================== LOG CAPTURE SYSTEM ====================
log_buffer = collections.deque(maxlen=500)

class OutputCapturer:
    def write(self, text):
        log_buffer.append(text)
        # FORCE logs to Render Terminal so we can see why it crashes
        try:
            sys.__stdout__.write(text)
            sys.__stdout__.flush()
        except:
            pass

    def flush(self):
        try:
            sys.__stdout__.flush()
        except:
            pass

sys.stdout = OutputCapturer()
sys.stderr = OutputCapturer()
# ==============================================================

print("Environment setup complete. Checking MongoDB connection...")

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://santosh93348218_db_user:tvRiOqTjx2xYZn8J@secure.uetzpg8.mongodb.net/?retryWrites=true&w=majority&appName=secure")
DB_NAME = "scraper_db"       
COLLECTION_NAME = "videos"   

try:
    print("[*] Connecting to MongoDB with TLS SSL configuration...")
    mongo_client = MongoClient(
        MONGO_URI, 
        serverSelectionTimeoutMS=10000,
        tls=True,
        tlsAllowInvalidCertificates=True
    )
    mongo_client.server_info() # Test connection
    db = mongo_client[DB_NAME]
    collection = db[COLLECTION_NAME]
    print("[+] MongoDB connected successfully!")
except Exception as e:
    print(f"[-] MongoDB connection failed: {e}")
    print("CHECK YOUR MONGODB ATLAS NETWORK ACCESS! Set IP Access List to 0.0.0.0/0")
    sys.exit(1)

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
    try:
        server = HTTPServer(('0.0.0.0', port), DummyHandler)
        print(f"[*] Started dummy web server on port {port} to satisfy Render.")
        server.serve_forever()
    except Exception as e:
         print(f"[-] Dummy server failed to start: {e}")

def create_driver():
    options = Options()
    options.page_load_strategy = 'eager'
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--mute-audio")
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

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        print(f"Error creating driver with webdriver-manager: {e}")
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
    if not url: return 0
    match = re.search(r',(\d{10})/', url)
    if match: return int(match.group(1))
    match = re.search(r'[?&_,-](?:exp|expires|expiry|valid|token|t)?=?([1-9]\d{9})(?:[\/,=_&?-]|$)', url, re.IGNORECASE)
    if match: return int(match.group(1))
    match = re.search(r'(?:^|[^0-9])(1[6-9]\d{8})(?:[^0-9]|$)', url)
    if match: return int(match.group(1))
    return 0 

def main():
    print("=" * 70)
    print("  Smart M3U8 Renewer - Priority Expiry Checker (MongoDB + Render)")
    print("=" * 70)

    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()

    wait_sec = 10
    
    while True:
        gc.collect()
        print("\n" + "=" * 50)
        print(f"Checking for expiring links at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            all_videos = list(collection.find({}))
        except Exception as e:
            print(f"[-] Failed to read from MongoDB: {e}")
            time.sleep(60)
            continue

        if not all_videos:
            print("[-] No data found in MongoDB collection. Waiting 1 minute...")
            time.sleep(60)
            continue

        current_time = int(time.time())
        thirty_min_limit = current_time + 1800 

        expired_queue = []       
        expiring_soon_queue = [] 

        for idx, item in enumerate(all_videos):
            raw_m3u8_links = item.get("m3u8_links", [])
            m3u8_links = [link for link in raw_m3u8_links if "svacdn.tsyndicate.com" not in link]
            item["m3u8_links"] = m3u8_links 
            
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

            if earliest_expiry <= current_time:
                expired_queue.append({"expiry": earliest_expiry, "item_data": item})
            elif earliest_expiry <= thirty_min_limit:
                expiring_soon_queue.append({"expiry": earliest_expiry, "item_data": item})

        expired_queue.sort(key=lambda x: x["expiry"])
        expiring_soon_queue.sort(key=lambda x: x["expiry"])

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
        total_renewed = 0

        for priority_num, urgent_item in enumerate(urgent_queue, 1):
            video = urgent_item["item_data"]
            expiry = urgent_item["expiry"]
            vurl = video.get("source_page")
            doc_id = video.get("_id") 
            
            if not vurl or not doc_id:
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
                        if "svacdn.tsyndicate.com" in u: continue
                        if u not in GLOBAL_SEEN_M3U8:
                            GLOBAL_SEEN_M3U8.add(u)
                            round_found.append(u)
                    time.sleep(1)
                
                if round_found:
                    print(f"    [+] RENEWED! Got {len(round_found)} fresh m3u8 URL(s).")
                    try:
                        collection.update_one(
                            {"_id": doc_id}, 
                            {"$set": {
                                "m3u8_links": round_found,
                                "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }}
                        )
                        print("    [+] DB Updated successfully.")
                        total_renewed += 1
                    except Exception as db_err:
                        print(f"    [-] DB Update Failed: {db_err}")
                else:
                    print("    [-] Failed to renew link (no stream captured).")

            except Exception as e:
                print(f"    [-] Error processing page: {e}")
                
            finally:
                if driver:
                    try: driver.quit()
                    except: pass
                gc.collect()

            if priority_num % 2 == 0 and priority_num < len(urgent_queue):
                print("[ZzZ] Resting for 1 minute (60 seconds) before processing next batch...")
                time.sleep(60)

        if total_renewed == 0:
            print("\n[-] No successful renewals in this cycle.")

        print(f"\n[!] Renewal cycle complete. Total URLs renewed and updated in DB: {total_renewed}")
        print("[ZzZ] Resting for 20 minutes (1200 seconds) before next check...")
        time.sleep(1200)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Renewer stopped by user.")
        sys.exit(0)
