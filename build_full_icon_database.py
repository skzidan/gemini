import os
import json
import re
import urllib.request
import urllib.parse
from pathlib import Path

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent
OUT_ITEMS = BASE_DIR / "templates" / "items"
OUT_MAPPED = BASE_DIR / "templates" / "currency_wars_mapped"
OUT_ITEMS.mkdir(parents=True, exist_ok=True)
OUT_MAPPED.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HSR-Icon-Builder/1.0"}

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()

# --- Part 1: Bulk-download all icons from StarRailRes ---
print("=== Part 1: Downloading all item and curio icons from StarRailRes ===")
github_api_base = "https://api.github.com/repos/Mar-7th/StarRailRes/contents"

def download_folder(folder_path, out_dir):
    api_url = f"{github_api_base}/{folder_path}?ref=master"
    try:
        data = json.loads(fetch(api_url).decode("utf-8"))
    except Exception as e:
        print(f"  [Error] Could not list {folder_path}: {e}")
        return 0
    count = 0
    for item in data:
        if item["type"] == "file" and item["name"].endswith(".png"):
            file_url = item["download_url"]
            filepath = out_dir / item["name"]
            if filepath.exists() and filepath.stat().st_size > 1000:
                continue
            try:
                filepath.write_bytes(fetch(file_url))
                print(f"  [OK] {item['name']}")
                count += 1
            except Exception as e:
                print(f"  [Error] {item['name']}: {e}")
    return count

total = 0
total += download_folder("icon/item", OUT_ITEMS)
total += download_folder("icon/curio", OUT_ITEMS)
print(f"Bulk download complete. {total} new icons saved to {OUT_ITEMS}\n")

# --- Part 2: Scrape Currency Wars equipment names from Fandom Wiki ---
print("=== Part 2: Scraping Currency Wars equipment images from Fandom Wiki ===")
WIKI_API = "https://honkai-star-rail.fandom.com/api.php"

def wiki_query(params):
    params["format"] = "json"
    query = urllib.parse.urlencode(params)
    url = f"{WIKI_API}?{query}"
    return json.loads(fetch(url).decode("utf-8"))

def get_all_page_images(page_title):
    """Fetch all image URLs on a page, keyed by their captions or adjacent text."""
    data = wiki_query({
        "action": "parse",
        "page": page_title,
        "prop": "images",
    })
    if "parse" not in data:
        print(f"  [Wiki Error] Page '{page_title}' not found.")
        return []

    images = data["parse"].get("images", [])
    urls = []
    for img in images:
        if not img.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        # Get the image info (URL)
        info = wiki_query({
            "action": "query",
            "titles": f"File:{img}",
            "prop": "imageinfo",
            "iiprop": "url",
        })
        pages = info.get("query", {}).get("pages", {})
        for page in pages.values():
            if "imageinfo" in page:
                urls.append((img, page["imageinfo"][0]["url"]))
    return urls

# The Fandom wiki stores Currency Wars equipment under this page
page_title = "Currency Wars: Zero-Sum Game/Equipment"
image_list = get_all_page_images(page_title)

if not image_list:
    print("  [Warning] No images found on the primary equipment page.")
    # Fallback: try the main Currency Wars page
    page_title = "Currency Wars: Zero-Sum Game"
    image_list = get_all_page_images(page_title)

def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")

downloaded = 0
for filename, url in image_list:
    # Derive a clean slug from the filename (e.g., "Anti-Grav_Boots_Icon.png" -> "anti_grav_boots")
    base = Path(filename).stem
    # Remove common suffixes
    base = re.sub(r"(_icon|_image|_equipment|_item)$", "", base, flags=re.IGNORECASE)
    slug = slugify(base)
    filepath = OUT_MAPPED / f"{slug}.png"

    if filepath.exists() and filepath.stat().st_size > 1000:
        continue

    try:
        filepath.write_bytes(fetch(url))
        print(f"  [OK] {slug}.png")
        downloaded += 1
    except Exception as e:
        print(f"  [Error] {slug}: {e}")

print(f"\n=== DONE ===")
print(f"Bulk icons (by ID): {OUT_ITEMS}")
print(f"Mapped Currency Wars icons: {OUT_MAPPED} ({downloaded} new)")
print("\nYou can now copy the files from 'currency_wars_mapped' into 'templates/items'")
print("if you want the bot to match them directly by name.")