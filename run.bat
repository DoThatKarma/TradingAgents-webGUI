@echo off
rem TradingAgents-webGUI launcher (Windows cmd). Layout: server\ + webui\.
rem Usage: set OPENROUTER_API_KEY=sk-or-... && run.bat  →  http://127.0.0.1:8000
setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "SERVER_DIR=%SCRIPT_DIR%server"

rem 1. Virtualenv: create once, reuse afterwards.
if exist "%VENV_DIR%\Scripts\python.exe" goto deps
where python >nul 2>nul
if errorlevel 1 (
    echo error: 'python' not found; install Python 3.11+ from https://www.python.org/downloads/ 1>&2
    exit /b 1
)
python -m venv "%VENV_DIR%"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo error: could not create virtual environment at %VENV_DIR% 1>&2
    exit /b 1
)

:deps
rem 2. Dependencies: install once (marker file records completion).
if exist "%VENV_DIR%\.deps-installed" goto env
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo error: pip upgrade failed 1>&2
    exit /b 1
)
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%SCRIPT_DIR%requirements.lock"
if errorlevel 1 (
    echo error: dependency install failed; fix the error above and re-run run.bat 1>&2
    exit /b 1
)
type nul > "%VENV_DIR%\.deps-installed"

:env
rem 3. Environment: static SPA hosting for the bundled webui (single process).
set "TA_WEBGUI_STATIC_DIR=%SCRIPT_DIR%webui"

rem 4. Durable run persistence (ADR 0008): finished runs survive restarts.
rem    Override by setting TA_WEBGUI_PERSIST_DIR before launching.
if not defined TA_WEBGUI_PERSIST_DIR set "TA_WEBGUI_PERSIST_DIR=%SCRIPT_DIR%data\runs"
if not exist "%TA_WEBGUI_PERSIST_DIR%" mkdir "%TA_WEBGUI_PERSIST_DIR%"

rem 5. Start the API + SPA server from the server\ directory.
cd /d "%SERVER_DIR%"
"%VENV_DIR%\Scripts\python.exe" -m uvicorn app.api.app:create_app --factory --host 127.0.0.1 --port 8000
