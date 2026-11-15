import os
import requests
from pathlib import Path

# Official Character ID mapping extracted from HSR game files
CHARACTER_IDS = {
    "march_7th": "1001", "dan_heng": "1002", "himeko": "1003", "welt": "1004",
    "kafka": "1005", "silver_wolf": "1006", "arlan": "1008", "asta": "1009",
    "herta": "1013", "bronya": "1101", "seele": "1102", "serval": "1103",
    "gepard": "1104", "natasha": "1105", "pela": "1106", "clara": "1107",
    "sampo": "1108", "hook": "1109", "lynx": "1110", "luka": "1111",
    "topaz": "1112", "qingque": "1201", "tingyun": "1202", "luocha": "1203",
    "jing_yuan": "1204", "blade": "1205", "sushang": "1206", "yukong": "1207",
    "fu_xuan": "1208", "yanqing": "1209", "guinaifen": "1210", "bailu": "1211",
    "jingliu": "1212", "dan_heng_il": "1213", "xueyi": "1214", "hanya": "1215",
    "huohuo": "1217", "jiaoqiu": "1218", "feixiao": "1220", "yunli": "1221",
    "lingsha": "1222", "moze": "1223", "march_7th_hunt": "1224", "fugue": "1225",
    "gallagher": "1301", "argenti": "1302", "ruan_mei": "1303", "aventurine": "1304",
    "dr_ratio": "1305", "sparkle": "1306", "black_swan": "1307", "acheron": "1308",
    "robin": "1309", "sam": "1310", "screwllum": "1311", "misha": "1312",
    "sunday": "1313", "jade": "1314", "boothill": "1315", "rappa": "1317",
    "mr_reca": "1319", "the_dahlia": "1321", "the_herta": "1401", "aglaea": "1402",
    "tribbie": "1403", "mydei": "1404", "anaxa": "1405", "cipher": "1406",
    "castorice": "1407", "phainon": "1408", "hyacine": "1409", "hysilens": "1410",
    "styllus": "1411", "cerydra": "1412", "evernight": "1413", "dan_heng_pt": "1414",
    "cyrene": "1415", "sparxie": "1501", "yao_guang": "1502", "pearl": "1503",
    "ashveil": "1504", "evanescia": "1505", "silver_wolf_999": "1506",
    "mortenax_blade": "1507", "rin_tohsaka": "1508", "gilgamesh": "1509",
}

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "templates" / "units"
OUT_DIR.mkdir(parents=True, exist_ok=True)

print(f"Downloading character portraits to {OUT_DIR}...")

for name, char_id in CHARACTER_IDS.items():
    url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png"
    filepath = OUT_DIR / f"{name}.png"
    
    if filepath.exists():
        print(f"  [Skip] {name}.png already exists")
        continue
        
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(response.content)
            print(f"  [OK] Downloaded {name}.png")
        else:
            print(f"  [Error] Failed to download {name} (Status: {response.status_code})")
    except Exception as e:
        print(f"  [Error] Failed to download {name}: {e}")

print("\nDone! You can now run 'Scan Board & Update HUD Now' in the bot.")