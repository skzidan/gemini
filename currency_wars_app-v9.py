#!/usr/bin/env python3
"""
HSR Currency Wars v4.7 (Season 2) - Complete Automation Suite (WGC Background Mode)
"""
from __future__ import annotations

import argparse
import ctypes
import difflib
import json
import logging
import logging.handlers
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import cv2
import keyboard
import numpy as np
import win32api
import win32con
import win32gui

from cw_core import (
    DEFAULT_SCENES,
    NameRegistry,
    ShopState,
    ShopThresholds,
    ShopUnit,
    choose_equip_target,
    coerce_number,
    match_key,
    parse_roi,
    plan_craft,
    plan_shop,
    resolve_scene_map,
    select_purchases,
    slugify,
)

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

WGC_IMPORT_ERROR = ""
try:
    from wgc_python import WindowCapture, enumerate_windows
    HAS_WGC = True
except Exception as exc:  # ImportError, but also PyInstaller's PyInstallerImportError
    HAS_WGC = False
    WGC_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


LOG = logging.getLogger("currencywars")


def _app_base_dir() -> Path:
    """Folder that holds config.json / templates / logs.

    In a PyInstaller one-file build ``__file__`` lives in a temporary unpack
    folder, so edits to config.json would be lost. Frozen builds therefore use
    the folder next to the EXE.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


WINDOWED_BUILD = False


def _ensure_streams() -> bool:
    """Make stdout/stderr safe in PyInstaller ``--windowed`` builds.

    A windowed EXE has no console, so Python sets ``sys.stdout`` and
    ``sys.stderr`` to ``None``. Anything that prints then dies with
    ``AttributeError: 'NoneType' object has no attribute 'write'``. Point both
    at ``os.devnull`` and report whether output is hidden.
    """
    hidden = sys.stdout is None or sys.stderr is None
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    return hidden


def _write_crash_log(text: str, name: str = "crash.log") -> Path | None:
    """Persist an otherwise invisible traceback next to the app."""
    try:
        path = _app_base_dir() / name
        with path.open("w", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
        return path
    except OSError:
        return None


def _attach_log_handlers(log_path: Path, console: bool) -> None:
    """Attach a rotating file handler (and optional console handler) once."""
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    for handler in list(LOG.handlers):
        LOG.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_path), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        LOG.addHandler(file_handler)
    except OSError:
        pass
    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        LOG.addHandler(stream)


def is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ==============================================================================
# 1. INPUT SIMULATION & IMAGE RECOGNITION
# ==============================================================================

class InputSimulation:
    def __init__(self, config: dict):
        self.config = config
        # Safety: default to dry-run so a config without an explicit value can
        # never silently enable live clicks against the game window.
        self.dry_run = config.get("automation", {}).get("dry_run", True)
        self.window_title = config.get("window_title", "Honkai: Star Rail")
        self.hwnd = None
        # Set by the app so dry-run actions show up in the GUI log, not stdout.
        self.log_fn = None
        # Captured frames are rescaled to the reference resolution; this factor
        # maps those coordinates back to real window pixels for PostMessage.
        self.frame_scale = (1.0, 1.0)

    def _emit(self, msg: str):
        if self.log_fn is not None:
            self.log_fn(msg)
        else:
            print(msg)

    def update_hwnd(self, hwnd: int):
        self.hwnd = hwnd

    def _ensure_hwnd(self) -> bool:
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            return True
        self.hwnd = win32gui.FindWindow(None, self.window_title)
        return bool(self.hwnd)

    def set_frame_scale(self, scale_x: float, scale_y: float) -> None:
        """Map reference-resolution coordinates to real window pixels."""
        self.frame_scale = (float(scale_x), float(scale_y))

    def _to_window(self, x: float, y: float) -> tuple[int, int]:
        sx, sy = self.frame_scale
        return int(round(x * sx)), int(round(y * sy))

    def click_xy(self, x: int, y: int, label: str = ""):
        tx, ty = self._to_window(x, y)
        if self.dry_run:
            self._emit(f"[DRY-RUN] Click {label} at ({x}, {y})")
            return
        if not self._ensure_hwnd():
            return

        l_param = win32api.MAKELONG(tx, ty)
        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, l_param)
        time.sleep(0.05)
        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, l_param)

    def drag_xy(self, x1: int, y1: int, x2: int, y2: int, label: str = "", duration: float = 0.25):
        tx1, ty1 = self._to_window(x1, y1)
        tx2, ty2 = self._to_window(x2, y2)
        if self.dry_run:
            self._emit(f"[DRY-RUN] Drag {label}: ({x1}, {y1}) -> ({x2}, {y2})")
            return
        if not self._ensure_hwnd():
            return

        l_start = win32api.MAKELONG(tx1, ty1)
        l_end = win32api.MAKELONG(tx2, ty2)

        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, l_start)
        time.sleep(0.1)

        steps = 10
        for i in range(1, steps + 1):
            x = tx1 + (tx2 - tx1) * i // steps
            y = ty1 + (ty2 - ty1) * i // steps
            l_move = win32api.MAKELONG(x, y)
            win32gui.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, l_move)
            time.sleep(duration / steps)

        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, l_end)

    def click_named(self, key: str):
        pt = self.config.get("clicks", {}).get(key)
        if pt:
            self.click_xy(pt["x"], pt["y"], label=key)


class ImageRecognition:
    def __init__(self, templates_dir: str = "./templates", ignore_slugs=None):
        self.templates_dir = templates_dir
        self.cache: dict[str, np.ndarray] = {}
        self.ignore_slugs = {match_key(s) for s in (ignore_slugs or [])}
        self._listing_cache: dict[str, tuple[float, list[str]]] = {}

    def list_templates(self, subdir: str) -> list[str]:
        """List template files in a subdir, cached until the folder changes.

        The old code called ``os.listdir`` once per slot per cycle; this cache
        turns that into one stat per folder per cycle.
        """
        folder = os.path.join(self.templates_dir, subdir)
        if not os.path.isdir(folder):
            return []
        try:
            stamp = os.stat(folder).st_mtime
        except OSError:
            return []
        cached = self._listing_cache.get(folder)
        if cached and cached[0] == stamp:
            return cached[1]
        names = [
            name for name in os.listdir(folder)
            if name.lower().endswith(".png")
        ]
        self._listing_cache[folder] = (stamp, names)
        return names

    def preload(self, subdirs=("units", "items")) -> int:
        """Warm the template cache so the first cycle is not penalized."""
        loaded = 0
        for subdir in subdirs:
            for name in self.list_templates(subdir):
                if self.load_template(os.path.join(subdir, name)) is not None:
                    loaded += 1
        return loaded

    def load_template(self, rel_path: str) -> np.ndarray | None:
        full_path = os.path.join(self.templates_dir, rel_path)
        if full_path in self.cache:
            return self.cache[full_path]
        if not os.path.exists(full_path):
            return None
        img = cv2.imread(full_path, cv2.IMREAD_COLOR)
        if img is not None:
            self.cache[full_path] = img
        return img

    def find_template(self, scene: np.ndarray, template: np.ndarray, threshold: float = 0.78) -> dict:
        if scene is None or template is None:
            return {"found": False, "confidence": 0.0, "center": None}
        sh, sw = scene.shape[:2]
        th, tw = template.shape[:2]
        if sh < th or sw < tw:
            return {"found": False, "confidence": 0.0, "center": None}
        res = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= threshold:
            cx = max_loc[0] + tw // 2
            cy = max_loc[1] + th // 2
            return {"found": True, "confidence": float(max_val), "center": (cx, cy)}
        return {"found": False, "confidence": float(max_val), "center": None}

    def match_best_from_dir(self, patch: np.ndarray, subdir: str, threshold: float = 0.72) -> tuple[str | None, float]:
        if patch is None or patch.size == 0:
            return None, 0.0
        best_slug = None
        best_conf = 0.0
        ph, pw = patch.shape[:2]
        for fname in self.list_templates(subdir):
            slug = os.path.splitext(fname)[0]
            if match_key(slug) in self.ignore_slugs:
                continue
            if slug.startswith("slot") and slug.endswith("_capture"):
                continue
            tpl = self.load_template(os.path.join(subdir, fname))
            if tpl is None:
                continue
            th, tw = tpl.shape[:2]
            if th > ph or tw > pw:
                continue
            info = self.find_template(patch, tpl, threshold=threshold)
            if info["found"] and info["confidence"] > best_conf:
                best_conf = info["confidence"]
                best_slug = slug
        return best_slug, best_conf


class SceneDetector:
    """Detect full-screen UI states from template images.

    Scene templates are small crops of a distinctive part of each screen, stored
    under ``templates/scenes/<name>.png`` (configurable via the ``scenes``
    section of config.json). Missing templates are reported at startup instead
    of failing silently: without them the bot cannot know when a battle or a
    reward screen appeared.
    """

    def __init__(self, recognizer: ImageRecognition, config: dict, templates_subdir: str = "scenes"):
        self.recognizer = recognizer
        self.subdir = templates_subdir
        self.scene_map = resolve_scene_map(config)
        self.available: dict[str, tuple[str, np.ndarray]] = {}
        self.missing: list[str] = []
        for scene, paths in self.scene_map.items():
            for rel in paths:
                if not rel.startswith(templates_subdir + "/"):
                    rel = os.path.join(templates_subdir, rel).replace("\\", "/")
                template = recognizer.load_template(rel)
                if template is not None:
                    self.available[scene] = (rel, template)
                    break
            else:
                self.missing.append(scene)

    def detect(self, frame: np.ndarray, threshold: float = 0.84) -> tuple[str | None, float]:
        best_scene, best_conf = None, 0.0
        if frame is None:
            return best_scene, best_conf
        for scene, (_rel, template) in self.available.items():
            info = self.recognizer.find_template(frame, template, threshold=threshold)
            if info["found"] and info["confidence"] > best_conf:
                best_scene, best_conf = scene, info["confidence"]
        return best_scene, best_conf

# ==============================================================================
# 2. OCR & BOARD / BENCH / SHOP / EQUIPMENT SCANNERS
# ==============================================================================

class OcrHelper:
    """Tesseract wrapper with an explicit reading mode.

    The original implementation forced a digit whitelist on every call, which
    silently broke every feature that reads words (unit names, Escapade cards).
    ``mode="digits"`` keeps the fast, accurate numeric config; ``mode="text"``
    allows letters and is what name/card reading must use.
    """

    DIGITS_CONFIG = r"--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789"
    TEXT_CONFIG = r"--oem 3 --psm 6"

    @staticmethod
    def read_text(frame: np.ndarray, roi: list[int], mode: str = "digits") -> str:
        if not HAS_TESSERACT or frame is None or len(roi) != 4:
            return ""
        x1, y1, x2, y2 = map(int, roi)
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return ""
        crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # Scale up 4x for Tesseract
        gray = cv2.resize(gray, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)

        # --- SMART OCR ---
        # 1. Try Otsu (automatically finds the best contrast threshold)
        _, thresh_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # 2. Try fixed threshold as fallback
        _, thresh_fixed = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

        custom_config = OcrHelper.TEXT_CONFIG if mode == "text" else OcrHelper.DIGITS_CONFIG

        for thresh in (thresh_otsu, thresh_fixed):
            try:
                txt = pytesseract.image_to_string(thresh, config=custom_config)
                cleaned = re.sub(r"\s+", " ", txt).strip()
                if cleaned:
                    return cleaned
            except Exception:
                pass

        return ""

    @staticmethod
    def read_int(frame: np.ndarray, roi: list[int], default: int | None = None) -> int | None:
        raw = OcrHelper.read_text(frame, roi, mode="digits")
        nums = re.findall(r"\d+", raw)
        return int(nums[0]) if nums else default


class BoardUnitDetector:
    def __init__(
        self,
        recognizer: ImageRecognition,
        strategy_db: dict,
        registry: NameRegistry | None = None,
        input_sim: "InputSimulation | None" = None,
    ):
        self.recognizer = recognizer
        self.strategy_db = strategy_db
        self.registry = registry or NameRegistry()
        self.input_sim = input_sim
        self.known_units = self._collect_known_units()
        self.slug_to_name = {slugify(u): u for u in self.known_units}

    def _collect_known_units(self) -> list[str]:
        units = set()
        # Dynamically load from templates folder
        for fname in self.recognizer.list_templates("units"):
            stem = os.path.splitext(fname)[0]
            if self.registry.is_ignored(stem):
                continue
            canonical = self.registry.resolve(stem) or stem
            self.registry.register_template(canonical)
            units.add(canonical)

        for strat in self.strategy_db.get("strategies", []):
            if strat.get("protagonist_slot_unit"):
                units.add(self.registry.resolve(strat["protagonist_slot_unit"]) or strat["protagonist_slot_unit"])
            for pos in strat.get("unit_positioning", []):
                if pos.get("unit"):
                    units.add(self.registry.resolve(pos["unit"]) or pos["unit"])
        return sorted(units)

    def _classify_rarity_and_stars(self, patch: np.ndarray) -> tuple[str, int]:
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]
        border = np.concatenate([hsv[:8, :, :].reshape(-1, 3), hsv[-8:, :, :].reshape(-1, 3)], axis=0)
        mean_h = float(np.mean(border[:, 0]))
        mean_s = float(np.mean(border[:, 1]))
        mean_v = float(np.mean(border[:, 2]))

        if mean_s < 45:
            rarity = "White"
        elif 15 <= mean_h <= 34 and mean_v > 140:
            rarity = "Gold"
        elif 125 <= mean_h <= 160:
            rarity = "Purple"
        elif 90 <= mean_h <= 124:
            rarity = "Blue"
        else:
            rarity = "Gold" if mean_v > 175 else "Blue"

        top_strip = hsv[: max(10, h // 4), :, :]
        gold_mask = cv2.inRange(top_strip, np.array([15, 110, 150]), np.array([35, 255, 255]))
        ratio = float(np.count_nonzero(gold_mask)) / max(1, gold_mask.size)
        if ratio > 0.24:
            stars = 3
        elif ratio > 0.11:
            stars = 2
        else:
            stars = 1
        return rarity, stars

    def inspect_slot(self, frame: np.ndarray, roi: list[int], slot_meta: dict) -> dict | None:
        if frame is None or len(roi) != 4:
            return None
        x1, y1, x2, y2 = map(int, roi)
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None

        patch = frame[y1:y2, x1:x2]
        
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        if np.count_nonzero(edges) < 900:
            return None

        rarity, stars = self._classify_rarity_and_stars(patch)

        tpl_slug, tpl_conf = self.recognizer.match_best_from_dir(patch, "units", threshold=0.70)
        if tpl_slug:
            unit_name = self.registry.resolve(tpl_slug) or self.slug_to_name.get(
                slugify(tpl_slug), tpl_slug.replace("_", " ").title()
            )
            return {
                **slot_meta,
                "unit": unit_name,
                "source": "TPL",
                "confidence": round(tpl_conf, 2),
                "rarity": rarity,
                "stars": stars,
                "roi": [x1, y1, x2, y2],
            }

        raw_text = OcrHelper.read_text(frame, [x1, y1, x2, y2 + 24], mode="text")
        if raw_text:
            matched = self.registry.resolve(raw_text, cutoff=0.70)
            if matched:
                score = difflib.SequenceMatcher(
                    None, self.registry.key(raw_text), self.registry.key(matched)
                ).ratio()
                return {
                    **slot_meta,
                    "unit": matched,
                    "source": "OCR",
                    "confidence": round(score, 2),
                    "rarity": rarity,
                    "stars": stars,
                    "roi": [x1, y1, x2, y2],
                }

        return {
            **slot_meta,
            "unit": "Unknown_Unit",
            "source": "HSV",
            "confidence": 0.50,
            "rarity": rarity,
            "stars": stars,
            "roi": [x1, y1, x2, y2],
        }

    def scan_board(self, frame: np.ndarray) -> list[dict]:
        coords = self.strategy_db.get("screen_coordinates_1080p", {}).get("unit_board_slots", {})
        detected = []
        for zone in ("on_field", "off_field"):
            for slot in coords.get(zone, []):
                res = self.inspect_slot(
                    frame,
                    slot.get("ocr_roi", []),
                    {"slot_index": slot["slot_index"], "zone": zone, "x": slot["x"], "y": slot["y"]},
                )
                if res:
                    detected.append(res)
        return detected

    def scan_bench(self, frame: np.ndarray) -> list[dict]:
        bench_slots = self.strategy_db.get("screen_coordinates_1080p", {}).get("bench_reserve_slots", [])
        detected = []
        for slot in bench_slots:
            res = self.inspect_slot(
                frame,
                slot.get("ocr_roi", []),
                {"bench_index": slot["bench_index"], "x": slot["x"], "y": slot["y"]},
            )
            if res:
                detected.append(res)
        return detected

    def scan_shop_templates(self, frame: np.ndarray) -> list[dict]:
        cards = self.strategy_db.get("screen_coordinates_1080p", {}).get("shop_card_slots", [])
        results = []
        if frame is None:
            return results
        for card in cards:
            x = card["click"]["x"]
            y = card["click"]["y"]
            x1, y1 = max(0, x - 40), max(0, y - 40)
            x2, y2 = min(frame.shape[1], x + 40), min(frame.shape[0], y + 40)
            patch = frame[y1:y2, x1:x2]

            slug, conf = self.recognizer.match_best_from_dir(patch, "units", threshold=0.60)
            if slug:
                results.append({
                    "shop_index": card["shop_index"],
                    "unit": self.registry.resolve(slug) or self.slug_to_name.get(slugify(slug), slug),
                    "slug": slug,
                    "confidence": conf,
                    "click": {"x": x, "y": y}
                })
        return results

    def execute_skill(self, skill_name: str, strategy: dict):
        if self.input_sim is None:
            LOG.warning("[Skill] No input backend configured; cannot execute %s", skill_name)
            return False
        skills = strategy.get("skills", [])
        for skill in skills:
            if skill["name"] == skill_name:
                LOG.info("[Skill] Executing %s at (%s, %s)", skill_name, skill["x"], skill["y"])
                self.input_sim.click_xy(skill["x"], skill["y"], label=skill_name)
                return True
        return False

# ==============================================================================
# 3. PREP-PHASE AUTOMATION ENGINES (v4.7)
# ==============================================================================

class EquipmentCraftingEngine:
    def __init__(
        self,
        input_sim: InputSimulation,
        recognizer: ImageRecognition,
        strategy_db: dict,
        config: dict,
        unit_registry: NameRegistry | None = None,
        item_registry: NameRegistry | None = None,
    ):
        self.input_sim = input_sim
        self.recognizer = recognizer
        self.strategy_db = strategy_db
        self.config = config
        self.units = unit_registry or NameRegistry()
        self.items = item_registry or NameRegistry()
        self._action_cooldowns: dict[str, float] = {}
        self._warned_unverified = False
        self.min_edges = int(config.get("vision", {}).get("min_edges", 900))

    def _cooldown_ready(self, key: str, seconds: float = 6.0) -> bool:
        now = time.time()
        if now - self._action_cooldowns.get(key, 0.0) >= seconds:
            self._action_cooldowns[key] = now
            return True
        return False

    def scan_inventory(self, frame: np.ndarray) -> list[dict]:
        slots = self.strategy_db.get("screen_coordinates_1080p", {}).get("equipment_inventory_slots", [])
        found_items = []
        skipped = 0
        if frame is None:
            return found_items
        h, w = frame.shape[:2]
        for slot in slots:
            cx, cy = slot["x"], slot["y"]
            x1, y1 = max(0, cx - 32), max(0, cy - 32)
            x2, y2 = min(w, cx + 32), min(h, cy + 32)
            patch = frame[y1:y2, x1:x2]
            if patch.size == 0:
                continue
            gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            if np.count_nonzero(edges) < self.min_edges:
                continue
            slug, conf = self.recognizer.match_best_from_dir(patch, "items", threshold=0.72)
            if not slug:
                continue
            if not self.items.is_verified(slug):
                skipped += 1
                continue
            name = self.items.resolve(slug) or slug.replace("_", " ").title()
            found_items.append({
                "item_slot": slot["item_slot"],
                "slug": slug,
                "name": name,
                "confidence": round(conf, 2),
                "x": cx,
                "y": cy,
            })
        if skipped and not self._warned_unverified:
            self._warned_unverified = True
            LOG.warning(
                "[Craft] Ignoring %d unverified item template(s). templates/items contains "
                "auto-downloaded placeholders; add verified slugs to unit_registry.json -> "
                "items.verified before enabling crafting.",
                skipped,
            )
        return found_items

    def resolve_target_unit(self, item_name: str, strategy: dict, deployed_units: list[dict]) -> dict | None:
        overrides = self.config.get("multi_carry_routing_overrides", {}).get(strategy.get("id", ""), {})
        return choose_equip_target(
            item_name, strategy, deployed_units,
            overrides=overrides, key=self.units.key,
        )

    def run_crafting_and_equipping(self, frame: np.ndarray, strategy: dict, deployed_units: list[dict], log_fn):
        inv = self.scan_inventory(frame)
        if not inv:
            return inv
        recipes = self.strategy_db.get("crafting_recipes", [])
        archetype_id = strategy.get("id", "")

        craft = plan_craft(inv, recipes, archetype_id, key=self.items.key)
        if craft:
            recipe = craft["recipe"]
            a, b = craft["component_a"], craft["component_b"]
            if self._cooldown_ready(f"craft_{recipe.get('item_id', recipe.get('name'))}", seconds=8.0):
                log_fn(f"[Craft] Combining {a['name']} + {b['name']} -> {recipe['name']}")
                self.input_sim.drag_xy(a["x"], a["y"], b["x"], b["y"], label=f"craft_{recipe.get('item_id', '')}")
                time.sleep(0.35)
                return inv

        crafted_keys = {self.items.key(r["name"]) for r in recipes if r.get("name")}
        for item in inv:
            if self.items.key(item["slug"]) not in crafted_keys:
                continue
            target = self.resolve_target_unit(item["name"], strategy, deployed_units)
            if target and self._cooldown_ready(f"equip_{item['slug']}_to_{target['unit']}", seconds=6.0):
                log_fn(f"[Equip] Routing {item['name']} -> {target['unit']} (Slot {target['slot_index']})")
                self.input_sim.drag_xy(item["x"], item["y"], target["x"], target["y"], label=f"equip_{item['slug']}")
                time.sleep(0.3)
        return inv


class PrepPhaseController:
    def __init__(
        self,
        config: dict,
        strategy_db: dict,
        input_sim: InputSimulation,
        recognizer: ImageRecognition,
        unit_registry: NameRegistry | None = None,
        item_registry: NameRegistry | None = None,
    ):
        self.config = config
        self.strategy_db = strategy_db
        self.input_sim = input_sim
        self.recognizer = recognizer
        self.units = unit_registry or NameRegistry()
        self.items = item_registry or NameRegistry()
        self.detector = BoardUnitDetector(recognizer, strategy_db, self.units, input_sim)
        self.crafter = EquipmentCraftingEngine(
            input_sim, recognizer, strategy_db, config, self.units, self.items
        )
        self.pity_refresh_counter = 0
        self._refreshed_this_cycle = False
        self._last_escapade_scan = 0.0
        self.last_telemetry = {
            "gold": None, "level": None, "hp": None, "pity": 0,
            "board": [], "bench": [], "inventory": [], "protagonist": None, "bonds": {},
            "state": "UNKNOWN",
        }

    def get_active_strategy(self) -> dict:
        sid = self.config.get("active_strategy_id", "combo_aglaea")
        for s in self.strategy_db.get("strategies", []):
            if s["id"] == sid:
                return s
        strategies = self.strategy_db.get("strategies", [])
        return strategies[0] if strategies else {"id": "none", "name": "None", "unit_positioning": []}

    def read_hud(self, frame: np.ndarray) -> dict:
        ui = self.strategy_db.get("screen_coordinates_1080p", {}).get("ui_controls", {})
        return {
            "gold": OcrHelper.read_int(frame, ui.get("gold_counter_ocr_roi", []), default=None),
            "level": OcrHelper.read_int(frame, ui.get("squad_level_ocr_roi", []), default=None),
            "hp": OcrHelper.read_int(frame, ui.get("squad_hp_ocr_roi", []), default=None),
        }

    @staticmethod
    def hud_looks_like_prep(hud: dict) -> bool:
        """Cheap sanity gate: are the shop/prep HUD numbers visible?

        This keeps the bot from clicking buy/reroll coordinates while a battle
        or another screen is up. It requires at least two plausible readings,
        so a single garbage OCR result cannot unlock actions by itself.
        """
        def ok(value, lo, hi):
            return value is not None and lo <= value <= hi

        votes = sum((
            ok(hud.get("gold"), 0, 999),
            ok(hud.get("level"), 1, 20),
            ok(hud.get("hp"), 0, 100),
        ))
        return votes >= 2

    def handle_escapade(self, frame: np.ndarray, strategy: dict, log_fn) -> bool:
        # Escapade cards are mostly static; OCR is expensive, so scan at most
        # once every two seconds instead of every cycle.
        now = time.time()
        if now - self._last_escapade_scan < 2.0:
            return False
        self._last_escapade_scan = now

        ui = self.strategy_db.get("screen_coordinates_1080p", {}).get("ui_controls", {})
        choices = ui.get("escapade_choices", [])
        keywords = [k.lower() for k in strategy.get("escapade_keywords", [])]
        best_choice = None
        best_score = 0
        for ch in choices:
            txt = OcrHelper.read_text(frame, ch.get("ocr_roi", []), mode="text").lower()
            if not txt:
                continue
            score = sum(1 for kw in keywords if kw in txt)
            if score > best_score:
                best_score = score
                best_choice = ch
        if best_choice and best_score > 0:
            log_fn(f"[Escapade] Selecting card #{best_choice['index']} (matched {best_score} keywords)")
            self.input_sim.click_xy(best_choice["x"], best_choice["y"], label="escapade_choice")
            time.sleep(0.4)
            return True
        return False

    def run_shop_and_xp(self, frame: np.ndarray, strategy: dict, log_fn, hud: dict | None = None):
        ui = self.strategy_db.get("screen_coordinates_1080p", {}).get("ui_controls", {})
        econ = self.strategy_db.get("economy_and_refresh_thresholds", {})
        hud = hud if hud is not None else self.read_hud(frame)
        gold, level, hp = hud.get("gold"), hud.get("level"), hud.get("hp")

        self.last_telemetry["gold"] = gold
        self.last_telemetry["level"] = level
        self.last_telemetry["hp"] = hp
        self.last_telemetry["pity"] = self.pity_refresh_counter

        automation = self.config.get("automation", {})
        buy_enabled = automation.get("enable_shop_auto_buy", True)
        xp_enabled = automation.get("enable_auto_xp_leveling", True)
        positioning = strategy.get("unit_positioning", [])
        protagonist_name = strategy.get("protagonist_slot_unit") or ""
        wanted = [p["unit"] for p in positioning if p.get("unit")]
        if protagonist_name:
            wanted.append(protagonist_name)

        protagonist_in_shop = False
        if buy_enabled:
            shop_units = self.detector.scan_shop_templates(frame)
            buys, protagonist_in_shop = select_purchases(
                [ShopUnit(name=u["unit"], confidence=u["confidence"], x=u["click"]["x"], y=u["click"]["y"])
                 for u in shop_units],
                wanted, protagonist_name, key=self.units.key,
            )
            for unit in buys:
                log_fn(f"[Shop] Buying {unit.name} (Conf: {int(unit.confidence * 100)}%)")
                self.input_sim.click_xy(unit.x, unit.y, label=f"buy_{unit.name}")
                time.sleep(0.2)

        if protagonist_in_shop:
            if self.pity_refresh_counter > 0:
                log_fn(f"[Shop] Protagonist ({protagonist_name}) spotted - pity reset")
            self.pity_refresh_counter = 0

        thresholds = ShopThresholds.from_strategy(strategy, econ)
        plan = plan_shop(
            ShopState(
                gold=gold, level=level, hp=hp,
                pity=self.pity_refresh_counter,
                protagonist_in_shop=protagonist_in_shop,
                refreshed_this_cycle=self._refreshed_this_cycle,
                buy_enabled=buy_enabled,
                xp_enabled=xp_enabled,
            ),
            thresholds,
        )
        if plan.reasons:
            log_fn("[Shop] " + "; ".join(plan.reasons))

        if plan.level_up:
            xp_btn = ui.get("buy_xp_level_up_button")
            if xp_btn:
                log_fn(f"[XP] Leveling up (Lv.{level} -> Target Lv.{thresholds.primary_roll_level}, Gold={gold})")
                self.input_sim.click_xy(xp_btn["x"], xp_btn["y"], label="buy_xp")
                time.sleep(0.2)

        if plan.refresh:
            ref_btn = ui.get("shop_refresh_button")
            if ref_btn:
                log_fn(f"[Shop] Refreshing shop (Gold={gold}, Pity={self.pity_refresh_counter}/{thresholds.pity_refreshes})")
                self.input_sim.click_xy(ref_btn["x"], ref_btn["y"], label="refresh_shop")
                # Only count the pity step once the action was actually issued.
                self.pity_refresh_counter += 1
                self._refreshed_this_cycle = True
                time.sleep(0.25)

    def deploy_bench_and_protagonist(self, frame: np.ndarray, strategy: dict, board: list[dict], bench: list[dict], log_fn):
        pos_map = {self.units.key(p["unit"]): p for p in strategy.get("unit_positioning", [])}
        occupied_slots = {u["slot_index"]: u for u in board}

        if self.config.get("automation", {}).get("enable_bench_deployment", True):
            for b_unit in bench:
                spec = pos_map.get(self.units.key(b_unit["unit"]))
                if not spec:
                    continue
                target_slot_idx = spec["slot_index"]
                target_xy = spec.get("screen_xy")
                if not target_xy or len(target_xy) != 2:
                    continue
                if target_slot_idx not in occupied_slots:
                    log_fn(f"[Deploy] {b_unit['unit']} Bench #{b_unit['bench_index']} -> Slot #{target_slot_idx}")
                    self.input_sim.drag_xy(b_unit["x"], b_unit["y"], target_xy[0], target_xy[1], label="deploy_unit")
                    time.sleep(0.25)
                    break
                else:
                    occupant = occupied_slots[target_slot_idx]
                    if self.units.key(occupant["unit"]) not in pos_map:
                        log_fn(f"[Swap] {occupant['unit']} -> {b_unit['unit']} in Slot #{target_slot_idx}")
                        self.input_sim.drag_xy(b_unit["x"], b_unit["y"], target_xy[0], target_xy[1], label="swap_unit")
                        time.sleep(0.25)
                        break

        if self.config.get("automation", {}).get("enable_protagonist_plot_armor", True):
            ui = self.strategy_db.get("screen_coordinates_1080p", {}).get("ui_controls", {})
            prot_slot = ui.get("protagonist_plot_armor_slot")
            if prot_slot:
                current_prot = self.detector.inspect_slot(frame, prot_slot.get("ocr_roi", []), {"slot": "protagonist"})
                self.last_telemetry["protagonist"] = current_prot["unit"] if current_prot else None
                for prio_unit in strategy.get("plot_armor_priority", []):
                    if current_prot and self.units.key(current_prot["unit"]) == self.units.key(prio_unit):
                        break
                    prio_key = self.units.key(prio_unit)
                    candidate = next(
                        (u for u in board + bench if self.units.key(u["unit"]) == prio_key), None
                    )
                    if candidate:
                        log_fn(f"[Plot Armor] Slotting {candidate['unit']} as Protagonist")
                        self.input_sim.drag_xy(candidate["x"], candidate["y"], prot_slot["x"], prot_slot["y"], label="assign_protagonist")
                        self.last_telemetry["protagonist"] = candidate["unit"]
                        time.sleep(0.25)
                        break

    def run_prep_cycle(self, frame: np.ndarray, log_fn, hud: dict | None = None) -> dict:
        self._refreshed_this_cycle = False
        strategy = self.get_active_strategy()
        if not strategy.get("unit_positioning"):
            log_fn("[Warn] Strategy JSON has no strategies / unit_positioning - check your JSON")
            return self.last_telemetry

        automation = self.config.get("automation", {})
        if automation.get("enable_escapade_handler", True):
            self.handle_escapade(frame, strategy, log_fn)

        self.run_shop_and_xp(frame, strategy, log_fn, hud=hud)

        board: list[dict] = []
        bench: list[dict] = []
        if automation.get("enable_board_unit_detection", True):
            board = self.detector.scan_board(frame)
            bench = self.detector.scan_bench(frame)
        self.last_telemetry["board"] = board
        self.last_telemetry["bench"] = bench

        self.deploy_bench_and_protagonist(frame, strategy, board, bench, log_fn)

        inv = []
        if automation.get("enable_equipment_crafting", True):
            inv = self.crafter.run_crafting_and_equipping(frame, strategy, board, log_fn)
        self.last_telemetry["inventory"] = inv
        self.last_telemetry["bonds"] = {b: "Active" for b in strategy.get("bonds", [])}
        self.last_telemetry["pity"] = self.pity_refresh_counter
        return self.last_telemetry

# ==============================================================================
# 4. CLICK-THROUGH TRANSPARENT LIVE VISUAL BOARD OVERLAY HUD
# ==============================================================================

class LiveBoardOverlay:
    RARITY_COLORS = {
        "Gold": "#FFD700", "Purple": "#C084FC", "Blue": "#38BDF8", "White": "#E2E8F0",
    }

    def __init__(self, root: tk.Tk, config: dict, strategy_db: dict, input_sim: InputSimulation,
                 registry: NameRegistry | None = None):
        self.root = root
        self.config = config
        self.strategy_db = strategy_db
        self.input_sim = input_sim
        self.registry = registry or NameRegistry()
        self.win: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.visible = False
        self._pending = None

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def show(self):
        if self.win is not None and tk.Toplevel.winfo_exists(self.win):
            self.win.deiconify()
            self.visible = True
            return
            
        hwnd = self.input_sim.hwnd
        if hwnd:
            try:
                left, top, right, bot = win32gui.GetClientRect(hwnd)
                pt = win32gui.ClientToScreen(hwnd, (left, top))
                ox, oy = pt
                w, h = right - left, bot - top
            except Exception:
                ox, oy = 0, 0
                w, h = 1920, 1080
        else:
            ox, oy = 0, 0
            w, h = 1920, 1080

        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{w}x{h}+{ox}+{oy}")
        self.win.attributes("-topmost", True)
        chroma_key = "#010101"
        self.win.configure(bg=chroma_key)
        try:
            self.win.attributes("-transparentcolor", chroma_key)
        except tk.TclError:
            self.win.attributes("-alpha", 0.35)
        self.canvas = tk.Canvas(self.win, width=w, height=h, bg=chroma_key, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._make_click_through()
        self.visible = True

    def _make_click_through(self):
        if os.name != "nt" or self.win is None:
            return
        try:
            self.win.update_idletasks()
            GA_ROOT = 2
            hwnd = ctypes.windll.user32.GetAncestor(self.win.winfo_id(), GA_ROOT)
            if not hwnd:
                hwnd = self.win.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        except Exception:
            pass

    def hide(self):
        if self.win is not None and tk.Toplevel.winfo_exists(self.win):
            self.win.withdraw()
            self.visible = False

    def request_render(self, telemetry: dict, strategy: dict):
        self._pending = (telemetry, strategy)

    def drain_pending(self):
        if not self.visible or self.canvas is None or self.win is None or not tk.Toplevel.winfo_exists(self.win):
            self._pending = None
            return
        payload = self._pending
        self._pending = None
        if payload:
            self._render_internal(*payload)

    def render(self, telemetry: dict, strategy: dict):
        if not self.visible or self.canvas is None or self.win is None or not tk.Toplevel.winfo_exists(self.win):
            return
        self._render_internal(telemetry, strategy)

    def _render_internal(self, telemetry: dict, strategy: dict):
        c = self.canvas
        c.delete("all")

        c.create_rectangle(18, 18, 430, 148, fill="#0B1120", outline="#00E5FF", width=2)
        c.create_text(30, 34, anchor="w",
                      text=f"HSR Currency Wars v4.7 | {strategy.get('name', '?')} | {telemetry.get('state', '?')}",
                      fill="#00E5FF", font=("Segoe UI", 10, "bold"))
        gold_s = telemetry.get("gold") if telemetry.get("gold") is not None else "?"
        lvl_s = telemetry.get("level") if telemetry.get("level") is not None else "?"
        hp_s = telemetry.get("hp") if telemetry.get("hp") is not None else "?"
        pity_s = telemetry.get("pity", 0)
        c.create_text(30, 60, anchor="w",
                      text=f"Gold: {gold_s}g (Cap 30g)  |  Squad Lv.{lvl_s}  |  HP: {hp_s}",
                      fill="#E2E8F0", font=("Consolas", 9, "bold"))
        prot_s = telemetry.get("protagonist") or "Unassigned"
        c.create_text(30, 84, anchor="w", text=f"Protagonist: {prot_s}  |  Bond Pity: {pity_s}/2",
                      fill="#FACC15", font=("Consolas", 9))
        bonds_str = ", ".join(strategy.get("bonds", []))
        c.create_text(30, 108, anchor="w", text=f"Target Bonds: {bonds_str}",
                      fill="#4ADE80", font=("Consolas", 9))
        c.create_text(30, 130, anchor="w", text="Hotkeys: F10 Pause | F12 Emergency Stop",
                      fill="#94A3B8", font=("Segoe UI", 8))

        prot_slot = self.strategy_db.get("screen_coordinates_1080p", {}).get("ui_controls", {}).get("protagonist_plot_armor_slot")
        if prot_slot and "ocr_roi" in prot_slot:
            x1, y1, x2, y2 = prot_slot["ocr_roi"]
            is_match = self.registry.key(prot_s) == self.registry.key(strategy.get("protagonist_slot_unit") or "")
            p_color = "#22C55E" if is_match else "#F59E0B"
            c.create_rectangle(x1, y1, x2, y2, outline=p_color, width=2, dash=(4, 2))
            c.create_text(x1, y1 - 10, anchor="w", text=f"PLOT ARMOR: {prot_s}",
                          fill=p_color, font=("Consolas", 9, "bold"))

        expected_zones = {self.registry.key(p["unit"]): p["zone"] for p in strategy.get("unit_positioning", [])}
        for u in telemetry.get("board", []):
            x1, y1, x2, y2 = u["roi"]
            col = self.RARITY_COLORS.get(u.get("rarity", "White"), "#E2E8F0")
            stars_txt = "★" * u.get("stars", 1)
            exp_z = expected_zones.get(self.registry.key(u["unit"]))
            zone_tag = "OK" if (exp_z is None or exp_z == u["zone"]) else "WRONG ZONE"
            border_col = "#EF4444" if zone_tag == "WRONG ZONE" else col
            c.create_rectangle(x1, y1, x2, y2, outline=border_col, width=2)
            c.create_rectangle(x1, y1 - 22, x2 + 28, y1 - 2, fill="#090D16", outline=border_col, width=1)
            c.create_text(
                x1 + 4, y1 - 12, anchor="w",
                text=f"{u['unit']} {stars_txt} [{u['source']}:{int(u['confidence']*100)}%]",
                fill=border_col, font=("Consolas", 8, "bold"),
            )
            if zone_tag == "WRONG ZONE":
                c.create_text(x1 + 4, y2 + 10, anchor="w", text="MOVE ZONE!",
                              fill="#EF4444", font=("Consolas", 8, "bold"))

        for b in telemetry.get("bench", []):
            x1, y1, x2, y2 = b["roi"]
            c.create_rectangle(x1, y1, x2, y2, outline="#E879F9", width=2)
            c.create_text(x1, y2 + 10, anchor="w", text=f"B{b['bench_index']}: {b['unit']}",
                          fill="#E879F9", font=("Consolas", 8, "bold"))

        for it in telemetry.get("inventory", []):
            ix, iy = it["x"], it["y"]
            c.create_rectangle(ix - 30, iy - 30, ix + 30, iy + 30, outline="#FB923C", width=2)
            c.create_text(ix - 32, iy - 38, anchor="w", text=it["name"],
                          fill="#FB923C", font=("Consolas", 8, "bold"))

# ==============================================================================
# 5. MAIN AUTOMATION ENGINE & SETTINGS GUI
# ==============================================================================

class CurrencyWarsApp:
    def __init__(self, root: tk.Tk | None, headless: bool = False):
        self.headless = headless
        if headless:
            # Mock root with minimal required methods
            class MockRoot:
                def after(self, delay, callback):
                    # Run callback immediately in headless mode
                    callback()
                def title(self, *args): pass
                def geometry(self, *args): pass
                def configure(self, *args, **kwargs): pass
                def pack(self, *args, **kwargs): pass
            self.root = MockRoot()
        else:
            self.root = root
            self.root.title("HSR Currency Wars v4.7 Automation Suite (Background)")
            self.root.geometry("880x700")
            self.root.configure(bg="#0A0E1A")
            # Tk swallows callback exceptions into stderr, which a windowed
            # build does not have. Route them to the log and a dialog instead.
            self.root.report_callback_exception = self._report_callback_exception
        self._startup_errors: list[str] = []
        self._startup_warnings: list[str] = []
        self._last_battle_click = 0.0
        self._battle_started_at = 0.0
        self._last_scene_action: dict[str, float] = {}
        self._last_unknown_log = 0.0
        self._last_advance = 0.0
        self._ui_queue: "queue.Queue" = queue.Queue()
        self._log_queue: "queue.Queue" = queue.Queue(maxsize=2000)
        self._capture: "WindowCapture | None" = None
        self._capture_target = None
        self._last_frame_size: tuple[int, int] | None = None
        self._warned_resolution = False
        self._warned_hud_rois = False
        # Wall-clock budget for the automation loop (0/None = unlimited).
        self._run_deadline_seconds: float | None = None
        self.run_count = 0

        self.base_dir = _app_base_dir()
        self.config_path = self.base_dir / "config.json"
        _attach_log_handlers(self.base_dir / "automation.log", console=headless)
        self.config = self._load_json(self.config_path, default=self._default_config())
        self.strategy_path = self.base_dir / self.config.get("strategy_json_path", "hsr_currency_wars_v4_7_strategies-v4.json")
        self.strategy_db = self._load_json(self.strategy_path, default={"strategies": [], "screen_coordinates_1080p": {}})

        self._ensure_template_dirs()

        registry_path = self.base_dir / self.config.get("name_registry_path", "unit_registry.json")
        self.unit_registry = NameRegistry.from_file(registry_path, section="units")
        self.item_registry = NameRegistry.from_file(registry_path, section="items")

        template_dir = self.config.get("paths", {}).get("templates", "./templates")
        ignore_cfg = self.config.get("template_ignore", {})
        self.recognizer = ImageRecognition(
            template_dir,
            ignore_slugs=set(ignore_cfg.get("units", [])) | set(ignore_cfg.get("items", [])),
        )
        self.input_sim = InputSimulation(self.config)
        self.input_sim.log_fn = self.log
        self.prep_ctrl = PrepPhaseController(
            self.config, self.strategy_db, self.input_sim, self.recognizer,
            self.unit_registry, self.item_registry,
        )
        self.scene_detector = SceneDetector(self.recognizer, self.config)
        if not headless:
            self.overlay = LiveBoardOverlay(
                self.root, self.config, self.strategy_db, self.input_sim, self.unit_registry
            )
        else:
            self.overlay = None

        self._detect_game_window()

        self.running = False
        self.paused = False
        self.vars: dict[tuple, tk.Variable] = {}

        if not headless:
            self._build_ui()
            self._load_into_ui()
            self._register_hotkeys()
            self._startup_diagnostics()
            self._start_ui_tick()
        else:
            self._startup_diagnostics()

    def _default_config(self) -> dict:
        return {
            "window_title": "Honkai: Star Rail",
            "strategy_json_path": "hsr_currency_wars_v4_7_strategies-v4.json",
            "name_registry_path": "unit_registry.json",
            "active_strategy_id": "combo_aglaea",
            "resolution": {"width": 1920, "height": 1080},
            "game_offset": {"x": 0, "y": 0},
            "scenes": dict(DEFAULT_SCENES),
            "template_ignore": {"units": [], "items": []},
            "vision": {"min_edges": 900},
            "automation": {
                "enable_auto_battle": True, "battle_timeout_seconds": 180, "max_runs": 0, "delay_between_runs_seconds": 2.5,
                # Safety: dry-run is on by default. Live clicks require explicitly setting this to false.
                "dry_run": True, "enable_shop_auto_buy": True,
                "enable_auto_xp_leveling": True, "enable_bench_deployment": True, "enable_protagonist_plot_armor": True,
                "enable_equipment_crafting": True, "enable_board_unit_detection": True, "enable_escapade_handler": True,
                "enable_live_overlay": True, "require_prep_ui": True, "advance_on_unknown": False,
                "wait_for_window_seconds": 0,
            },
            "safety": {"pause_key": "f10", "stop_key": "f12", "emergency_stop_on_error": True},
            "paths": {"templates": "./templates"},
            "clicks": {"start_battle": {"x": 960, "y": 930}, "confirm": {"x": 960, "y": 600}, "next": {"x": 1600, "y": 900}, "collect": {"x": 960, "y": 500}},
        }

    def _ensure_template_dirs(self):
        descriptions = {
            "units": "Place unit portrait crops here as <unit_slug>.png.\nExample: aglaea.png, sunday.png\n",
            "items": "Place item icon crops here as <item_slug>.png.\nExample: anti_grav_boots.png\n",
            "scenes": (
                "Place small crops of distinctive UI regions here.\n"
                "victory.png / reward.png / battle_start.png are used to advance the game loop.\n"
                "Capture them with:\n"
                "  python currency_wars_app-v9.py --capture-scene victory --scene-roi x1,y1,x2,y2\n"
            ),
        }
        for sub, text in descriptions.items():
            d = self.base_dir / "templates" / sub
            d.mkdir(parents=True, exist_ok=True)
            readme = d / "README.txt"
            if not readme.exists():
                readme.write_text(text, encoding="utf-8")
        for sub in ("captures", "review"):
            (self.base_dir / sub).mkdir(exist_ok=True)

    def _load_json(self, path: Path, default: dict) -> dict:
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self._startup_errors.append(f"Failed to load {path.name}: {e}")
                return default
        return default

    def _detect_game_window(self) -> bool:
        title = self.config.get("window_title", "Honkai: Star Rail")
        try:
            hwnd = win32gui.FindWindow(None, title)
            if hwnd:
                self.input_sim.update_hwnd(hwnd)
                return True
        except Exception as e:
            self._startup_errors.append(f"Window detect failed: {e}")
        return False

    def _register_hotkeys(self):
        try:
            keyboard.add_hotkey(self.config.get("safety", {}).get("pause_key", "f10"), self._toggle_pause)
            keyboard.add_hotkey(self.config.get("safety", {}).get("stop_key", "f12"), self._stop_automation)
        except Exception as e:
            self._startup_errors.append(f"Hotkey registration failed: {e}")

    def launch_game(self):
        path = self.config.get("game_path")
        if path and os.path.exists(path):
            self.log(f"[System] Launching game: {path}")
            subprocess.Popen(path)
        else:
            self.log(f"[Error] Game path not found or invalid: {path}")

    def _startup_diagnostics(self):
        if not is_admin():
            self._startup_warnings.append(
                "Not running as Administrator - global F10/F12 hotkeys and clicks may not fire."
            )
        # Safety: if dry_run is not explicitly true, flag that the bot will send
        # live clicks so this can never go unnoticed.
        if not self.config.get("automation", {}).get("dry_run", True):
            self._startup_warnings.append(
                "WARNING: dry_run is OFF. The bot will send REAL clicks (PostMessage) to the game window. "
                "Enable 'Dry Run Mode' in the Strategy tab, or set automation.dry_run=true in config.json, "
                "before letting it run unattended."
            )
        if HAS_TESSERACT:
            try:
                v = pytesseract.get_tesseract_version()
                self.log(f"Tesseract OCR ready (version {v})")
            except Exception as e:
                self._startup_errors.append(f"Tesseract binary not found on PATH: {e}")
        else:
            self.log("pytesseract not installed - running in template/HSV-only mode")
        if not HAS_WGC:
            detail = f" ({WGC_IMPORT_ERROR})" if WGC_IMPORT_ERROR else ""
            self._startup_errors.append(
                "wgc_python capture engine failed to load"
                + detail
                + ". Source run: pip install wgc-python. "
                "Frozen EXE: rebuild with `python build_exe.py` (it bundles wgc_python.dll)."
            )
        else:
            self.log("WGC capture engine verified.")

        if self.scene_detector.missing:
            self._startup_warnings.append(
                "Missing scene templates: " + ", ".join(self.scene_detector.missing)
            )
            self.log(
                "[Scenes] The loop cannot advance past prep without these. Capture one per screen with:"
            )
            for scene in self.scene_detector.missing:
                self.log(
                    f"  python currency_wars_app-v9.py --capture-scene {scene} --scene-roi x1,y1,x2,y2"
                )
        else:
            self.log(f"[Scenes] Loaded {len(self.scene_detector.available)} scene templates.")

        strategy_units = set()
        for strat in self.strategy_db.get("strategies", []):
            if strat.get("protagonist_slot_unit"):
                strategy_units.add(strat["protagonist_slot_unit"])
            for pos in strat.get("unit_positioning", []):
                if pos.get("unit"):
                    strategy_units.add(pos["unit"])
        unresolved = self.unit_registry.unresolved(sorted(strategy_units))
        if unresolved:
            self._startup_warnings.append(
                "Strategy units without a matching template: " + ", ".join(unresolved)
            )

        recipes = self.strategy_db.get("crafting_recipes", [])
        recipe_names = {c for r in recipes for c in r.get("components", [])}
        recipe_names |= {r.get("name", "") for r in recipes}
        unmapped = self.item_registry.unresolved(sorted(n for n in recipe_names if n))
        if unmapped:
            self.log(
                "[Craft] Item templates not mapped (crafting will ignore these until you add "
                "aliases + verified entries in unit_registry.json): " + ", ".join(unmapped)
            )

        loaded = self.recognizer.preload(("units", "items"))
        self.log(f"[Templates] Preloaded {loaded} templates.")

        if self._detect_game_window():
            self.log(f"[Startup] Found game window HWND: {self.input_sim.hwnd}")
        else:
            wait = float(self.config.get("automation", {}).get("wait_for_window_seconds", 0) or 0)
            msg = f"Could not find window with title '{self.config.get('window_title')}'"
            if wait:
                msg += f" - will wait up to {int(wait)}s for it to appear"
            else:
                msg += " - will wait indefinitely (start the game or press F12 to stop)"
            self._startup_warnings.append(msg)

        for msg in self._startup_errors:
            self.log(f"[Startup][ERROR] {msg}")
        for msg in self._startup_warnings:
            self.log(f"[Startup][WARN] {msg}")
        self.log(f"Strategy JSON: {self.strategy_path.name} ({len(self.strategy_db.get('strategies', []))} strategies loaded)")

    def _post_ui(self, fn):
        """Run ``fn`` on the Tk thread, or inline in headless mode."""
        if self.headless:
            fn()
        else:
            self._ui_queue.put(fn)

    def _toggle_pause(self):
        def _apply():
            self.paused = not self.paused
            self.log(f"[Hotkey] Automation {'PAUSED' if self.paused else 'RESUMED'}")
        self._post_ui(_apply)

    def _stop_automation(self):
        def _apply():
            self.running = False
            self.log("[Hotkey] Emergency STOP triggered")
        self._post_ui(_apply)

    def log(self, msg: str):
        LOG.info(msg)
        if self.headless:
            return
        try:
            self._log_queue.put_nowait(f"[{time.strftime('%H:%M:%S')}] {msg}")
        except queue.Full:
            pass

    def _report_callback_exception(self, exc_type, exc, tb):
        """Show and persist errors raised inside Tk callbacks."""
        import traceback
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        LOG.error("GUI callback failed:\n%s", text)
        _write_crash_log(text, name="crash.log")
        try:
            self.log(f"[Error] {exc_type.__name__}: {exc}")
        except Exception:
            pass
        try:
            messagebox.showerror(
                "Currency Wars - error",
                f"{exc_type.__name__}: {exc}\n\nDetails were written to automation.log and crash.log.",
            )
        except Exception:
            pass

    def capture_game_frame(self) -> np.ndarray | None:
        if not HAS_WGC:
            return None

        title = self.config.get("window_title", "Honkai: Star Rail")
        if self._capture_target is None:
            try:
                windows = enumerate_windows()
            except Exception as e:
                self.log(f"[Capture] Window enumeration failed: {e}")
                return None
            for win_title, win_class in windows:
                if title.lower() in win_title.lower():
                    if win_class in ("UnityWndClass", "UnityWnd"):
                        self._capture_target = (win_title, win_class)
                        break
            if self._capture_target is None:
                for win_title, win_class in windows:
                    if title.lower() in win_title.lower():
                        self._capture_target = (win_title, win_class)
                        break
            if self._capture_target is None:
                return None
            self.log(f"[Capture] Using window '{self._capture_target[0]}' ({self._capture_target[1]})")
            # Keep the input backend pointing at the same window capture sees.
            try:
                hwnd = win32gui.FindWindow(None, self._capture_target[0])
                if hwnd:
                    self.input_sim.update_hwnd(hwnd)
            except Exception:
                pass

        try:
            if self._capture is None:
                self._capture = WindowCapture(
                    self._capture_target[0], self._capture_target[1], client_area_only=True
                )
            frame = self._capture.capture_one(timeout=1.0)
            if frame is None:
                return None
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            h, w = bgr.shape[:2]
            self._last_frame_size = (w, h)
            expected = self.config.get("resolution", {})
            exp_w, exp_h = int(expected.get("width", 1920)), int(expected.get("height", 1080))
            if (w, h) != (exp_w, exp_h):
                # A window that is a few pixels off 16:9 (window borders, DPI
                # scaling) shifts every calibrated ROI by enough to miss small
                # OCR boxes. Rescale the frame to the reference size so ROIs and
                # template matching keep working, and remember the factor so
                # clicks can be mapped back to real window pixels.
                aspect_err = abs((w / max(1, h)) - (exp_w / max(1, exp_h)))
                if aspect_err <= 0.02:
                    bgr = cv2.resize(bgr, (exp_w, exp_h), interpolation=cv2.INTER_LINEAR)
                    self.input_sim.set_frame_scale(w / exp_w, h / exp_h)
                    if not self._warned_resolution:
                        self._warned_resolution = True
                        self.log(
                            f"[Capture] Frame {w}x{h} rescaled to {exp_w}x{exp_h} "
                            f"(clicks scaled by {w / exp_w:.4f}, {h / exp_h:.4f}). "
                            "Set the game to exactly 1920x1080 windowed for best accuracy."
                        )
                elif not self._warned_resolution:
                    self._warned_resolution = True
                    self.log(
                        f"[Capture][WARN] Frame is {w}x{h} ({w / max(1, h):.3f} aspect) but the "
                        f"strategy ROIs assume {exp_w}x{exp_h} (1.778). Rescaling would distort the "
                        "image, so ROIs will be wrong. Set the game to 1920x1080 windowed."
                    )
            return bgr
        except Exception as e:
            self.log(f"[Error] WGC capture failed: {e}")
            self._close_capture()
            return None

    def _close_capture(self):
        if self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                pass
            self._capture = None
        self._capture_target = None

    def capture_scene(self, name: str, roi: list[int] | None = None) -> bool:
        """Grab the current frame and save a scene template for ``name``.

        With ``roi`` only that crop is written (recommended: small, stable UI
        region). Without it the full frame is saved, which is less reliable as
        a template but useful for inspecting what the bot sees.
        """
        frame = self.capture_game_frame()
        if frame is None:
            self.log("[Scene] Capture failed - is the game running and visible?")
            return False

        stamp = time.strftime("%Y%m%d_%H%M%S")
        capture_dir = self.base_dir / "captures"
        capture_dir.mkdir(exist_ok=True)
        cv2.imwrite(str(capture_dir / f"scene_{name}_{stamp}_full.png"), frame)

        patch = frame
        if roi:
            if len(roi) != 4:
                self.log("[Scene] --scene-roi must be x1,y1,x2,y2")
                return False
            x1, y1, x2, y2 = roi
            h, w = frame.shape[:2]
            patch = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
            if patch.size == 0:
                self.log(f"[Scene] ROI {roi} is empty for a {w}x{h} frame")
                return False

        out = self.base_dir / "templates" / "scenes" / f"{name}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(out), patch):
            self.log(f"[Scene] Failed to write {out}")
            return False
        self.log(f"[Scene] Saved {out} ({patch.shape[1]}x{patch.shape[0]})")
        self.log("[Scene] Full frame saved to captures/ for reference.")
        return True

    def _build_ui(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#0F172A")
        style.configure("TLabel", background="#0F172A", foreground="#F1F5F9", font=("Segoe UI", 10))
        style.configure("TCheckbutton", background="#0F172A", foreground="#F1F5F9", font=("Segoe UI", 10))
        style.configure("TButton", background="#334155", foreground="#F1F5F9", font=("Segoe UI", 10), padding=6)
        style.configure("Accent.TButton", background="#0EA5E9", foreground="#FFFFFF", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("TNotebook", background="#0A0E1A")
        style.configure("TNotebook.Tab", background="#11182B", foreground="#E6EDF3", padding=[12, 6])
        style.map("TNotebook.Tab", background=[("selected", "#1F2937")], foreground=[("selected", "#00E5FF")])

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.tab_strat = ttk.Frame(nb)
        self.tab_general = ttk.Frame(nb)
        self.tab_output = ttk.Frame(nb)
        nb.add(self.tab_strat, text="v4.7 Strategy & Board AI")
        nb.add(self.tab_general, text="Window & Safety")
        nb.add(self.tab_output, text="Live Output")

        self._build_strategy_tab()
        self._build_general_tab()
        self._build_output_tab()

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=10, pady=8)
        ttk.Button(bar, text="Save Config", command=self._save_from_ui).pack(side="left", padx=4)
        ttk.Button(bar, text="Calibrate ROIs", command=self._launch_calibrator).pack(side="left", padx=4)
        ttk.Button(bar, text="Toggle HUD", command=self.overlay.toggle).pack(side="left", padx=4)
        self.start_btn = ttk.Button(bar, text="Start Bot", style="Accent.TButton", command=self._start_thread)
        self.start_btn.pack(side="right", padx=4)
        ttk.Button(bar, text="Stop (F12)", command=self._stop_automation).pack(side="right", padx=4)

    def _build_strategy_tab(self):
        fr = self.tab_strat
        ttk.Label(fr, text="Active Season 2 Archetype:").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        strat_ids = [s["id"] for s in self.strategy_db.get("strategies", [])] or ["combo_aglaea"]
        self.strat_var = tk.StringVar(value=self.config.get("active_strategy_id", strat_ids[0]))
        cb = ttk.Combobox(fr, textvariable=self.strat_var, values=strat_ids, state="readonly", width=36)
        cb.grid(row=0, column=1, sticky="w", padx=8, pady=8)
        cb.bind("<<ComboboxSelected>>", lambda _: self._refresh_strategy_summary())
        self.summary_lbl = ttk.Label(fr, text="", foreground="#38BDF8", justify="left")
        self.summary_lbl.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        self._refresh_strategy_summary()

        toggles = [
            ("Enable Shop Auto-Buy (30g Interest Protection & 2-Refresh Pity)", ("automation", "enable_shop_auto_buy")),
            ("Enable Automated Shop XP Leveling", ("automation", "enable_auto_xp_leveling")),
            ("Enable Bench-to-Board Auto-Deployment & Filler Swap", ("automation", "enable_bench_deployment")),
            ("Enable Protagonist Plot Armor Auto-Selection", ("automation", "enable_protagonist_plot_armor")),
            ("Enable Board Unit Detection (HSV + Portrait + OCR)", ("automation", "enable_board_unit_detection")),
            ("Enable Equipment Drag-and-Drop Crafting & Multi-Carry Routing", ("automation", "enable_equipment_crafting")),
            ("Enable Escapade / Node Card Selector", ("automation", "enable_escapade_handler")),
            ("Enable Live Transparent Board Overlay HUD", ("automation", "enable_live_overlay")),
            ("Require Prep HUD before acting (recommended safety)", ("automation", "require_prep_ui")),
            ("Advance unknown screens with Next (risky)", ("automation", "advance_on_unknown")),
            ("Dry Run Mode (Log actions without moving mouse)", ("automation", "dry_run")),
        ]
        for idx, (label, path) in enumerate(toggles, start=2):
            v = tk.BooleanVar()
            ttk.Checkbutton(fr, text=label, variable=v).grid(row=idx, column=0, columnspan=2, sticky="w", padx=8, pady=3)
            self.vars[path] = v

        btn_row = ttk.Frame(fr)
        btn_row.grid(row=len(toggles) + 2, column=0, columnspan=2, sticky="w", padx=8, pady=12)
        ttk.Button(btn_row, text="Scan Board Now", command=self._test_board_scan).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Capture Portraits", command=self._capture_unit_portraits).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Diagnostics", command=self._run_self_test_from_ui).pack(side="left", padx=4)

    def _build_general_tab(self):
        fr = self.tab_general
        fields = [
            ("Game Window Title", ("window_title",)), ("Resolution Width", ("resolution", "width")),
            ("Resolution Height", ("resolution", "height")), ("Window Offset X", ("game_offset", "x")),
            ("Window Offset Y", ("game_offset", "y")), ("Delay Between Cycles (s)", ("automation", "delay_between_runs_seconds")),
            ("Pause Hotkey", ("safety", "pause_key")), ("Stop Hotkey", ("safety", "stop_key")),
        ]
        for r, (lbl, path) in enumerate(fields):
            ttk.Label(fr, text=lbl).grid(row=r, column=0, sticky="w", padx=8, pady=6)
            v = tk.StringVar()
            ttk.Entry(fr, textvariable=v, width=32).grid(row=r, column=1, sticky="w", padx=8, pady=6)
            self.vars[path] = v

    def _build_output_tab(self):
        self.output_text = tk.Text(self.tab_output, bg="#05080F", fg="#7EE787", font=("Consolas", 9), state="disabled")
        self.output_text.pack(fill="both", expand=True, padx=6, pady=6)

    def _refresh_strategy_summary(self):
        self.config["active_strategy_id"] = self.strat_var.get()
        for s in self.strategy_db.get("strategies", []):
            if s["id"] == self.strat_var.get():
                units = ", ".join(p["unit"] for p in s.get("unit_positioning", []))
                bonds = ", ".join(s.get("bonds", []))
                self.summary_lbl.config(text=f"Protagonist: {s.get('protagonist_slot_unit')}  |  Bonds: {bonds}\nCore Lineup: {units}")
                return

    def _load_into_ui(self):
        for path, var in self.vars.items():
            cur = self.config
            for k in path[:-1]:
                cur = cur.get(k, {})
            val = cur.get(path[-1], "")
            if isinstance(var, tk.BooleanVar):
                var.set(bool(val))
            else:
                var.set(str(val))

    def _save_from_ui(self):
        self.config["active_strategy_id"] = self.strat_var.get()
        for path, var in self.vars.items():
            cur = self.config
            for k in path[:-1]:
                cur = cur.setdefault(k, {})
            key = path[-1]
            if isinstance(var, tk.BooleanVar):
                cur[key] = var.get()
            else:
                cur[key] = coerce_number(var.get(), fallback=cur.get(key, var.get()))
        # Default to dry-run so saving can never accidentally switch to live.
        self.input_sim.dry_run = self.config.get("automation", {}).get("dry_run", True)
        _write_json_atomic(self.config_path, self.config)
        self.log("Saved configuration to config.json")

    def _run_self_test_from_ui(self):
        ok, lines = run_self_test(self, verbose=False)
        for line in lines:
            self.log(line)
        self.log(f"[Self-Test] {'PASS' if ok else 'FAIL - see lines above'}")

    def _launch_calibrator(self):
        cal = self.base_dir / "calibrate_currency_wars_1080p.py"
        if not cal.exists():
            messagebox.showinfo(
                "Calibrator",
                "calibrate_currency_wars_1080p.py was not found next to the app.",
            )
            return
        if getattr(sys, "frozen", False):
            # A frozen EXE cannot run a .py file; the user needs their Python.
            messagebox.showinfo(
                "Calibrator",
                "Run this from a terminal in the app folder:\n\n"
                f'  python "{cal.name}" --strategy "{self.strategy_path.name}"\n\n'
                "Then restart the app so the new ROIs are loaded.",
            )
            self.log(
                f"[Calibrate] Run: python {cal.name} --strategy {self.strategy_path.name} "
                "(restart the app afterwards)"
            )
            return
        subprocess.Popen([sys.executable, str(cal), "--strategy", str(self.strategy_path)])

    def _test_board_scan(self):
        frame = self.capture_game_frame()
        if frame is None:
            self.log("[Error] Frame capture failed (is the game running and visible?)")
            return
        scene, scene_conf = self.scene_detector.detect(frame)
        hud = self.prep_ctrl.read_hud(frame)
        self.prep_ctrl.last_telemetry["state"] = scene.upper() if scene else "UNKNOWN"
        board = self.prep_ctrl.detector.scan_board(frame)
        bench = self.prep_ctrl.detector.scan_bench(frame)
        inv = self.prep_ctrl.crafter.scan_inventory(frame)
        shop = self.prep_ctrl.detector.scan_shop_templates(frame)

        self.prep_ctrl.last_telemetry["board"] = board
        self.prep_ctrl.last_telemetry["bench"] = bench
        self.prep_ctrl.last_telemetry["inventory"] = inv

        self.log(f"[Test Scan] Scene: {scene or 'none'} ({scene_conf:.2f})")
        self.log(f"[Test Scan] Prep HUD detected: {self.prep_ctrl.hud_looks_like_prep(hud)}")
        self.log(f"[Test Scan] Board: {len(board)} units | Bench: {len(bench)} units | Items: {len(inv)}")
        for u in board:
            self.log(f"  Slot {u['slot_index']} ({u['zone']}): {u['unit']} {'★' * u['stars']} [{u['source']}:{u['confidence']}]")

        gold, level, hp = hud.get("gold"), hud.get("level"), hud.get("hp")
        self.log(f"  [HUD OCR] Gold: {gold} | Level: {level} | HP: {hp}")

        for u in shop:
            self.log(f"  Shop #{u['shop_index']}: {u['unit']} (Conf: {int(u['confidence']*100)}%)")

        if self.overlay:
            if not self.overlay.visible:
                self.overlay.show()
            self.overlay.render(self.prep_ctrl.last_telemetry, self.prep_ctrl.get_active_strategy())

        strategy = self.prep_ctrl.get_active_strategy()
        if strategy.get("skills"):
            self.log(f"  [Skills] Configured: {', '.join(s['name'] for s in strategy['skills'])}")

    def _capture_unit_portraits(self):
        frame = self.capture_game_frame()
        if frame is None:
            return
        out_dir = self.base_dir / "templates" / "units"
        out_dir.mkdir(parents=True, exist_ok=True)
        coords = self.strategy_db.get("screen_coordinates_1080p", {}).get("unit_board_slots", {})
        saved = 0
        for zone in ("on_field", "off_field"):
            for slot in coords.get(zone, []):
                roi = slot.get("ocr_roi")
                if not roi or len(roi) != 4:
                    continue
                x1, y1, x2, y2 = roi
                patch = frame[y1:y2, x1:x2]
                
                gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
                edges = cv2.Canny(gray, 50, 150)
                if np.count_nonzero(edges) >= int(self.config.get("vision", {}).get("min_edges", 900)):
                    fname = out_dir / f"slot{slot['slot_index']}_capture.png"
                    cv2.imwrite(str(fname), patch)
                    saved += 1
        self.log(f"[Capture] Saved {saved} occupied board portrait patches to {out_dir}. Rename them to <unit_slug>.png.")

    def _save_debug_frame(self):
        frame = self.capture_game_frame()
        if frame is None:
            self.log("[Error] Frame capture failed")
            return
        out_path = self.base_dir / "debug_frame.png"
        cv2.imwrite(str(out_path), frame)
        self.log(f"[Debug] Saved raw capture to {out_path}")
        self.log(f"[Debug] Frame size: {frame.shape[1]}x{frame.shape[0]}")

    def _save_debug_crops(self):
        frame = self.capture_game_frame()
        if frame is None:
            return
        out_dir = self.base_dir / "debug_crops"
        out_dir.mkdir(exist_ok=True)
        
        ui = self.strategy_db.get("screen_coordinates_1080p", {}).get("ui_controls", {})
        
        # Gold
        roi_g = ui.get("gold_counter_ocr_roi", [])
        if len(roi_g) == 4:
            x1, y1, x2, y2 = roi_g
            crop = frame[y1:y2, x1:x2]
            cv2.imwrite(str(out_dir / "raw_gold.png"), crop)
            
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
            _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
            cv2.imwrite(str(out_dir / "processed_gold.png"), thresh)

        # Level
        roi_l = ui.get("squad_level_ocr_roi", [])
        if len(roi_l) == 4:
            x1, y1, x2, y2 = roi_l
            crop = frame[y1:y2, x1:x2]
            cv2.imwrite(str(out_dir / "raw_level.png"), crop)
            
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
            _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
            cv2.imwrite(str(out_dir / "processed_level.png"), thresh)

        # Slot 0
        board_coords = self.strategy_db.get("screen_coordinates_1080p", {}).get("unit_board_slots", {}).get("on_field", [])
        if board_coords:
            roi_s = board_coords[0].get("ocr_roi", [])
            if len(roi_s) == 4:
                x1, y1, x2, y2 = roi_s
                crop = frame[y1:y2, x1:x2]
                cv2.imwrite(str(out_dir / "raw_slot0.png"), crop)
                
        self.log(f"[Debug] Saved debug crops to {out_dir}")

    def _start_thread(self):
        if self.running:
            return
        self._save_from_ui()
        self.running = True
        self.paused = False
        self.run_count = 0
        self._last_battle_click = 0.0
        self._battle_started_at = 0.0
        self._last_scene_action.clear()
        self.start_btn.config(state="disabled")
        if self.config.get("automation", {}).get("enable_live_overlay", True):
            self.overlay.show()
        threading.Thread(target=self._automation_loop, daemon=True).start()

    # -- state machine -----------------------------------------------------
    def _scene_click(self, scene: str, click_key: str, cooldown: float = 6.0) -> bool:
        """Click once per ``cooldown`` seconds for a given scene."""
        now = time.time()
        if now - self._last_scene_action.get(scene, 0.0) < cooldown:
            return False
        if not self.config.get("clicks", {}).get(click_key):
            self.log(f"[State] Scene '{scene}' matched but clicks.{click_key} is not configured")
            return False
        self._last_scene_action[scene] = now
        self.log(f"[State] {scene} -> clicking {click_key}")
        self.input_sim.click_named(click_key)
        return True

    def _handle_scene(self, scene: str, confidence: float) -> None:
        automation = self.config.get("automation", {})
        if scene == "battle_start":
            if not automation.get("enable_auto_battle", True):
                return
            if self._scene_click(scene, "start_battle", cooldown=8.0):
                self.run_count += 1
                self._battle_started_at = time.time()
                time.sleep(3.0)
        elif scene == "victory":
            if self._scene_click(scene, "next", cooldown=5.0):
                self._battle_started_at = 0.0
                time.sleep(1.0)
        elif scene == "reward":
            if self._scene_click(scene, "collect", cooldown=5.0):
                time.sleep(1.0)
        else:
            if time.time() - self._last_unknown_log > 12.0:
                self._last_unknown_log = time.time()
                self.log(f"[State] Unhandled scene '{scene}' ({confidence:.2f}) - no click mapping")

    def _automation_loop(self):
        automation = self.config.get("automation", {})
        delay = float(automation.get("delay_between_runs_seconds", 2.5))
        max_runs = int(automation.get("max_runs", 0) or 0)
        max_seconds = float(self._run_deadline_seconds or 0.0)
        started = time.time()
        require_prep = bool(automation.get("require_prep_ui", True))
        advance_unknown = bool(automation.get("advance_on_unknown", False))
        battle_timeout = float(automation.get("battle_timeout_seconds", 180) or 0)
        wait_for_window = float(automation.get("wait_for_window_seconds", 0) or 0)
        # Default to dry-run so a misconfigured config can never silently go live.
        dry_run = bool(automation.get("dry_run", True))

        self.log("=== Currency Wars v4.7 Automation Loop Started ===")
        self.log(
            f"[Loop] dry_run={dry_run} max_runs={max_runs or 'unlimited'} "
            f"require_prep_ui={require_prep} scenes={sorted(self.scene_detector.available)}"
        )
        no_window_since = 0.0
        waiting_logged = False
        try:
            while self.running:
                if self.paused:
                    time.sleep(0.4)
                    continue
                if max_seconds and time.time() - started >= max_seconds:
                    self.log(f"[Loop] Reached --max-seconds={max_seconds:g}; stopping")
                    break

                frame = self.capture_game_frame()
                if frame is None:
                    now = time.time()
                    if not no_window_since:
                        no_window_since = now
                    if not waiting_logged:
                        waiting_logged = True
                        self.log(
                            f"[Loop] Waiting for the game window '{self.config.get('window_title')}'. "
                            "The loop keeps retrying; press F12 to stop."
                        )
                    if wait_for_window and now - no_window_since > wait_for_window:
                        self.log(f"[Loop] Game window did not appear within {wait_for_window:g}s; stopping")
                        break
                    time.sleep(2.0)
                    continue
                no_window_since = 0.0
                waiting_logged = False

                scene, confidence = self.scene_detector.detect(frame)
                telemetry = self.prep_ctrl.last_telemetry
                telemetry["state"] = scene.upper() if scene else "UNKNOWN"

                if scene:
                    self._handle_scene(scene, confidence)
                    if max_runs and self.run_count >= max_runs:
                        self.log(f"[Loop] Reached max_runs={max_runs}; stopping")
                        break
                else:
                    hud = self.prep_ctrl.read_hud(frame)
                    if require_prep and not self.prep_ctrl.hud_looks_like_prep(hud):
                        if advance_unknown and time.time() - self._last_advance > 8.0:
                            self._last_advance = time.time()
                            if self._scene_click("unknown_advance", "next", cooldown=8.0):
                                telemetry["state"] = "ADVANCE"
                        elif time.time() - self._last_unknown_log > 15.0:
                            self._last_unknown_log = time.time()
                            self.log(
                                f"[State] Not in prep (HUD unreadable: {hud}) and no scene template matched. "
                                "Waiting instead of clicking blindly."
                            )
                            if not self._warned_hud_rois:
                                self._warned_hud_rois = True
                                self.log(
                                    "[Calibrate] If you are on the Preparation screen, the HUD ROIs do not "
                                    "match your layout. Run: python calibrate_currency_wars_1080p.py "
                                    "--strategy " + self.strategy_path.name + " (then restart the app)."
                                )
                    else:
                        telemetry = self.prep_ctrl.run_prep_cycle(frame, self.log, hud=hud)
                        telemetry["state"] = "PREP"
                        self._last_unknown_log = 0.0

                        if self._battle_started_at and battle_timeout:
                            if time.time() - self._battle_started_at > battle_timeout:
                                msg = f"[Loop] Battle timeout ({battle_timeout:g}s) exceeded; stopping for safety"
                                self.log(msg)
                                if self.config.get("safety", {}).get("emergency_stop_on_error", True):
                                    break

                if self.overlay is not None and not self.headless:
                    self.overlay.request_render(telemetry, self.prep_ctrl.get_active_strategy())

                time.sleep(max(0.5, delay))
        except Exception as exc:
            self.log(f"[Error] Loop exception: {exc}")
            import traceback
            for line in traceback.format_exc().splitlines()[-4:]:
                self.log(f"[Error] {line}")
        finally:
            self.running = False
            self._battle_started_at = 0.0
            if not self.headless:
                self._post_ui(lambda: self.start_btn.config(state="normal"))
            self.log("=== Automation Loop Stopped ===")

    def _start_ui_tick(self):
        def _tick():
            try:
                while True:
                    try:
                        fn = self._ui_queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        fn()
                    except Exception as exc:
                        LOG.warning("UI command failed: %s", exc)

                lines = []
                for _ in range(80):
                    try:
                        lines.append(self._log_queue.get_nowait())
                    except queue.Empty:
                        break
                if lines:
                    self.output_text.config(state="normal")
                    self.output_text.insert("end", "\n".join(lines) + "\n")
                    self.output_text.see("end")
                    self.output_text.config(state="disabled")

                if self.overlay:
                    self.overlay.drain_pending()
            finally:
                self.root.after(50, _tick)
        self.root.after(50, _tick)


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write ``data`` as JSON so a crash cannot leave a truncated config."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def run_self_test(app: "CurrencyWarsApp", verbose: bool = False) -> tuple[bool, list[str]]:
    """Check that this machine/folder can actually run the bot.

    Returns ``(ok, lines)``. ``ok`` is False only for problems that will stop
    the bot from working (missing libraries, unusable JSON, missing scene
    templates). Softer problems are reported but do not fail the check.
    """
    lines: list[str] = []
    errors = 0
    warnings = 0

    def ok_line(msg: str) -> None:
        lines.append(f"[ OK ] {msg}")

    def warn(msg: str) -> None:
        nonlocal warnings
        warnings += 1
        lines.append(f"[WARN] {msg}")

    def error(msg: str) -> None:
        nonlocal errors
        errors += 1
        lines.append(f"[FAIL] {msg}")

    lines.append("== Environment ==")
    import platform
    lines.append(f"Python {platform.python_version()} on {platform.platform()}")
    try:
        cv2_version = cv2.__version__
    except Exception:
        cv2_version = "missing"
    ok_line(f"OpenCV {cv2_version}, NumPy {np.__version__}")

    if HAS_TESSERACT:
        try:
            version = pytesseract.get_tesseract_version()
            ok_line(f"Tesseract {version}")
        except Exception as exc:
            error(f"pytesseract installed but the Tesseract binary is not usable: {exc}")
    else:
        error("pytesseract is not installed; shop/HUD OCR will not work")

    if HAS_WGC:
        ok_line("WGC window capture available")
    else:
        error("wgc_python is not installed; background capture will not work")

    ok_line("Administrator privileges" if is_admin() else "Not elevated (hotkeys/PostMessage may fail)")

    lines.append("== Configuration ==")
    for label, path in (("config.json", app.config_path), ("strategy JSON", app.strategy_path)):
        if path.exists():
            ok_line(f"{label} present: {path.name}")
        else:
            error(f"{label} missing: {path}")

    strategies = app.strategy_db.get("strategies", [])
    if strategies:
        ok_line(f"Strategy file loaded: {len(strategies)} strategies")
    else:
        error("Strategy JSON has no 'strategies' entries - nothing to run")

    active = app.prep_ctrl.get_active_strategy()
    if active.get("id") == "none":
        warn("active_strategy_id does not match any strategy; falling back to a stub")
    else:
        ok_line(f"Active strategy: {active.get('id')} ({active.get('name', '?')})")

    coords = app.strategy_db.get("screen_coordinates_1080p", {})
    if not coords:
        error("Strategy JSON has no screen_coordinates_1080p block")
    else:
        ok_line(
            f"ROIs: {len(coords.get('unit_board_slots', {}).get('on_field', []))} field slots, "
            f"{len(coords.get('shop_card_slots', []))} shop cards"
        )

    automation = app.config.get("automation", {})
    ok_line(
        "Actions: "
        + ("DRY-RUN (no clicks sent)" if automation.get("dry_run", True) else "LIVE")
        + f", require_prep_ui={bool(automation.get('require_prep_ui', True))}"
    )
    if not automation.get("require_prep_ui", True):
        warn("require_prep_ui is off: the bot may click buy/reroll coordinates on non-prep screens")

    lines.append("== Scenes ==")
    loaded = sorted(app.scene_detector.available)
    if app.scene_detector.missing:
        error("Missing scene templates: " + ", ".join(app.scene_detector.missing))
        for scene in app.scene_detector.missing:
            lines.append(
                f"       capture with: python currency_wars_app-v9.py "
                f"--capture-scene {scene} --scene-roi x1,y1,x2,y2"
            )
    if loaded:
        ok_line("Scene templates loaded: " + ", ".join(loaded))

    lines.append("== Units ==")
    unit_templates = app.recognizer.list_templates("units")
    ok_line(f"{len(unit_templates)} unit template file(s) in templates/units")
    if not unit_templates:
        error("templates/units is empty; board/shop detection cannot identify anything")

    strategy_units: set[str] = set()
    for strat in strategies:
        if strat.get("protagonist_slot_unit"):
            strategy_units.add(strat["protagonist_slot_unit"])
        for pos in strat.get("unit_positioning", []):
            if pos.get("unit"):
                strategy_units.add(pos["unit"])
    unresolved = app.unit_registry.unresolved(sorted(strategy_units))
    if unresolved:
        warn("Strategy units without a matching template: " + ", ".join(unresolved))
    else:
        ok_line("All strategy units resolve to a template or alias")

    lines.append("== Items / Crafting ==")
    item_templates = app.recognizer.list_templates("items")
    verified = app.item_registry.verified
    if verified:
        ok_line(f"{len(item_templates)} item template(s), {len(verified)} verified for crafting")
    elif item_templates:
        warn(
            f"{len(item_templates)} item template(s) present but items.verified is empty: "
            "crafting/equipping is disabled until you confirm real icons"
        )
    else:
        warn("No item templates; crafting/equipping is disabled")

    recipes = app.strategy_db.get("crafting_recipes", [])
    recipe_names = {c for r in recipes for c in r.get("components", [])}
    recipe_names |= {r.get("name", "") for r in recipes if r.get("name")}
    unmapped = app.item_registry.unresolved(sorted(recipe_names))
    if unmapped:
        warn("Recipe items with no template mapping: " + ", ".join(unmapped))

    lines.append("== Game window ==")
    if app._detect_game_window():
        ok_line(f"Found '{app.config.get('window_title')}' (HWND {app.input_sim.hwnd})")
    else:
        warn(f"Window '{app.config.get('window_title')}' not found right now (start the game to verify capture)")

    if verbose:
        lines.append("== Preloaded templates ==")
        lines.append(f"{app.recognizer.preload(('units', 'items'))} template images readable")

    lines.append("")
    verdict = "PASS" if errors == 0 else f"FAIL ({errors} error(s), {warnings} warning(s))"
    lines.append(f"Result: {verdict}")
    return errors == 0, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HSR Currency Wars v4.7 Automation")
    parser.add_argument("--headless", action="store_true", help="run without the GUI")
    parser.add_argument("--dry-run", action="store_true", help="log actions without sending clicks")
    parser.add_argument("--self-test", action="store_true", help="validate environment, config and templates")
    parser.add_argument("--capture-scene", metavar="NAME", help="save templates/scenes/NAME.png from the game")
    parser.add_argument("--scene-roi", metavar="x1,y1,x2,y2", help="crop used with --capture-scene")
    parser.add_argument("--max-seconds", type=float, default=0.0, help="stop the loop after N seconds (0 = unlimited)")
    args = parser.parse_args(argv)

    if args.scene_roi and not args.capture_scene:
        parser.error("--scene-roi requires --capture-scene")

    if args.self_test:
        app = CurrencyWarsApp(None, headless=True)
        try:
            ok, lines = run_self_test(app, verbose=True)
        finally:
            app._close_capture()
        for line in lines:
            print(line)
        report = _write_crash_log("\n".join(lines), name="self_test_report.txt")
        if WINDOWED_BUILD:
            try:
                messagebox.showinfo(
                    "Currency Wars - validate setup",
                    lines[-1] + (f"\n\nFull report: {report}" if report else ""),
                )
            except Exception:
                pass
        return 0 if ok else 1

    if args.capture_scene:
        app = CurrencyWarsApp(None, headless=True)
        try:
            roi = parse_roi(args.scene_roi) if args.scene_roi else None
            if args.scene_roi and roi is None:
                parser.error("--scene-roi must be four integers: x1,y1,x2,y2")
            ok = app.capture_scene(args.capture_scene, roi)
        finally:
            app._close_capture()
        if WINDOWED_BUILD:
            try:
                messagebox.showinfo(
                    "Currency Wars - scene capture",
                    "Saved." if ok else "Capture failed. See automation.log.",
                )
            except Exception:
                pass
        return 0 if ok else 1

    def apply_cli_overrides(app: "CurrencyWarsApp") -> None:
        if args.dry_run:
            app.config.setdefault("automation", {})["dry_run"] = True
            app.input_sim.dry_run = True
            app.log("Dry-run mode forced via CLI (--dry-run)")
        if args.max_seconds:
            app._run_deadline_seconds = args.max_seconds
            app.log(f"Loop budget set to {args.max_seconds:g}s")

    if args.headless:
        app = CurrencyWarsApp(None, headless=True)
        apply_cli_overrides(app)
        try:
            app.running = True
            app._automation_loop()
        except KeyboardInterrupt:
            app.log("[Loop] Interrupted (Ctrl+C)")
        finally:
            app.running = False
            app._close_capture()
        return 0

    root = tk.Tk()
    app = CurrencyWarsApp(root)
    apply_cli_overrides(app)
    try:
        root.mainloop()
    finally:
        app.running = False
        app._close_capture()
    return 0


if __name__ == "__main__":
    WINDOWED_BUILD = _ensure_streams()
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        import traceback
        text = traceback.format_exc()
        path = _write_crash_log(text)
        if WINDOWED_BUILD:
            try:
                messagebox.showerror(
                    "Currency Wars crashed",
                    f"{text[-1200:]}\n\nFull traceback: {path}",
                )
            except Exception:
                pass
        raise