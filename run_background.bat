@echo off
:: ------------------------------------------------------------
:: Background runner for HSR Currency Wars Automation
:: ------------------------------------------------------------
:: Starts the bot without the Tk window. The automation loop
:: itself waits for the game window, so this script does not
:: need to relaunch in a loop. It only restarts if the process
:: exits with an error.
::
:: Requirements:
::   - Run from an Administrator command prompt (global hotkeys
::     and PostMessage clicks need it)
::   - Python 3.9+ with the packages from requirements.txt
::   - The game running with the window title set in config.json
::
:: Usage:
::   run_background.bat                        -> uses config.json
::   run_background.bat --dry-run              -> force dry-run
::   run_background.bat --self-test            -> validate setup
::   run_background.bat --max-seconds 600      -> stop after 10 min
:: ------------------------------------------------------------

setlocal
cd /d "%~dp0"

:run
echo [%date% %time%] Starting automation: --headless %*
python "%~dp0currency_wars_app-v9.py" --headless %*
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" goto done

echo [%date% %time%] Exited with error %RC%; retrying in 10 seconds. Press Ctrl+C to stop.
timeout /t 10 /nobreak >nul
goto run

:done
echo [%date% %time%] Automation finished.
exit /b 0
