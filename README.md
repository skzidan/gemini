# HSR Currency Wars Automation (v4.7)

Background assistant for the *Currency Wars* event in Honkai: Star Rail. It
captures the game window with Windows Graphics Capture, reads the screen with
OpenCV + Tesseract, decides what to do, and sends clicks with `PostMessage`.

**Dry-run is on by default.** Nothing is clicked until you disable it.

> Using automation violates the game's Terms of Service and can get your
> account banned. Use at your own risk. This project contains no
> anti-cheat/anti-detection features.

---

## Quick start

```bat
python -m pip install -r requirements.txt
python currency_wars_app-v9.py --self-test
rem then start the GUI:
python currency_wars_app-v9.py
```

Tesseract is not a pip package. Install the Windows build and put
`tesseract.exe` on `PATH`.

`--self-test` prints a PASS/FAIL report for the environment, config JSON,
strategy JSON, scene templates, unit templates and item verification. Exit code
is `0` on pass.

---

## First-run checklist

The bot cannot work on a fresh copy: it needs real screenshots from **your**
game, at **your** window size.

1. **Windowed 1920x1080.** The ROIs in the strategy JSON assume this size. A
   window that is a few pixels off (e.g. a 1906x1072 client area) is rescaled to
   1920x1080 automatically and click coordinates are mapped back, so the
   calibrated ROIs keep working. Very different aspect ratios are refused with a
   warning instead of being distorted.
2. **HUD ROIs.** If the prep screen is detected but Gold/Level/HP read as
   `None`, your HUD sits somewhere else than in the shipped layout. Calibrate by
   dragging boxes over the digits - the tool reads each box back with Tesseract
   so you can see it is correct before saving:
   ```bat
   python calibrate_currency_wars_1080p.py --strategy hsr_currency_wars_v4_7_strategies-v4.json
   ```
   It writes a `.bak` next to the strategy JSON. Restart the app afterwards.
   `--print` lists the current values, `--image shot.png` calibrates offline.
3. **Scene templates.** `templates/scenes/` must contain `victory.png`,
   `reward.png` and `battle_start.png`. Capture them with the game on that
   screen:
   ```bat
   python currency_wars_app-v9.py --capture-scene victory --scene-roi 820,300,1100,360
   python currency_wars_app-v9.py --capture-scene reward  --scene-roi x1,y1,x2,y2
   python currency_wars_app-v9.py --capture-scene battle_start --scene-roi x1,y1,x2,y2
   ```
   Pick a small, static region. Every capture also writes a full frame to
   `captures/` so you can check what the bot saw.
4. **Unit templates.** `templates/units/` ships with portraits; verify they
   match your UI language/scale. The GUI button *Capture Unit Portraits*
   dumps the current board slots so you can rename them to `<unit_slug>.png`.
5. **Item templates are NOT verified.** `templates/items/` was populated by
   download scripts that guessed item IDs, so it contains placeholder icons.
   Crafting/equipping stays disabled until you add confirmed slugs to
   `unit_registry.json` -> `items.verified`.
6. Run `--self-test` again. Missing scenes are the only hard blocker.

## Safety model

| Guard | Behaviour |
| --- | --- |
| `automation.dry_run` | Logs actions instead of sending clicks. Default `true`. |
| `automation.require_prep_ui` | Requires 2 of 3 plausible HUD readings (gold 0-999, level 1-20, HP 0-100) before any shop/buy/reroll action. Prevents clicking blind on a battle or menu screen. |
| `automation.advance_on_unknown` | Off by default. When off, unknown screens are waited on, not clicked through. |
| `automation.battle_timeout_seconds` | Stops the loop if a battle never resolves. |
| `automation.wait_for_window_seconds` | `0` waits forever for the game window; otherwise gives up after N seconds. |
| F10 / F12 | Pause / emergency stop (needs Administrator for global hotkeys). |
| `automation.max_runs` | Stop after N battles (`0` = unlimited). |

## CLI

```
python currency_wars_app-v9.py                     # GUI
python currency_wars_app-v9.py --dry-run           # force dry-run
python currency_wars_app-v9.py --headless          # no GUI, logs to console + automation.log
python currency_wars_app-v9.py --headless --max-seconds 600
python currency_wars_app-v9.py --self-test         # validate setup (exit 0/1)
python currency_wars_app-v9.py --capture-scene NAME [--scene-roi x1,y1,x2,y2]
```

`run_background.bat` is a thin wrapper around `--headless` and forwards any
extra arguments (`run_background.bat --dry-run --max-seconds 600`).

## Layout

| Path | Purpose |
| --- | --- |
| `currency_wars_app-v9.py` | GUI, capture, OCR, clicks, state machine. |
| `cw_core.py` | Pure decision logic (registry, shop policy, crafting, routing). No cv2/win32/Tk imports, so it is unit tested without a game. |
| `unit_registry.json` | Display-name aliases + ignored template files + item allow-list. |
| `config.json` | Runtime settings. Saved atomically by the GUI. |
| `hsr_currency_wars_v4_7_strategies-v4.json` | Strategies, screen ROIs, thresholds, recipes. |
| `calibrate_currency_wars_1080p.py` | Drag-and-drop ROI calibrator for the HUD and buttons. |
| `templates/units`, `templates/items`, `templates/scenes` | Template images. |
| `captures/`, `review/`, `automation.log` | Output, rotated at 2 MB x3. |
| `tests/` | `python -m unittest discover -s tests -v` |

## How the loop decides

1. Capture a frame.
2. Match scene templates (`victory` / `reward` / `battle_start`) -> click the
   mapped button from `clicks`.
3. No scene? Read the HUD. If it does not look like the prep screen, wait
   (or advance, only if you opted in).
4. In prep: Escapade cards (throttled OCR) -> shop buy -> shop XP/reroll ->
   bench deployment -> plot armor -> crafting/equipping.

Shop economy comes from `cw_core.plan_shop`: level up while keeping the 30g
interest cap, reroll at level 8+ with enough gold, hold at 2 pity refreshes
unless the protagonist is in the shop, and go all-in below 30 HP.

## Troubleshooting

| Symptom | Where to look |
| --- | --- |
| EXE shows "Failed to execute script" and never writes `automation.log` | A native library was not bundled (this used to be `wgc_python.dll`). Rebuild with `python build_exe.py`; it now bundles `wgc_python` and refuses to finish if the DLL or OpenCV is missing. `python build_exe.py --console` shows the full traceback. |
| EXE takes ~10-20 s before the first log line | One-file builds unpack to `%TEMP%\_MEI*` on every start. Use `python build_exe.py --onedir` for instant startup. |
| EXE/GUI closes instantly or shows a traceback dialog | `crash.log` next to the EXE, then `automation.log`. GUI callback errors are also shown in a dialog and written to both files. |
| `--self-test` gives no output in the EXE | A windowed build has no console. The report is written to `self_test_report.txt` and shown in a dialog. |
| Bot waits on "Waiting for the game window" | Window title must match `window_title` in config.json exactly; the game must be running (not the launcher). |
| Everything is read as wrong values | Game must be windowed 1920x1080; see the resolution warning in `automation.log`. Small mismatches are auto-rescaled. |
| `[State] Not in prep (HUD unreadable ...)` while on the prep screen | The HUD ROIs don't match your layout. Run `python calibrate_currency_wars_1080p.py` and drag over the Gold/Level/HP numbers. |
| Scene section fails in `--self-test` | Capture `victory`, `reward`, `battle_start` as described above. |

## Known limitations

- Post-click verification is minimal: the loop verifies the *screen* before
  acting, not that every click succeeded.
- `templates/items` is still placeholder data; only verified items are used.
- The item names in recipes are mapped through `unit_registry.json`; the
  shipped aliases cover the known mismatches, not every future item.
- No installer/signing; `build_exe.py` produces an unsigned PyInstaller build.
- No version control history: this folder is not a git repository.
