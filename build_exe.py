#!/usr/bin/env python3
"""
HSR Currency Wars v4.7 Automation - Standalone Executable Build Script (PyInstaller)

Usage:
  python build_exe.py                  # Build one-file windowed EXE + portable dist folder
  python build_exe.py --onedir         # Build fast-startup folder distribution
  python build_exe.py --console        # Keep console visible for debugging
  python build_exe.py --install-deps   # Install PyInstaller & required packages first
  python build_exe.py --no-collect-cv2 # Skip --collect-all cv2 (smaller EXE, may miss DLLs)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ENTRY_SCRIPT_CANDIDATES = [
    "currency_wars_app-v9.py",
    "currency_wars_app-v8.py",
    "currency_wars_app.py",
]

STRATEGY_JSON_CANDIDATES = [
    "hsr_currency_wars_v4_7_strategies-v4.json",
    "hsr_currency_wars_v4_7_strategies-v3.json",
]

COMPANION_SCRIPTS = [
    "calibrate_currency_wars_1080p.py",
    "crop_equipment_icons.py",
    "capture_templates.py",
]

REQUIRED_PACKAGES = [
    "pyinstaller",
    "opencv-python",
    "numpy",
    "pywin32",
    "keyboard",
    "pytesseract",
    "wgc-python",
]

HIDDEN_IMPORTS = [
    "cv2", "numpy",
    "keyboard", "win32api", "win32con", "win32gui",
    "wgc_python", "cw_core",
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
    "pytesseract", "ctypes", "difflib",
]


def resolve_first_existing(base_dir: Path, candidates: list[str]) -> Path | None:
    for name in candidates:
        p = base_dir / name
        if p.exists():
            return p
    return None


def install_dependencies() -> None:
    print("[1/4] Installing / upgrading build & runtime dependencies...")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *REQUIRED_PACKAGES]
    subprocess.run(cmd, check=True)


def verify_pyinstaller() -> None:
    import importlib.util

    if importlib.util.find_spec("PyInstaller") is None:
        print("[Info] PyInstaller not found. Installing automatically...")
        install_dependencies()


def ensure_runtime_assets(base_dir: Path) -> tuple[Path, Path | None]:
    entry = resolve_first_existing(base_dir, ENTRY_SCRIPT_CANDIDATES)
    if entry is None:
        raise FileNotFoundError(
            f"Could not find entry script in {base_dir}. "
            f"Expected one of: {', '.join(ENTRY_SCRIPT_CANDIDATES)}"
        )

    strategy_json = resolve_first_existing(base_dir, STRATEGY_JSON_CANDIDATES)
    if strategy_json is None:
        print(
            "[Warning] Strategy JSON not found in current directory. "
            "Make sure to place hsr_currency_wars_v4_7_strategies-v4.json next to the EXE."
        )

    # Seed template folders with READMEs so end users know what goes where
    for sub, hint in (
        ("items", "Place item icon crops here as <item_slug>.png (e.g. anti_grav_boots.png)."),
        ("units", "Place unit portrait crops here as <unit_slug>.png (e.g. aglaea.png)."),
        (
            "scenes",
            "Capture scene templates with:\n"
            "  python currency_wars_app-v9.py --capture-scene victory --scene-roi x1,y1,x2,y2\n"
            "See the README in this folder.",
        ),
    ):
        d = base_dir / "templates" / sub
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.txt"
        if not readme.exists():
            readme.write_text(hint + "\n", encoding="utf-8")

    return entry, strategy_json


def clean_build_artifacts(base_dir: Path) -> None:
    for folder in ("build", "__pycache__"):
        target = base_dir / folder
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    for spec_file in base_dir.glob("*.spec"):
        spec_file.unlink(missing_ok=True)


def copy_external_runtime_files(base_dir: Path, dist_dir: Path, strategy_json: Path | None) -> None:
    dist_dir.mkdir(parents=True, exist_ok=True)

    files_to_copy: list[Path] = []
    cfg = base_dir / "config.json"
    if cfg.exists():
        files_to_copy.append(cfg)
    registry = base_dir / "unit_registry.json"
    if registry.exists():
        files_to_copy.append(registry)
    # The calibrator is a plain script run by the user's Python, so it needs the
    # pure-logic module it imports next to it.
    core = base_dir / "cw_core.py"
    if core.exists():
        files_to_copy.append(core)
    if strategy_json is not None and strategy_json.exists():
        files_to_copy.append(strategy_json)
    for helper in COMPANION_SCRIPTS:
        hp = base_dir / helper
        if hp.exists():
            files_to_copy.append(hp)

    for src in files_to_copy:
        dst = dist_dir / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
            print(f"  [Copied] {src.name} -> {dist_dir}")

    src_templates = base_dir / "templates"
    dst_templates = dist_dir / "templates"
    if src_templates.exists() and src_templates.resolve() != dst_templates.resolve():
        shutil.copytree(src_templates, dst_templates, dirs_exist_ok=True)
        print(f"  [Copied] templates/ -> {dst_templates}")

    bat_path = dist_dir / "Launch_CurrencyWars_Admin.bat"
    exe_name = "CurrencyWarsAutomation.exe" if os.name == "nt" else "CurrencyWarsAutomation"
    bat_content = (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo Starting HSR Currency Wars v4.7 Automation...\r\n"
        "echo (Running elevated so global F10/F12 hotkeys reach the game)\r\n"
        f"start \"\" \"{exe_name}\"\r\n"
    )
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"  [Created] {bat_path.name}")

    cal_bat = dist_dir / "Calibrate_HUD.bat"
    cal_bat.write_text(
        "@echo off\r\n"
        "rem Opens the HUD ROI calibrator. Drag a box over each element, then \"Save all\".\r\n"
        "cd /d \"%~dp0\"\r\n"
        "python calibrate_currency_wars_1080p.py --strategy "
        "hsr_currency_wars_v4_7_strategies-v4.json\r\n"
        "if errorlevel 1 pause\r\n",
        encoding="utf-8",
    )
    print(f"  [Created] {cal_bat.name}")


def verify_bundle(base_dir: Path, check_cv2: bool = True) -> None:
    """Fail the build if required native libraries did not make it in.

    PyInstaller happily produces an EXE that dies on ``import wgc_python`` when
    only the Python wrapper was collected. Catch that here instead of shipping
    an EXE that shows a "Failed to execute script" dialog.
    """
    toc_dir = base_dir / "build"
    tocs = sorted(toc_dir.glob("*/*.toc")) if toc_dir.exists() else []
    blob = ""
    for toc in tocs:
        try:
            blob += toc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    dll_dir = base_dir / "dist" / "CurrencyWarsAutomation" / "_internal"
    required = {
        "wgc_python.dll": "wgc_python background capture engine",
    }
    if check_cv2:
        required["cv2.pyd"] = "OpenCV"
    missing = []
    for needle, label in required.items():
        found = needle in blob or (dll_dir.exists() and any(dll_dir.rglob(needle)))
        print(f"  [Check] {label}: {'bundled' if found else 'MISSING (' + needle + ')'}")
        if not found:
            missing.append(label)
    if missing:
        raise RuntimeError(
            "The build is incomplete; missing from the bundle: "
            + ", ".join(missing)
            + ". Re-run after `pip install --upgrade wgc-python opencv-python`."
        )


def build_executable(
    base_dir: Path,
    entry_script: Path,
    strategy_json: Path | None,
    onedir: bool = False,
    console: bool = False,
    icon_path: str | None = None,
    collect_cv2: bool = True,
    clean: bool = True,
) -> Path:
    sep = ";" if os.name == "nt" else ":"
    app_name = "CurrencyWarsAutomation"

    if clean:
        print("[2/4] Cleaning previous PyInstaller build artifacts...")
        clean_build_artifacts(base_dir)

    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", app_name,
        "--onedir" if onedir else "--onefile",
        "--console" if console else "--windowed",
        "--noupx",
    ]

    if os.name == "nt":
        pyinstaller_args.append("--uac-admin")

    if icon_path and Path(icon_path).exists():
        pyinstaller_args.extend(["--icon", str(Path(icon_path).resolve())])

    if (base_dir / "config.json").exists():
        pyinstaller_args.extend(["--add-data", f"{base_dir / 'config.json'}{sep}."])
    if (base_dir / "unit_registry.json").exists():
        pyinstaller_args.extend(["--add-data", f"{base_dir / 'unit_registry.json'}{sep}."])
    if strategy_json is not None and strategy_json.exists():
        pyinstaller_args.extend(["--add-data", f"{strategy_json}{sep}."])
    if (base_dir / "templates").exists():
        pyinstaller_args.extend(["--add-data", f"{base_dir / 'templates'}{sep}templates"])

    for mod in HIDDEN_IMPORTS:
        pyinstaller_args.extend(["--hidden-import", mod])

    if collect_cv2:
        # Safe default: bundles cv2's native DLLs. Use --no-collect-cv2 to shrink.
        pyinstaller_args.extend(["--collect-all", "cv2"])

    # wgc_python is a ctypes wrapper around wgc_python/wgc_python.dll. A plain
    # --hidden-import only copies __init__.py, so the EXE died at import time
    # with "Failed to load dynlib/dll ... wgc_python.dll". Collect the whole
    # package so the DLL travels with the bundle.
    pyinstaller_args.extend(["--collect-all", "wgc_python"])

    pyinstaller_args.append(str(entry_script))

    print(f"[3/4] Building {app_name} from {entry_script.name}...")
    subprocess.run(pyinstaller_args, cwd=str(base_dir), check=True)

    verify_bundle(base_dir, check_cv2=collect_cv2)

    dist_output_dir = (base_dir / "dist" / app_name) if onedir else (base_dir / "dist")
    print("[4/4] Staging external configs, templates, and launcher beside executable...")
    copy_external_runtime_files(base_dir, dist_output_dir, strategy_json)

    exe_file = dist_output_dir / ("CurrencyWarsAutomation.exe" if os.name == "nt" else "CurrencyWarsAutomation")
    return exe_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build standalone executable for HSR Currency Wars v4.7 Automation"
    )
    parser.add_argument("--entry", default=None, help="Explicit path to entry script")
    parser.add_argument("--onedir", action="store_true", help="Build directory bundle instead of one-file EXE")
    parser.add_argument("--console", action="store_true", help="Keep console window visible for debugging")
    parser.add_argument("--icon", default=None, help="Optional path to a .ico file")
    parser.add_argument("--install-deps", action="store_true", help="Install deps first")
    parser.add_argument("--no-clean", action="store_true", help="Skip deleting build/ and .spec files")
    parser.add_argument("--no-collect-cv2", action="store_true", help="Skip --collect-all cv2 (smaller, may fail)")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent

    if args.install_deps:
        install_dependencies()
    else:
        verify_pyinstaller()

    if args.entry:
        entry_script = Path(args.entry).resolve()
        if not entry_script.exists():
            print(f"[Error] Entry script not found: {entry_script}", file=sys.stderr)
            return 1
        strategy_json = resolve_first_existing(base_dir, STRATEGY_JSON_CANDIDATES)
    else:
        entry_script, strategy_json = ensure_runtime_assets(base_dir)

    try:
        exe_path = build_executable(
            base_dir=base_dir,
            entry_script=entry_script,
            strategy_json=strategy_json,
            onedir=args.onedir,
            console=args.console,
            icon_path=args.icon,
            collect_cv2=not args.no_collect_cv2,
            clean=not args.no_clean,
        )
    except RuntimeError as exc:
        print(f"\n[Build failed] {exc}", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print("BUILD COMPLETE!")
    print(f"Executable:  {exe_path}")
    print(f"Dist Folder: {exe_path.parent}")
    print("Keep config.json, unit_registry.json, hsr_currency_wars_v4_7_strategies-v4.json, and")
    print("templates/ in the same folder as the EXE so GUI edits persist.")
    print("Launch via Launch_CurrencyWars_Admin.bat for hotkey access.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())