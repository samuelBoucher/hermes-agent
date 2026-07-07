"""Built-in hook that starts the dashboard whenever the gateway boots."""

from __future__ import annotations

import os
import subprocess
import sys
from urllib.error import URLError
from urllib.request import urlopen

DASHBOARD_STATUS_URL = "http://127.0.0.1:9119/api/status"
DASHBOARD_CMD = [sys.executable, "-m", "hermes_cli.main", "dashboard", "--no-open"]


def _dashboard_is_up() -> bool:
    """Return True when the local dashboard is already answering on :9119."""
    try:
        with urlopen(DASHBOARD_STATUS_URL, timeout=0.5) as resp:
            return resp.status == 200
    except (OSError, URLError, ValueError):
        return False


def handle(event_type, context):
    """Launch the dashboard after gateway startup if it's not already running."""
    if event_type != "gateway:startup":
        return

    if os.environ.get("HERMES_DISABLE_DASHBOARD_AUTOSTART") == "1":
        return

    if _dashboard_is_up():
        return

    subprocess.Popen(
        DASHBOARD_CMD,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=os.environ.copy(),
    )
