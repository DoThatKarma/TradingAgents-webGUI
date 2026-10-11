"""Container health probe for the TradingAgents-webGUI server.

Used by the image HEALTHCHECK (deploy/Dockerfile). Uses only the standard
library so the slim runtime image needs no curl/wget. GET /api/health is
auth-exempt by design (ADR 0005), so no token is required here.

Exit codes: 0 = healthy (HTTP 200), 1 = unhealthy (any other status,
connection error, or timeout).
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

PORT = os.environ.get("TA_WEBGUI_PORT", "8000")
URL = f"http://127.0.0.1:{PORT}/api/health"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=5) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError, TimeoutError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
