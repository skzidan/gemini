#!/usr/bin/env python3
"""calibrate_currency_wars_1080p.py - point-and-drag ROI calibration.

The strategy JSON ships with ROIs measured on one particular UI layout. If your
game window is a different size, or your HUD sits elsewhere, every OCR reading
comes back empty and the bot refuses to act (``[State] Not in prep``).

This tool grabs one frame from the game window, shows it, and writes the boxes
you drag into ``screen_coordinates_1080p`` in the strategy JSON. It reads the
region back with Tesseract immediately, so you can see whether a box is right
before saving.

Usage:
  python calibrate_currency_wars_1080p.py                      # live frame
  python calibrate_currency_wars_1080p.py --image shot.png      # use a PNG
  python calibrate_currency_wars_1080p.py --strategy other.json # other target
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import cv2

from cw_core import parse_roi, roi_center, roi_from_points

DEFAULT_STRATEGY = "hsr_currency_wars_v4_7_strategies-v4.json"
MAX_CANVAS_W, MAX_CANVAS_H = 1280, 720


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
def capture_frame_from_game(window_title: str = "Honkai: Star Rail"):
    """Grab one frame using the same engine the bot uses. ``None`` on failure."""
    try:
        from wgc_python import WindowCapture, enumerate_windows
    except Exception as exc:
        print(f"[Calibrate] wgc_python unavailable: {exc}")
        return None

    target = None
    try:
        for title, win_class in enumerate_windows():
            if window_title.lower() in title.lower():
                if win_class in ("UnityWndClass", "UnityWnd"):
                    target = (title, win_class)
                    break
                target = target or (title, win_class)
    except Exception as exc:
        print(f"[Calibrate] Window enumeration failed: {exc}")
        return None
    if target is None:
        print(f"[Calibrate] No window matching '{window_title}'.")
        return None

    cap = None
    try:
        cap = WindowCapture(target[0], target[1], client_area_only=True)
        frame = cap.capture_one(timeout=2.0)
    except Exception as exc:
        print(f"[Calibrate] Capture failed: {exc}")
        return None
    finally:
        if cap is not None:
            try:
                cap.close()
            except Exception:
                pass
    if frame is None:
        return None
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
class Target:
    """One calibratable HUD element.

    ``ocr`` targets store a read-only box; ``click`` targets store both a box
    (kept for reference/OCR) and the point that gets clicked.
    """

    def __init__(self, key: str, label: str, kind: str, hint: str):
        self.key = key
        self.label = label
        self.kind = kind
        self.hint = hint


TARGETS = [
    Target(
        "gold_counter_ocr_roi",
        "Gold counter (number only)",
        "ocr",
        "Drag tightly around the gold digits, not the coin icon.",
    ),
    Target(
        "squad_level_ocr_roi",
        "Squad level (number only)",
        "ocr",
        "Drag tightly around the level digits.",
    ),
    Target(
        "squad_hp_ocr_roi",
        "Squad HP (number only)",
        "ocr",
        "Drag tightly around the HP digits.",
    ),
    Target(
        "shop_refresh_button",
        "Store / refresh button",
        "click",
        "Drag around the button; the click point is its centre.",
    ),
    Target(
        "buy_xp_level_up_button",
        "Buy XP / level up button",
        "click",
        "Drag around the button; the click point is its centre.",
    ),
    Target(
        "lock_shop_toggle",
        "Lock shop toggle",
        "click",
        "Drag around the toggle; the click point is its centre.",
    ),
    Target(
        "protagonist_plot_armor_slot",
        "Plot armour slot (protagonist)",
        "click",
        "Drag around the slot; the click point is its centre.",
    ),
]


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def load_strategy(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_strategy(path: Path, data: dict) -> Path:
    """Write atomically and keep a ``.bak`` copy of the previous file."""
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        backup.write_bytes(path.read_bytes())
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)
    return backup


def apply_target(strategy: dict, target: Target, roi: list[int]) -> None:
    """Store ``roi`` on the matching ``ui_controls`` entry."""
    ui = strategy.setdefault("screen_coordinates_1080p", {}).setdefault("ui_controls", {})
    if target.kind == "ocr":
        ui[target.key] = roi
        return
    entry = ui.get(target.key)
    if not isinstance(entry, dict):
        entry = {}
        ui[target.key] = entry
    x, y = roi_center(roi)
    entry["x"] = x
    entry["y"] = y
    entry["ocr_roi"] = roi


def describe(strategy: dict) -> list[str]:
    ui = strategy.get("screen_coordinates_1080p", {}).get("ui_controls", {})
    lines = []
    for target in TARGETS:
        entry = ui.get(target.key)
        if target.kind == "ocr":
            lines.append(f"{target.label:34s} {entry if entry else 'not set'}")
        elif isinstance(entry, dict):
            lines.append(
                f"{target.label:34s} click=({entry.get('x')}, {entry.get('y')}) "
                f"roi={entry.get('ocr_roi')}"
            )
        else:
            lines.append(f"{target.label:34s} not set")
    return lines


# ---------------------------------------------------------------------------
# OCR preview
# ---------------------------------------------------------------------------
def ocr_preview(frame, roi: list[int], mode: str) -> str:
    """Read the dragged box back so the user can confirm it before saving.

    Mirrors ``OcrHelper.read_text`` (4x upscale, Otsu then fixed threshold) so
    the preview matches what the bot will actually see.
    """
    try:
        import pytesseract
    except ImportError:
        return "(pytesseract not installed)"
    x1, y1, x2, y2 = (int(v) for v in roi)
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return "(empty)"
    gray = cv2.resize(
        cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY),
        None,
        fx=4.0,
        fy=4.0,
        interpolation=cv2.INTER_CUBIC,
    )
    _, thresh_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, thresh_fixed = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    config = "--oem 3 --psm 6"
    if mode == "digits":
        config += " -c tessedit_char_whitelist=0123456789"
    for thresh in (thresh_otsu, thresh_fixed):
        try:
            text = re.sub(r"\s+", " ", pytesseract.image_to_string(thresh, config=config)).strip()
            if text:
                return text
        except Exception as exc:
            return f"(OCR failed: {exc})"
    return "(empty)"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
class CalibratorApp:
    def __init__(self, root: tk.Tk, frame, strategy_path: Path, strategy: dict):
        self.root = root
        self.frame = frame
        self.strategy_path = strategy_path
        self.strategy = strategy
        self.selected = TARGETS[0]
        self.start: tuple[float, float] | None = None
        self.box_id = None
        self._pending: dict[str, list[int]] = {}

        img_h, img_w = frame.shape[:2]
        self.img_size = (img_w, img_h)
        scale = min(MAX_CANVAS_W / img_w, MAX_CANVAS_H / img_h, 1.0)
        self.scale = scale
        self.disp_size = (int(img_w * scale), int(img_h * scale))

        root.title("Currency Wars - calibrate HUD ROIs")
        root.configure(bg="#0A0E1A")

        self._build_widgets()
        self._draw_targets()

    # -- layout ------------------------------------------------------------
    def _build_widgets(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(side="top", fill="x")

        ttk.Label(top, text="Element:").pack(side="left")
        self.choice = tk.StringVar(value=TARGETS[0].label)
        combo = ttk.Combobox(
            top, textvariable=self.choice, values=[t.label for t in TARGETS], width=36, state="readonly"
        )
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", self._on_select)

        ttk.Button(top, text="Reload frame", command=self._reload_frame).pack(side="left", padx=4)
        ttk.Button(top, text="Save all", command=self.save).pack(side="left", padx=4)
        ttk.Button(top, text="Print current ROIs", command=self._print_current).pack(side="left", padx=4)

        self.hint = ttk.Label(top, text="", foreground="#38BDF8")
        self.hint.pack(side="left", padx=10)

        self.canvas = tk.Canvas(
            self.root,
            width=self.disp_size[0],
            height=self.disp_size[1],
            bg="#000000",
            highlightthickness=1,
            highlightbackground="#1E293B",
            cursor="crosshair",
        )
        self.canvas.pack(padx=6, pady=4)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self.status = ttk.Label(self.root, text="", foreground="#E2E8F0", justify="left")
        self.status.pack(side="top", fill="x", padx=8, pady=(0, 8))

        self._on_select()

    def _draw_targets(self):
        self.canvas.delete("target")
        ui = self.strategy.get("screen_coordinates_1080p", {}).get("ui_controls", {})
        self._refresh_photo()
        for target in TARGETS:
            entry = ui.get(target.key)
            roi = None
            if target.kind == "ocr" and isinstance(entry, list):
                roi = entry
            elif isinstance(entry, dict):
                roi = entry.get("ocr_roi")
            if not roi or len(roi) != 4:
                continue
            x1, y1, x2, y2 = [int(v * self.scale) for v in roi]
            colour = "#22C55E" if target.kind == "ocr" else "#F59E0B"
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=colour, dash=(3, 2), tags="target")
            self.canvas.create_text(
                x1, max(8, y1 - 7), text=target.label, fill=colour, anchor="w", font=("Consolas", 8), tags="target"
            )

    def _refresh_photo(self):
        """Render the captured frame at display size (Tk cannot scale images)."""
        tmp = Path(__file__).with_name("_calibration_frame.png")
        shown = self.frame
        if (shown.shape[1], shown.shape[0]) != self.disp_size:
            shown = cv2.resize(shown, self.disp_size, interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(tmp), shown)
        photo = tk.PhotoImage(file=str(tmp))
        self.photo = photo  # keep a reference or Tk drops it
        self.canvas.delete("frame")
        self.canvas.create_image(0, 0, image=photo, anchor="nw", tags="frame")
        self.canvas.tag_lower("frame")
        try:
            tmp.unlink()
        except OSError:
            pass

    # -- events ------------------------------------------------------------
    def _on_select(self, _event=None):
        label = self.choice.get()
        self.selected = next((t for t in TARGETS if t.label == label), TARGETS[0])
        self.hint.config(text=self.selected.hint)

    def _to_image(self, cx: float, cy: float) -> tuple[float, float]:
        return cx / self.scale, cy / self.scale

    def _on_press(self, event):
        self.start = (event.x, event.y)
        if self.box_id:
            self.canvas.delete(self.box_id)
        self.box_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#38BDF8", width=2
        )

    def _on_drag(self, event):
        if not self.start or not self.box_id:
            return
        self.canvas.coords(self.box_id, self.start[0], self.start[1], event.x, event.y)

    def _on_release(self, event):
        if not self.start or not self.box_id:
            return
        p1 = self._to_image(*self.start)
        p2 = self._to_image(event.x, event.y)
        roi = roi_from_points(p1, p2, self.img_size[0], self.img_size[1])
        self.start = None
        if roi is None:
            self.status.config(text="Box too small - drag a rectangle over the element.")
            return

        self._pending[self.selected.key] = roi
        reading = ocr_preview(self.frame, roi, "digits" if self.selected.kind == "ocr" else "text")
        centre = roi_center(roi)
        self.status.config(
            text=(
                f"{self.selected.label}: ROI {roi} -> reading {reading!r}\n"
                f"click point would be {centre}. Use 'Save all' to keep it."
            )
        )

    def _print_current(self):
        print("\n".join(describe(self.strategy)))
        self.status.config(text="Current values printed to the console.")

    def _reload_frame(self):
        frame = capture_frame_from_game()
        if frame is None:
            self.status.config(text="Capture failed - is the game running and visible?")
            return
        self.frame = frame
        self.img_size = (frame.shape[1], frame.shape[0])
        self._draw_targets()
        self.status.config(text=f"Reloaded frame {self.img_size[0]}x{self.img_size[1]}.")

    def save(self):
        if not self._pending:
            messagebox.showinfo("Calibrate", "Nothing to save yet - drag a box first.")
            return
        for key, roi in self._pending.items():
            target = next(t for t in TARGETS if t.key == key)
            apply_target(self.strategy, target, roi)
        backup = write_strategy(self.strategy_path, self.strategy)
        saved = ", ".join(f"{t.label}={self._pending[t.key]}" for t in TARGETS if t.key in self._pending)
        self._pending.clear()
        self._draw_targets()
        self.status.config(text=f"Saved to {self.strategy_path.name} (backup: {backup.name})\n{saved}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate HUD ROIs for the Currency Wars bot")
    parser.add_argument(
        "--strategy", "--config", dest="strategy", default=None, help=f"strategy JSON (default: {DEFAULT_STRATEGY})"
    )
    parser.add_argument("--image", default=None, help="calibrate against a saved PNG instead of a live frame")
    parser.add_argument("--roi", default=None, help="with --image, pre-crop to x1,y1,x2,y2 first")
    parser.add_argument("--print", action="store_true", help="print the current ROIs and exit")
    args = parser.parse_args(argv)

    base = Path(__file__).resolve().parent
    strategy_path = Path(args.strategy) if args.strategy else base / DEFAULT_STRATEGY
    if not strategy_path.exists():
        print(f"[Calibrate] Strategy JSON not found: {strategy_path}")
        return 2
    strategy = load_strategy(strategy_path)

    if args.print:
        print("\n".join(describe(strategy)))
        return 0

    if args.image:
        frame = cv2.imread(str(Path(args.image)))
        if frame is None:
            print(f"[Calibrate] Could not read image: {args.image}")
            return 2
        if args.roi:
            roi = parse_roi(args.roi)
            if not roi:
                print("[Calibrate] --roi must be x1,y1,x2,y2")
                return 2
            x1, y1, x2, y2 = roi
            frame = frame[y1:y2, x1:x2]
    else:
        frame = capture_frame_from_game()
        if frame is None:
            print("[Calibrate] No live frame. Start the game, or pass --image shot.png.")
            return 1

    # Normalise to the reference resolution the strategy coordinates use, so a
    # 1906x1072 client area still produces 1920x1080 ROIs.
    exp_w = int(strategy.get("screen_coordinates_1080p", {}).get("resolution", {}).get("width", 1920))
    exp_h = int(strategy.get("screen_coordinates_1080p", {}).get("resolution", {}).get("height", 1080))
    h, w = frame.shape[:2]
    if (w, h) != (exp_w, exp_h):
        if abs((w / max(1, h)) - (exp_w / max(1, exp_h))) <= 0.02:
            frame = cv2.resize(frame, (exp_w, exp_h), interpolation=cv2.INTER_LINEAR)
            print(f"[Calibrate] Rescaled {w}x{h} capture to {exp_w}x{exp_h}.")
        else:
            print(
                f"[Calibrate][WARN] Capture is {w}x{h}; not 16:9, so ROIs will not transfer. "
                f"Set the game to {exp_w}x{exp_h} windowed."
            )

    root = tk.Tk()
    CalibratorApp(root, frame, strategy_path, strategy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
