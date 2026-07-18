#!/usr/bin/env python3
"""Single owner of the runtime state/config file locations.

These paths used to be re-spelled in five modules (notifier, app,
auto_refresh_session, open_logged_in_browser, dashboard_server, cdp_client).
That mattered more than it looks: the project's promise that a packaged
binary and a `python app.py` run share the same config, session and history
holds only as long as every one of those copies agrees, and a typo in any of
them would silently split state in two rather than fail loudly.

This module deliberately imports nothing from the rest of the project, so it
can sit at the bottom of the import graph and be safely imported everywhere.
"""
from pathlib import Path

__version__ = "1.0.0"

CONFIG_DIR = Path.home() / ".config" / "info-kierowca-notifier"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSION_FILE = CONFIG_DIR / "session.json"

STATE_DIR = Path.home() / ".local" / "state" / "info-kierowca-notifier"
LOG_FILE = STATE_DIR / "notifier.log"
STATUS_FILE = STATE_DIR / "status.json"
# A plain flag file rather than a config.json field, so pausing is a quick
# runtime toggle independent of saved settings, and works the same whether
# checks are driven by app.py's in-process loop or a systemd timer tick.
PAUSE_FILE = STATE_DIR / "paused"
AUTO_REFRESH_LOCK = STATE_DIR / "auto-refresh.lock"

# Static data shipped alongside the code (and bundled into the frozen build).
WORD_CENTERS_FILE = Path(__file__).parent / "word_centers.json"
CATEGORIES_FILE = Path(__file__).parent / "categories.json"
