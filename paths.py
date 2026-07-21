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

__version__ = "1.2.0"

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

# Both added 2026-07-20 for open_logged_in_browser.py's experimental
# auto_confirm_reschedule flow (see notifier.trigger_open_browser() and
# open_logged_in_browser.try_select_target_slot()). RESCHEDULE_LOG_FILE is
# separate from LOG_FILE rather than shared with it: that one's written via
# a RotatingFileHandler from notifier.py's own process, and a detached
# subprocess writing raw stdout into the same path could straddle a
# rotation and silently write into an already-renamed file. This one is a
# plain append-only file with no rotation — events here are rare (one
# reschedule attempt at a time, not once a tick) so it isn't expected to
# grow the way the poll log does.
RESCHEDULE_LOG_FILE = STATE_DIR / "reschedule.log"
RESCHEDULE_CONFIRM_COOLDOWN_FILE = STATE_DIR / "reschedule-confirm-cooldown"

# Static data shipped alongside the code (and bundled into the frozen build).
WORD_CENTERS_FILE = Path(__file__).parent / "word_centers.json"
CATEGORIES_FILE = Path(__file__).parent / "categories.json"


def empty_status():
    """The "nothing has happened yet" status shape, shared by
    notifier.load_status() (its fallback when status.json is missing/
    unreadable) and dashboard_server.EMPTY_STATUS (the JSON served before the
    first check). Lives here — the one module both already import — so the two
    dashboards can't drift out of step, as they did once before (the dashboard
    copy had grown "urgent"/"paused" keys the notifier default lacked).

    A fresh dict (with fresh lists) each call on purpose: load_status()'s
    result is mutated in place by the poll loop, so a shared constant would let
    one caller's edits leak into the other's default.
    """
    return {
        "last_check": None,
        "outcome": None,
        "message": "",
        "urgent": False,
        "current_hits": [],
        "history": [],
        "paused": False,
        "next_check_at": None,
        "session_expires_estimate": None,
    }
