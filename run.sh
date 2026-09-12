#!/usr/bin/env bash
# TradingAgents-webGUI launcher (POSIX). Package layout: server/ + webui/.
# Usage: OPENROUTER_API_KEY=sk-or-... ./run.sh  →  http://127.0.0.1:8000
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
SERVER_DIR="$SCRIPT_DIR/server"

# 1. Virtualenv: create once, reuse afterwards.
if [ ! -x "$VENV_DIR/bin/python" ]; then
    PY="${PYTHON:-python3}"
    if ! command -v "$PY" >/dev/null 2>&1; then
        echo "error: '$PY' not found; install Python 3.11+ (or run with PYTHON=/path/to/python ./run.sh)" >&2
        exit 1
    fi
    "$PY" -m venv "$VENV_DIR"
fi

# 2. Dependencies: install once (marker file records completion).
if [ ! -f "$VENV_DIR/.deps-installed" ]; then
    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    "$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.lock"
    touch "$VENV_DIR/.deps-installed"
fi

# 3. Environment: static SPA hosting for the bundled webui (single process).
export TA_WEBGUI_STATIC_DIR="$SCRIPT_DIR/webui"

# 4. Start the API + SPA server from the server/ directory.
cd "$SERVER_DIR"
exec "$VENV_DIR/bin/python" -m uvicorn app.api.app:create_app --factory \
    --host 127.0.0.1 --port 8000
