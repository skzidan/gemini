import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Configuration
CATEGORY = "Category:Currency_Wars_Character_Icons"
OUT_DIR = Path("templates/units")
OUT_DIR.mkdir(parents=True, exist_ok=True)

API_URL = "https://honkai-star-rail.fandom.com/api.php"

# FIXED: Using a real browser User-Agent and Referer to bypass 403 Forbidden
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://honkai-star-rail.fandom.com/",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
}

def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")

print("1. Fetching category members from Fandom API...")
members_url = (
    f"{API_URL}?action=query&list=categorymembers"
    f"&cmtitle={urllib.parse.quote(CATEGORY)}&cmlimit=500&format=json"
)
data = fetch_json(members_url)

if "query" not in data or "categorymembers" not in data["query"]:
    print("Error: Could not fetch category members.")
    exit(1)

members = data["query"]["categorymembers"]
file_titles = [m["title"] for m in members if m["title"].startswith("File:")]
print(f"Found {len(file_titles)} file pages.")

if not file_titles:
    print("No files found.")
    exit(1)

print("\n2. Fetching image URLs...")
chunk_size = 50
success_count = 0

for i in range(0, len(file_titles), chunk_size):
    chunk = file_titles[i:i + chunk_size]
    titles_str = "|".join(chunk)
    
    info_url = (
        f"{API_URL}?action=query&titles={urllib.parse.quote(titles_str)}"
        f"&prop=imageinfo&iiprop=url&format=json"
    )
    try:
        info_data = fetch_json(info_url)
        pages = info_data.get("query", {}).get("pages", {})
        
        for page_id, page_info in pages.items():
            if "imageinfo" not in page_info:
                continue
                
            image_url = page_info["imageinfo"][0]["url"]
            title = page_info["title"]
            
            # Extract name: "File:Character Aglaea Currency Wars Icon.png" -> "aglaea"
            raw_name = title.replace("File:", "")
            raw_name = re.sub(r"\.(png|jpg|jpeg|webp)$", "", raw_name, flags=re.IGNORECASE)
            raw_name = raw_name.replace(" Currency Wars Icon", "").replace(" Currency Wars", "")
            raw_name = raw_name.replace("Character ", "").replace(" Icon", "").strip()
            
            slug = slugify(raw_name)
            if not slug:
                continue
                
            filepath = OUT_DIR / f"{slug}.png"
            
            if filepath.exists() and filepath.stat().st_size > 1000:
                print(f"  [Skip] {slug}.png already exists")
                success_count += 1
                continue
                
            try:
                req = urllib.request.Request(image_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    filepath.write_bytes(resp.read())
                print(f"  [OK] Downloaded {slug}.png")
                success_count += 1
                time.sleep(0.5) # Polite delay
            except Exception as e:
                print(f"  [Error] Failed to download {slug}: {e}")
                
    except Exception as e:
        print(f"  [API Error] Failed to fetch batch {i//chunk_size}: {e}")

print(f"\nDone! Successfully downloaded {success_count} icons to {OUT_DIR}.")