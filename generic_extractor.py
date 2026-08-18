import json
import time
import sys
import re
import requests
import base64
import datetime
import os
import gc  # Added for strict memory management
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

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
    
    # Eager load strategy skips waiting for heavy CSS/Assets to load
    options.page_load_strategy = 'eager'
    
    # --- RENDER / SERVER HEADLESS OPTIONS ---
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage") # CRITICAL for 500MB limits
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--mute-audio") # Save processing power
    
    # --- EXTREME MEMORY SAVING FLAGS ---
    options.add_argument("--disable-extensions")
    options.add_argument("--no-zygote")
    options.add_argument("--disable-site-isolation-trials")
    options.add_argument("--js-flags=--max-old-space-size=128")
    
    # Block downloading of images, CSS, and fonts to save massive amounts of RAM
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
    """Finds the 10-digit UNIX timestamp in the URL (e.g., ,1786914000/)."""
    match = re.search(r',(\d{10})/', url)
    if match:
        return int(match.group(1))
    return 0 # 0 means could not parse, assume it needs immediate update

def main():
    print("=" * 70)
    print("  Smart M3U8 Renewer - Priority Expiry Checker (Render Optimized)")
    print("=" * 70)

    wait_sec = 10
    
    while True:
        gc.collect() # Clean up memory at the start of loop
        
        print("\n" + "=" * 50)
        print(f"Checking for expiring links at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        existing_data = read_current_data_from_github()
        if not existing_data or "data" not in existing_data:
            print("[-] No data found on GitHub. Waiting 1 minute...")
            time.sleep(60)
            continue

        all_videos = existing_data["data"]
        current_time = int(time.time())
        one_hour_limit = current_time + 3600 # 1 hour from now
        
        # 1. Identify videos that need renewal
        urgent_queue = []
        for idx, item in enumerate(all_videos):
            m3u8_links = item.get("m3u8_links", [])
            earliest_expiry = float('inf')
            
            if not m3u8_links:
                earliest_expiry = 0
            else:
                for link in m3u8_links:
                    exp = extract_expiry_timestamp(link)
                    if exp < earliest_expiry:
                        earliest_expiry = exp
            
            # If the earliest expiry is within 1 hour, or it's 0 (invalid/missing)
            if earliest_expiry <= one_hour_limit:
                urgent_queue.append({
                    "array_index": idx,
                    "expiry": earliest_expiry,
                    "item_data": item
                })

        # 2. Sort the queue so the lowest expiry (most urgent) is processed FIRST
        urgent_queue.sort(key=lambda x: x["expiry"])

        if not urgent_queue:
            print("[+] All links are healthy and have > 1 hour of lifetime left.")
            print("[ZzZ] Sleeping for 20 minutes before checking again...")
            time.sleep(1200)
            continue

        print(f"[!] Found {len(urgent_queue)} videos that are expired or expiring within 1 hour!")
        
        # 3. Process ALL urgent items to renew them completely
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
                time_status = "NO VALID LINK"
            elif time_left_mins < 0:
                time_status = f"EXPIRED {abs(time_left_mins)} mins ago"
            else:
                time_status = f"Expires in {time_left_mins} mins"

            print(f"\n[{priority_num}/{len(urgent_queue)}] Urgent: {time_status}")
            print(f"Opening: {vurl}")
            
            driver = None
            try:
                # --- EXTREME RAM PROTECTION ---
                # Open browser brand new for EVERY single video
                driver = create_driver()
                
                driver.get(vurl)
                time.sleep(2)
                clear_js(driver)
                inject(driver)
                
                end_t = time.time() + wait_sec
                round_found = []
                while time.time() < end_t:
                    for u in get_all_m3u8(driver):
                        if u not in GLOBAL_SEEN_M3U8:
                            GLOBAL_SEEN_M3U8.add(u)
                            round_found.append(u)
                    time.sleep(1)
                
                if round_found:
                    print(f"    [+] RENEWED! Got {len(round_found)} fresh m3u8 URL(s).")
                    # Replace the old m3u8 array with the newly fetched ones
                    all_videos[idx]["m3u8_links"] = round_found
                    updates_made_in_batch += 1
                    total_renewed += 1
                else:
                    print("    [-] Failed to renew link (no stream captured).")

            except Exception as e:
                print(f"    [-] Error processing page: {e}")
                
            finally:
                # --- CRITICAL MEMORY STEP ---
                # Kill Chrome completely after EVERY single video
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass
                
                # Strict RAM wipe
                gc.collect()

            # Upload to GitHub every 20 successful renewals to save progress safely
            if updates_made_in_batch >= 20:
                print(f"\n[+] Batch of 20 renewed. Uploading progress to GitHub...")
                existing_data["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                existing_data["data"] = all_videos
                update_links_in_github(existing_data)
                updates_made_in_batch = 0

        # 4. Upload any remaining updated data to GitHub
        if updates_made_in_batch > 0:
            print(f"\n[+] Finalizing upload... Saving remaining {updates_made_in_batch} renewals to GitHub.")
            existing_data["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            existing_data["data"] = all_videos
            update_links_in_github(existing_data)
        elif total_renewed == 0:
            print("\n[-] No successful renewals in this cycle.")

        # 5. Rest for 20 minutes after completing the full renewal cycle
        print(f"\n[!] Renewal cycle complete. Total URLs renewed: {total_renewed}")
        print("[ZzZ] Resting for 20 minutes (1200 seconds) before the next check...")
        time.sleep(1200)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Renewer stopped by user.")
        sys.exit(0)
