import cv2, numpy as np, json, os, sys

frame = cv2.imread('debug_frame.png')
if frame is None:
    print("ERROR: cannot read debug_frame.png"); sys.exit(1)
fh, fw = frame.shape[:2]
print(f"debug_frame.png actual size: {fw}x{fh}")

with open('hsr_currency_wars_v4_7_strategies-v4.json') as f:
    strat = json.load(f)
sc = strat['screen_coordinates_1080p']
ref_w, ref_h = sc['resolution']['width'], sc['resolution']['height']
print(f"Reference resolution in JSON: {ref_w}x{ref_h}")

tpl_dir = 'templates/CurrencyWars'
tpls = []
for fn in sorted(os.listdir(tpl_dir)):
    if fn.endswith('.png'):
        p = os.path.join(tpl_dir, fn)
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is not None:
            tpls.append((fn, img))

print(f"\nTesting {len(tpls)} templates at 3 resolutions:\n")
print(f"{'Template':<30} {'Size':>10} {'@orig':>12} {'@1920x1080':>12} {'@2560x1440':>12}")
print("-" * 80)

for name, tpl in tpls:
    th, tw = tpl.shape[:2]
    row = [name, f"{tw}x{th}"]
    for target_w, target_h in [(fw, fh), (1920, 1080), (2560, 1440)]:
        scale_x = target_w / fw
        scale_y = target_h / fh
        scaled = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_CUBIC)
        stw = max(1, int(tw * scale_x))
        sth = max(1, int(th * scale_y))
        scaled_tpl = cv2.resize(tpl, (stw, sth), interpolation=cv2.INTER_CUBIC)
        sh, sw = scaled.shape[:2]
        tsh, tsw = scaled_tpl.shape[:2]
        if sh < tsh or sw < tsw:
            row.append("N/A"); continue
        res = cv2.matchTemplate(scaled, scaled_tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        res2 = cv2.matchTemplate(scaled, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val2, _, _ = cv2.minMaxLoc(res2)
        row.append(f"{max_val:.3f}/{max_val2:.3f}")
    print(f"{row[0]:<30} {row[1]:>10} {row[2]:>12} {row[3]:>12} {row[4]:>12}")

print("\nLegend: scaled_tpl_conf / orig_tpl_conf")
print("scaled_tpl = template resized to frame scale (resolution-independent)")
print("orig_tpl   = original template on scaled frame (current, resolution-dependent)")

print("\n=== Coordinate scaling verification ===")
sr = sc['ui_controls']['shop_refresh_button']
print(f"shop_refresh_button JSON: x={sr['x']}, y={sr['y']} (for 1920x1080)")
s1440 = 2560/1920
print(f"Scaled to 2560x1440: x={int(sr['x']*s1440)}, y={int(sr['y']*s1440)}")

sf = cv2.resize(frame, (2560, 1440), interpolation=cv2.INTER_CUBIC)
tpl = cv2.imread(os.path.join(tpl_dir, 'new.png'), cv2.IMREAD_COLOR)
if tpl is not None:
    res = cv2.matchTemplate(sf, tpl, cv2.TM_CCOEFF_NORMED)
    _, mv, _, ml = cv2.minMaxLoc(res)
    print(f"'new.png' on 2560x1440: conf={mv:.4f} at ({ml[0]},{ml[1]})")
