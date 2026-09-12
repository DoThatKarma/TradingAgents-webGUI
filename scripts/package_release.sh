#!/usr/bin/env bash
# Build the downloadable user release: tradingagents-webgui-<version>.zip
#
# Runnable from the repo root: scripts/package_release.sh
# Output: dist-release/tradingagents-webgui-<version>.zip (gitignored)
#
# Zip layout (top-level folder inside the zip):
#   tradingagents-webgui/
#     server/            backend package (app/ + pyproject.toml)
#     webui/             built frontend (renamed from frontend/dist)
#     run.sh, run.bat    one-command launchers
#     README.md          user quickstart (generated from USERGUIDE.md)
#     requirements.lock  install requirements (pinned upstream commit)
#     LICENSE
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OUT_DIR="$REPO_ROOT/dist-release"
STAGE_ROOT="$OUT_DIR/stage"
PKG_DIR="$STAGE_ROOT/tradingagents-webgui"

# The release ships a tokenless frontend build: strip token/key variables so
# VITE_API_TOKEN and friends can never leak into the packaged assets.
unset VITE_API_TOKEN TA_WEBGUI_API_TOKEN OPENROUTER_API_KEY || true

# ---------------------------------------------------------------- pinned sha
# The fork of upstream TradingAgents (provides the `tradingagents` package) is
# pinned to an exact commit for reproducible user installs. Resolution order:
# 1. TRADINGAGENTS_PIN_SHA env override
# 2. HEAD of a local checkout of the fork (sibling dir or agent workspace)
# 3. The known-good embedded sha (must match a pushed commit on the fork)
DEFAULT_PIN_SHA="265a4546280e1b1758c13ba7e2b06b07c6d3dcc3"
PIN_SHA="${TRADINGAGENTS_PIN_SHA:-}"
if [ -z "$PIN_SHA" ]; then
    for candidate in "$REPO_ROOT/../TradingAgentsPlugin" /a0/usr/projects/tradingagentsplugin; do
        if git -C "$candidate" rev-parse HEAD >/dev/null 2>&1; then
            PIN_SHA="$(git -C "$candidate" rev-parse HEAD)"
            echo "==> Using fork HEAD as pin: $PIN_SHA (from $candidate)"
            break
        fi
    done
fi
if [ -z "$PIN_SHA" ]; then
    PIN_SHA="$DEFAULT_PIN_SHA"
    echo "==> No local fork checkout found; using embedded pin: $PIN_SHA"
fi
if [ "$PIN_SHA" != "$DEFAULT_PIN_SHA" ]; then
    echo "    NOTE: pin differs from the embedded default $DEFAULT_PIN_SHA"
fi
if [[ ! "$PIN_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "error: pin sha must be a full 40-char hex commit sha (got: $PIN_SHA)" 1>&2
    exit 1
fi

# ------------------------------------------------------------------- version
VERSION="$(sed -n 's/^version = "\([^"]*\)"$/\1/p' server/pyproject.toml | head -n1)"
if [ -z "$VERSION" ]; then
    echo "error: could not read version from server/pyproject.toml" >&2
    exit 1
fi
ZIP_PATH="$OUT_DIR/tradingagents-webgui-$VERSION.zip"
echo "==> Packaging version $VERSION"

# --------------------------------------------------------- build the frontend
echo "==> Building frontend"
(
    cd frontend
    if [ -f package-lock.json ]; then
# .env files on disk would flow into the Vite bundle - refuse to package them.
if compgen -G "frontend/.env*" > /dev/null; then
    echo "error: frontend/.env* files exist; remove them before packaging (they may leak into the build)" 1>&2
    exit 1
fi
        npm ci || npm install
    else
        npm install
    fi
    npm run build
)
if [ ! -f frontend/dist/index.html ]; then
    echo "error: frontend build did not produce frontend/dist/index.html" >&2
    exit 1
fi

# ------------------------------------------------------------ assemble stage
# Idempotent: always rebuild the staging tree from scratch.
echo "==> Staging release tree"
rm -rf "$STAGE_ROOT"
mkdir -p "$PKG_DIR/server"

cp -R server/app "$PKG_DIR/server/app"
cp server/pyproject.toml "$PKG_DIR/server/pyproject.toml"
cp -R frontend/dist "$PKG_DIR/webui"
cp LICENSE "$PKG_DIR/LICENSE"
cp USERGUIDE.md "$PKG_DIR/README.md"
cp run.sh "$PKG_DIR/run.sh"
cp run.bat "$PKG_DIR/run.bat"

# Runtime requirements only: server deps from server/pyproject.toml plus the
# pinned upstream fork (declares itself as the `tradingagents` package).
# httpx is deliberately excluded (test-only dependency).
cat > "$PKG_DIR/requirements.lock" <<EOF
# TradingAgents-webGUI user release requirements (Python 3.11+)
# Upstream framework pinned to an exact commit for reproducible installs.
tradingagents @ git+https://github.com/DoThatKarma/TradingAgentsPlugin.git@$PIN_SHA
fastapi>=0.115
uvicorn[standard]>=0.30
sse-starlette>=2.1
pydantic>=2.7
pydantic-settings>=2.3
EOF

# Prune caches/junk the copies may have dragged along.
find "$PKG_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$PKG_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

# --------------------------------------------------------- verify the stage
for required in \
    "$PKG_DIR/webui/index.html" \
    "$PKG_DIR/server/app/api/app.py" \
    "$PKG_DIR/server/pyproject.toml" \
    "$PKG_DIR/run.sh" \
    "$PKG_DIR/run.bat" \
    "$PKG_DIR/README.md" \
    "$PKG_DIR/requirements.lock" \
    "$PKG_DIR/LICENSE"; do
    if [ ! -e "$required" ]; then
        echo "error: staging incomplete, missing $required" >&2
        exit 1
    fi
done
for forbidden in '.env' 'tests' 'node_modules' '__pycache__' '.pytest_cache' '.ruff_cache' 'docs'; do
    hits="$(find "$PKG_DIR" -name "$forbidden" -print -quit)"
    if [ -n "$hits" ]; then
        echo "error: forbidden path '$forbidden' present in staging: $hits" >&2
        exit 1
    fi
done

# -------------------------------------------------------------------- zip it
# python3 zipfile (no zip binary assumed); preserve the run.sh exec bit.
echo "==> Writing $ZIP_PATH"
rm -f "$ZIP_PATH"
python3 - "$STAGE_ROOT" "$ZIP_PATH" <<'PYEOF'
import os
import sys
import zipfile

stage_root, zip_path = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for dirpath, _dirnames, filenames in os.walk(stage_root):
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            arc = os.path.relpath(full, stage_root)
            mode = 0o755 if os.access(full, os.X_OK) else 0o644
            info = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as fh:
                zf.writestr(info, fh.read())
PYEOF

SIZE="$(du -h "$ZIP_PATH" | cut -f1)"
echo "==> Done: $ZIP_PATH ($SIZE)"
