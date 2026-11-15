@echo off
rem Opens the HUD ROI calibrator. Drag a box over each element, then "Save all".
cd /d "%~dp0"
python calibrate_currency_wars_1080p.py --strategy hsr_currency_wars_v4_7_strategies-v4.json
if errorlevel 1 pause
