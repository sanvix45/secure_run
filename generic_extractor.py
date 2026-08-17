import json
import time
import sys
import re
import requests
import base64
import datetime
import threading
import queue
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

GLOBAL_SEEN_M3U8 = set()
SCANNED_SEEDS = set() # Memory to track which pages we have already checked for related videos

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

def read_current_data_from_github():
    """Reads the existing source.json from GitHub to prevent deleting old data."""
    try:
        response = requests.get(RAW_URL)
        if response.status_code == 200:
            print("[+] Successfully fetched existing data from GitHub.")
            try:
                return response.json()
            except requests.exceptions.JSONDecodeError:
                print("[-] Error: GitHub file exists but is not valid JSON.")
                return None
        elif response.status_code == 404:
            print("[!] GitHub file not found. A new one will be created.")
            return None
    except Exception as e:
        print(f"[-] Error reading from GitHub: {e}")
        return None
    return None

def update_links_in_github(new_data_dict):
    """Updates the target JSON file in the GitHub repository."""
    response = requests.get(API_URL, headers=HEADERS)
    
    sha = None
    if response.status_code == 200:
        file_info = response.json()
        sha = file_info['sha']
    elif response.status_code == 404:
        print("[!] New file will be created on GitHub.")
    else:
        print(f"[-] Error checking file: {response.status_code}, {response.text}")
        return False

    json_string = json.dumps(new_data_dict, indent=4)
    encoded_bytes = base64.b64encode(json_string.encode('utf-8'))
    encoded_content = encoded_bytes.decode('utf-8')

    payload = {
        "message": "Auto-updated m3u8 links & source pages via Python Scraper",
        "content": encoded_content
    }
    
    if sha:
        payload["sha"] = sha

    update_response = requests.put(API_URL, headers=HEADERS, json=payload)
    
    if update_response.status_code in [200, 201]:
        print("[+] Successfully Updated! File saved to GitHub repository.")
        return True
    else:
        print(f"[-] Update failed: {update_response.status_code}")
        try:
            print(update_response.json())
        except requests.exceptions.JSONDecodeError:
            print("Response text:", update_response.text)
        return False

def create_driver():
    """Initializes Chrome webdriver with network performance logging."""
    options = Options()
    # --- RENDER / SERVER HEADLESS OPTIONS ---
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    # Fake User-Agent so website doesn't block headless Chrome
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=options)
    driver.execute_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    )
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass
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
    except Exception:
        pass
    return found

def get_all_m3u8(driver):
    """Combines injected JS listener results and performance log tracking to collect all m3u8 stream links."""
    urls = []
    urls.extend(read_js(driver))
    urls.extend(read_perf(driver))
    return list(set(urls))

def extract_initials_videos(driver):
    """Parses window.initials layoutPage.videoListProps.videoThumbProps to grab direct video page URLs, titles, and images."""
    try:
        initials_data = driver.execute_script("""
            return window.initials && window.initials.layoutPage && 
                   window.initials.layoutPage.videoListProps && 
                   window.initials.layoutPage.videoListProps.videoThumbProps;
        """)
        if initials_data and isinstance(initials_data, list):
            video_entries = []
            for item in initials_data:
                page_url = item.get("pageURL")
                title = item.get("title")
                duration = item.get("duration")
                # Grabbing the image/thumbnail URL using standard properties
                image_url = item.get("thumbURL") or item.get("imageURL") or item.get("previewURL") or "Unknown"
                
                if page_url:
                    video_entries.append({"url": page_url, "title": title, "duration": duration, "image_url": image_url})
            return video_entries
    except Exception as e:
        print(f"[-] Error extracting window.initials: {e}")
    return []

def main():
    print("=" * 70)
    print("  Spider Auto-Scraper (Crawls Related Videos Infinitely)")
    print("=" * 70)

    driver = create_driver()
    wait_sec = 10
    
    while True:
        print("\n" + "=" * 60)
        print(f"Starting new Spider cycle at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        existing_data_dict = read_current_data_from_github()
        
        if not existing_data_dict:
            print("[-] No data found on GitHub. Retrying in 10 minutes...")
            time.sleep(600)
            continue

        existing_videos = existing_data_dict.get("data", [])
        
        # Extract ALL URLs currently in the database
        all_urls = [vid.get("source_page") for vid in existing_videos if vid.get("source_page")]
        
        if not all_urls:
            print("[-] No 'source_page' URLs found in your database.")
            print("    Please manually add at least 1 video entry to source.json so the spider has a starting point.")
            print("    Resting 10 minutes...")
            time.sleep(600)
            continue

        # Find URLs we haven't scanned in this session
        unscanned_urls = [u for u in all_urls if u not in SCANNED_SEEDS]

        # If we have scanned everything, clear memory to start over (related videos change over time)
        if not unscanned_urls:
            print("[!] All existing URLs have been scanned. Clearing memory to re-scan for new related videos!")
            SCANNED_SEEDS.clear()
            unscanned_urls = all_urls

        # Pick exactly ONE unscanned URL to process right now
        seed_url = unscanned_urls[0]
        SCANNED_SEEDS.add(seed_url)

        print(f"\n[>] Scanning video for NEW related videos: {seed_url}")
        print(f"    (Remaining unscanned in this cycle: {len(unscanned_urls) - 1})")
        
        try:
            driver.get(seed_url)
            time.sleep(3)
        except Exception as e:
            print(f"[-] Failed to load seed page: {e}")
            continue

        # Grab all related videos shown on this page
        video_list = extract_initials_videos(driver)
        
        # Fallback DOM search if JS initials fail
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='/videos/']")
            for el in elements:
                href = el.get_attribute("href")
                # Ensure it's a valid video link and not already in our temporary list
                if href and "xhamster46.desi/videos/" in href and not any(v.get("url") == href for v in video_list):
                    image_url = "Unknown"
                    title = "Unknown"
                    try:
                        img = el.find_element(By.TAG_NAME, "img")
                        image_url = img.get_attribute("src") or "Unknown"
                        title = img.get_attribute("alt") or "Unknown"
                    except: pass
                    video_list.append({"url": href, "title": title, "duration": "Unknown", "image_url": image_url})
        except Exception: pass

        if not video_list:
            print("    [-] No related videos found on this page.")
            continue

        # Fetch LATEST data from GitHub right before checking duplicates
        latest_data_dict = read_current_data_from_github()
        if latest_data_dict and "data" in latest_data_dict:
            existing_videos = latest_data_dict["data"]

        # Filter out videos that are ALREADY in our database (Duplicate check)
        new_related_videos = []
        for v in video_list:
            if not any(ext_vid.get("source_page") == v["url"] for ext_vid in existing_videos):
                new_related_videos.append(v)
                
        if not new_related_videos:
            print("    [-] No NEW related videos found here (all already in database). Moving immediately to next URL...")
            # Loop restarts instantly to grab the next seed URL without waiting 10 minutes
            continue

        print(f"    [+] Found {len(new_related_videos)} completely NEW videos here! Processing each one...")

        # Open each NEW related video to get its m3u8 link
        newly_captured_videos = []
        
        for idx, item in enumerate(new_related_videos, 1):
            vurl = item.get("url")
            vtitle = item.get("title", "Unknown")
            vimage = item.get("image_url", "Unknown")
            
            print(f"\n        [{idx}/{len(new_related_videos)}] Extracting New Video: {vurl}")
            try:
                driver.get(vurl)
                time.sleep(2)
                clear_js(driver)
                inject(driver)
                
                GLOBAL_SEEN_M3U8.clear()
                end_t = time.time() + wait_sec
                round_found = []
                
                while time.time() < end_t:
                    for u in get_all_m3u8(driver):
                        if u not in GLOBAL_SEEN_M3U8:
                            GLOBAL_SEEN_M3U8.add(u)
                            round_found.append(u)
                    time.sleep(1)
                
                if round_found:
                    print(f"            [+] Success! Captured {len(round_found)} m3u8 URL(s).")
                    # Add to our temporary list for this specific source page
                    newly_captured_videos.append({
                        "source_page": vurl,
                        "title": vtitle,
                        "image_url": vimage,
                        "m3u8_links": round_found
                    })
                else:
                    print("            [-] No m3u8 stream captured for this video.")
            except Exception as e:
                print(f"            [-] Error processing new video page: {e}")
                
        # Upload to GitHub immediately after finishing ALL new videos from THIS single source_page
        if newly_captured_videos:
            print(f"\n[+] Finished source page. Captured {len(newly_captured_videos)} new videos.")
            print("[+] Fetching absolute latest DB to merge safely without overwriting Renewer...")
            
            final_db = read_current_data_from_github()
            if final_db and "data" in final_db:
                final_db["data"].extend(newly_captured_videos)
                final_db["total_videos"] = len(final_db["data"])
                final_db["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                update_links_in_github(final_db)
            else:
                # Fallback if fetch failed
                existing_videos.extend(newly_captured_videos)
                existing_data_dict["data"] = existing_videos
                existing_data_dict["total_videos"] = len(existing_videos)
                existing_data_dict["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                update_links_in_github(existing_data_dict)

            # Rest for exactly 10 minutes between source pages ONLY IF we found and added new videos
            print("\n[ZzZ] Upload successful! Resting for 10 minutes (600 seconds) before processing the next source page...")
            time.sleep(600)
        else:
            print("\n[-] No valid m3u8s captured from this source page's related videos. Moving to next...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Script stopped by user.")
        sys.exit(0)
