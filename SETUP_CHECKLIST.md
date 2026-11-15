# First-Run Setup Checklist

This document walks you through the **9 steps** needed to go from a fresh clone to a working automation run. The bot cannot run out-of-the-box because it needs screenshots from **your** game at **your** window size.

---

## Prerequisites

| Requirement | How to check |
|---|---|
| Python 3.10+ | `python --version` |
| Tesseract OCR (Windows installer) | `tesseract --version` |
| Git | `git --version` |
| Honkai: Star Rail installed and running | — |

---

## Step 1 — Install Python dependencies

```bat
python -m pip install -r requirements.txt
```

If you see errors about `pywin32`, `opencv-python`, `Pillow` or `pytesseract`,
install them individually:

```bat
python -m pip install pywin32 opencv-python pillow pytesseract
```

## Step 2 — Install Tesseract

1. Download the Windows installer from:
   https://github.com/UB-Mannheim/tesseract/wiki
2. Run the installer. **Check the box for "English" language data.**
3. Make sure `tesseract.exe` is on `PATH`:
   - Typical path: `C:\Program Files\Tesseract-OCR\tesseract.exe`
   - Or add it manually: `set PATH=%PATH%;C:\Program Files\Tesseract-OCR`
4. Verify: `tesseract --version` should print version info.

## Step 3 — Run the self-test

```bat
python currency_wars_app-v9.py --self-test
```

This prints a PASS/FAIL report. **Expect these to FAIL** on a fresh clone:

| Check | Expected | Why it fails |
|---|---|---|
| Scene templates | `victory.png`, `reward.png`, `battle_start.png` in `templates/scenes/` | You need to capture them (Step 4) |
| Unit templates | Portraits in `templates/units/` | Skipped if already present ✓ |
| Item templates | Verified items in `templates/items/` | Placeholder icons — see Step 7 |

## Step 4 — Capture scene templates

You need **3 screenshots** from the actual game:

1. **Victory screen** — The "Victory!" banner that appears after winning a battle.
2. **Reward screen** — The screen showing rewards after a battle.
3. **Battle start** — The "Begin Battle" or "Prepare for Battle" button.

For each one:

```bat
# 1. Position the game window so the banner/button is visible (1920x1080 windowed).
# 2. Run the capture command. It saves the template + a full-frame debug capture.

python currency_wars_app-v9.py --capture-scene victory --scene-roi 820,300,1100,360
python currency_wars_app-v9.py --capture-scene reward  --scene-roi 820,300,1100,360
python currency_wars_app-v9.py --capture-scene battle_start --scene-roi 820,300,1100,360

# Adjust the ROI (x1,y1,x2,y2) to match where the banner actually appears on YOUR screen.
```

Each command writes:
- `templates/scenes/<name>.png` — the template
- `captures/<name>_<timestamp>_full.png` — the full frame for verification

**Tip:** Start with a generous ROI (e.g. `300,200,1600,800`) and tighten it after
viewing the debug capture, so you don't miss the banner.

## Step 5 — Calibrate HUD ROIs

If the self-test passed scene detection but the bot logs `[State] Not in prep
(HUD unreadable...)` while you are on the prep screen, your HUD numbers
(Gold/Level/HP) sit at different coordinates.

Run the interactive calibrator:

```bat
python calibrate_currency_wars_1080p.py --strategy hsr_currency_wars_v4_7_strategies-v4.json
```

1. A window opens showing a screenshot of the game.
2. **Drag a rectangle** over the Gold counter (top-center).
3. **Drag a rectangle** over the Squad Level (top-left).
4. **Drag a rectangle** over the HP bar (top-center-right).
5. The tool reads each box with Tesseract and shows you the result.
6. Press **Save** — it writes a `.bak` next to the strategy JSON with your ROIs.
7. Restart the app. The bot should now read the prep screen correctly.

## Step 6 — Verify unit templates

`templates/units/` already contains 100+ portrait templates. Verify they match
your game's UI language/scale:

```bat
# Open the GUI and click "Capture Unit Portraits"
python currency_wars_app-v9.py
```

This dumps the current board/bench slot icons to `captures/`. Compare them to
the existing PNGs in `templates/units/`. If they look different (e.g. different
language text or aspect ratio), rename your captured crops to match the expected
filenames and overwrite the shipped templates.

## Step 7 — Verify item templates (optional, only for crafting)

If you want the bot to **craft and equip items**, you need real item icons in
`templates/items/`. The shipped folder contains placeholder images gathered by
an automatic download script.

1. Open the game's equipment inventory.
2. Screenshot each item you want to verify.
3. Crop the icon to ~48x48 px.
4. Save it as `<item_slug>.png` in `templates/items/`.
5. Add the slug to `unit_registry.json` → `items.verified`.

**The verified list is a safety lock.** Without it, the bot never drags
unverified icons onto a unit.

## Step 8 — Run the self-test again

```bat
python currency_wars_app-v9.py --self-test
```

All checks should now pass (scene templates, unit templates, config, strategy
JSON). The only exception is item templates — these are optional.

## Step 9 — First dry-run test

Start the bot with dry-run enabled (this is the default):

```bat
python currency_wars_app-v9.py
```

1. The GUI opens.
2. Select a strategy from the dropdown.
3. Click **Start**.
4. Watch the log output — every action is printed but no clicks are sent.
5. Verify the log says things like:
   - `[Dry-Run] Would click shop slot 0 (Bronya)`
   - `[Dry-Run] Would buy XP level up`
   - `[Dry-Run] Would deploy Aglaea to frontline slot 0`
6. If everything looks correct, disable **"Dry Run"** in the GUI and restart.

---

## If something goes wrong

| Problem | Solution |
|---|---|
| `tesseract is not installed` | Install Tesseract (Step 2) or set `TESSERACT_CMD` env var |
| `[State] Not in prep` while on prep screen | Run calibrator (Step 5) |
| Bot clicks but nothing happens | Game window may be minimised or `window_title` in config.json is wrong |
| Bot clicks the wrong spot | Strategy JSON screen coordinates may mismatch your resolution |
| Bot does not see victory/reward/battle screens | Re-capture scene templates (Step 4) with tighter ROIs |
| EXE crashes on startup | Run from source instead (`python currency_wars_app-v9.py`) |

---

## Reference

| File | Purpose |
|---|---|
| `currency_wars_app-v9.py` | Main bot + GUI |
| `cw_core.py` | Decision logic |
| `config.json` | Runtime settings |
| `unit_registry.json` | Unit/item name aliases + verified items |
| `hsr_currency_wars_v4_7_strategies-v4.json` | Strategy definitions |
| `calibrate_currency_wars_1080p.py` | HUD calibrator |
| `build_exe.py` | PyInstaller build script |
| `templates/` | Scene, unit, and item templates |
| `captures/` | Debug screenshots |
| `automation.log` | Rotating log file |
