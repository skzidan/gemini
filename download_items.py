import os
import json
import re
import urllib.request
from pathlib import Path

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")

# Base paths
BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "templates" / "items"
STRATEGY_FILE = BASE_DIR / "hsr_currency_wars_v4_7_strategies-v4.json"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 1. Extract required item names from the strategy JSON
required_item_slugs = set()
if STRATEGY_FILE.exists():
    with open(STRATEGY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        # Extract crafting components
        for recipe in data.get("crafting_recipes", []):
            for comp in recipe.get("components", []):
                required_item_slugs.add(slugify(comp))
        # Extract priority items for units
        for strategy in data.get("strategies", []):
            for pos in strategy.get("unit_positioning", []):
                for item in pos.get("priority_items", []):
                    required_item_slugs.add(slugify(item))

print(f"Found {len(required_item_slugs)} required items from your strategy JSON.")
print(", ".join(required_item_slugs) if required_item_slugs else "(None)")

# 2. Fetch the full HSR item database
print("\nFetching item database from StarRailRes...")
items_data_url = "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/index_min/en/items.json"

# Pretend to be a browser to avoid GitHub blocking the request
opener = urllib.request.build_opener()
opener.addheaders = [('User-agent', 'Mozilla/5.0')]
urllib.request.install_opener(opener)

try:
    with urllib.request.urlopen(items_data_url, timeout=15) as response:
        items_db = json.loads(response.read().decode('utf-8'))
    print(f"Successfully loaded item database ({len(items_db)} items).")
except Exception as e:
    print(f"[Error] Failed to fetch item data: {e}")
    exit(1)

# 3. Match names and download icons
success_count = 0
base_icon_url = "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master"

for slug in required_item_slugs:
    matched_item = None
    
    # Search the database for a matching name
    for item_id, item_info in items_db.items():
        item_name = item_info.get("name", "")
        if slugify(item_name) == slug:
            matched_item = item_info
            break

    if matched_item:
        icon_path = matched_item.get("icon")  # e.g., "icon/item/21000.png"
        if not icon_path:
            print(f"  [Skip] {slug} (No icon path provided in data)")
            continue
            
        full_url = f"{base_icon_url}/{icon_path}"
        filepath = OUT_DIR / f"{slug}.png"

        if filepath.exists() and filepath.stat().st_size > 1000:
            print(f"  [Skip] {slug}.png already exists")
            success_count += 1
            continue

        try:
            urllib.request.urlretrieve(full_url, filepath)
            print(f"  [OK] Downloaded {slug}.png (from {matched_item.get('name')})")
            success_count += 1
        except Exception as e:
            print(f"  [Error] Failed to download {slug}: {e}")
    else:
        print(f"  [Not Found] {slug} (Could not match name in database)")

print(f"\nDone! Successfully retrieved {success_count}/{len(required_item_slugs)} item icons.")