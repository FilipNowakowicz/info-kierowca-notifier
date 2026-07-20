#!/usr/bin/env python3
"""Unified entry point: a local web app (first-run setup wizard + dashboard)
plus the background poller, all in one process — meant to be run directly
(`python app.py`) or packaged into a single no-console binary (see
pyinstaller.spec) so someone can just double-click it with zero setup.

Composes notifier.py (poll loop), dashboard_server.py (status page), and
auto_refresh_session.py (Chrome/QR login) rather than reimplementing any of
them — see each module's own docstring for what it does on its own.
"""
import http.server
import json
import os
import secrets
import socket
import socketserver
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime

import auto_refresh_session
import dashboard_server
import notifier
import open_logged_in_browser
from paths import CATEGORIES_FILE, WORD_CENTERS_FILE
from templates import LOGIN_PAGE, TOOLBAR_HTML, WIZARD_PAGE

HOST = dashboard_server.HOST
PORT = dashboard_server.PORT
# Fallback only, used before a config.json with its own poll_interval_seconds
# exists (see notifier.configured_poll_interval()); Settings sets the real one.
INTERVAL = notifier.DEFAULT_POLL_INTERVAL_SECONDS

# Static snapshot of every active DORD/WORD/MORD/PORD/ZORD center, fetched
# from the site's own (session-gated) dictionary endpoint — see
# fetch_word_centers.py, which regenerates this file. Baked in rather than
# fetched live because the wizard has to work before the user has ever
# logged in, and that endpoint needs a session. Location owned by paths.py.


def load_word_centers():
    try:
        with open(WORD_CENTERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


WORD_CENTERS = load_word_centers()

# Static snapshot of license categories (id/code/label), shown in the setup
# wizard's dropdown so a user picks "B — car" instead of the bare numeric id
# the API wants. Seeded with the confirmed B=5; refresh/extend with
# fetch_categories.py (session-gated, same reason as word_centers.json).
# Location owned by paths.py.


def load_categories():
    try:
        with open(CATEGORIES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [{"id": 5, "code": "B", "label": "B — car"}]


CATEGORIES = load_categories()

EXAM_TYPE_CHOICES = ("Theoretical", "Practice")

# The dashboard page with the interactive toolbar spliced in. Both halves are
# constant strings, so this is computed once at import rather than re-scanned
# and rebuilt on every "/" request.
DASHBOARD_PAGE = dashboard_server.PAGE.replace("</body>", TOOLBAR_HTML + "</body>")


def already_running():
    """True if something is already answering our status endpoint on PORT."""
    try:
        with socket.create_connection((HOST, PORT), timeout=0.3):
            pass
    except OSError:
        return False
    try:
        req = urllib.request.Request(f"http://{HOST}:{PORT}/status.json")
        with urllib.request.urlopen(req, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_session_valid():
    """Live probe for the manual 'Open browser' button: does session.json still
    refresh successfully? Same call notifier.run_check() makes at the top
    of every poll, just outside that loop so the button gets an answer
    immediately instead of waiting for the next tick.
    """
    if not notifier.SESSION_FILE.exists():
        return False
    session = notifier.load_json(notifier.SESSION_FILE)
    status, _body, _headers = notifier.do_request(notifier.REFRESH_URL, session, method="GET")
    if status == 204:
        notifier.save_json(notifier.SESSION_FILE, session)
        return True
    return False


def _wait_for_relogin_and_wake(prior_captured_at, wake_event):
    """Runs in a background thread after a forced relogin is launched, so
    the dashboard's session-expiry estimate updates the moment the QR scan
    lands instead of waiting for the poll loop's next regularly scheduled
    cycle (up to MAX_POLL_INTERVAL_SECONDS away). Waking the loop just
    re-runs run_check(), which recomputes session_expires_estimate from
    session.json's fresh captured_at - same mechanism /setup already uses
    for an interval change, so there's still only one thread ever touching
    dash_status/status.json.

    Watches for session.json's captured_at to actually change rather than
    just the auto-refresh lock clearing, since a stuck/failed relogin
    releases the lock too and waking on that alone would just re-run a
    check against the still-stale session.
    """
    time.sleep(2)  # grace period - mirrors login-status's own, covers the
    # moment right after launch before the process has acquired its lock
    deadline = time.time() + 3600
    while time.time() < deadline:
        if notifier.SESSION_FILE.exists():
            try:
                if notifier.load_json(notifier.SESSION_FILE).get("captured_at") != prior_captured_at:
                    break
            except Exception:
                pass
        if not notifier.auto_refresh_in_progress():
            break  # Chrome closed/crashed before scanning - nothing to wait on
        time.sleep(1)
    wake_event.set()


def build_config(payload):
    """Validate a /setup POST body and assemble it into config.json's schema."""
    def require_str(key, label):
        val = payload.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"{label} is required")
        return val.strip()

    def to_int_list(values, label):
        try:
            return [int(v) for v in values]
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be numeric IDs")

    profile_number = require_str("profile_number", "PKK number")
    ntfy_topic = require_str("ntfy_topic", "Notification topic")

    organization_ids = payload.get("organization_ids")
    if not isinstance(organization_ids, list) or not organization_ids:
        raise ValueError("Pick at least one WORD center")
    organization_ids = to_int_list(organization_ids, "WORD center IDs")
    if len(organization_ids) > notifier.SEARCH_ORG_ID_COUNT:
        raise ValueError(
            f"Pick at most {notifier.SEARCH_ORG_ID_COUNT} WORD centers "
            "— the site's search only accepts that many at a time"
        )

    exam_types = payload.get("exam_types")
    if not isinstance(exam_types, list) or not exam_types or not set(exam_types) <= set(EXAM_TYPE_CHOICES):
        raise ValueError("Pick at least one exam type")

    try:
        category = int(payload.get("category", 5))
    except (TypeError, ValueError):
        raise ValueError("Category must be a number")

    try:
        poll_interval_seconds = int(
            payload.get("poll_interval_seconds", notifier.DEFAULT_POLL_INTERVAL_SECONDS)
        )
    except (TypeError, ValueError):
        raise ValueError("Check frequency must be a number")
    if not notifier.MIN_POLL_INTERVAL_SECONDS <= poll_interval_seconds <= notifier.MAX_POLL_INTERVAL_SECONDS:
        raise ValueError(
            f"Check frequency must be between {notifier.MIN_POLL_INTERVAL_SECONDS} and "
            f"{notifier.MAX_POLL_INTERVAL_SECONDS} seconds"
        )

    try:
        earliest_slot_hour = int(payload.get("earliest_slot_hour", 0))
        latest_slot_hour = int(payload.get("latest_slot_hour", 24))
    except (TypeError, ValueError):
        raise ValueError("Preferred time window must be numbers")
    if not (0 <= earliest_slot_hour < latest_slot_hour <= 24):
        raise ValueError("Preferred time window must be a valid range between 00:00 and 24:00")

    current_slot_date = require_str("current_slot_date", "Current slot date")
    # Must be ISO: notifier.is_urgent() feeds this straight to
    # datetime.fromisoformat() on every check that finds a slot. An
    # unvalidated value (e.g. "05/12/2026") saved fine here, then raised
    # inside the poll loop where loop()'s except-Exception swallowed it —
    # freezing the dashboard on its last status with nothing to explain why.
    try:
        datetime.fromisoformat(current_slot_date)
    except ValueError:
        raise ValueError("Current slot date must be a date like 2026-09-14")

    config = {
        "organization_ids": organization_ids,
        "category": category,
        "profile_number": profile_number,
        "exam_types": exam_types,
        "ntfy_topic": ntfy_topic,
        "current_slot_date": current_slot_date,
        "poll_interval_seconds": poll_interval_seconds,
        "earliest_slot_hour": earliest_slot_hour,
        "latest_slot_hour": latest_slot_hour,
        "phone_alerts": bool(payload.get("phone_alerts", True)),
        "phone_alerts_relogin": bool(payload.get("phone_alerts_relogin", True)),
        "auto_refresh_chrome": bool(payload.get("auto_refresh_chrome", True)),
        "auto_open_browser": bool(payload.get("auto_open_browser", True)),
    }
    # Both experimental, off-by-default — see notifier.trigger_open_browser()/
    # open_logged_in_browser.py. auto_confirm_reschedule is meaningless without
    # auto_select_slot (trigger_open_browser() only ever passes
    # --confirm-reschedule alongside --target-slot), so it's enforced here too
    # rather than trusting the wizard's own JS-side dependent-toggle dimming —
    # a payload built by hand or by stale JS shouldn't be able to persist that
    # combination.
    auto_select_slot = bool(payload.get("auto_select_slot", False))
    config["auto_select_slot"] = auto_select_slot
    config["auto_confirm_reschedule"] = auto_select_slot and bool(
        payload.get("auto_confirm_reschedule", False)
    )
    return config


def pkk_category_id(category_code):
    for c in CATEGORIES:
        if c.get("code") == category_code:
            return c["id"]
    return None


def build_pkk_prefill():
    """Best-effort prefill for the first-run wizard: looks up the account's
    PKK profile(s) via the session the login screen just captured (see
    notifier.fetch_pkk_profiles), so the wizard can offer a ready-made
    "PKK number — category" pick instead of asking for both blind. Drops
    any profile whose categoryName doesn't map to a known category id
    rather than guessing; if that empties the list, the wizard's normal
    manual-entry fields are all that's shown, same as before this existed.
    """
    if not notifier.SESSION_FILE.exists():
        return []
    session = notifier.load_json(notifier.SESSION_FILE)
    prefill = []
    for p in notifier.fetch_pkk_profiles(session):
        category_id = pkk_category_id(p["categoryName"])
        if category_id is None:
            continue
        prefill.append({"pkkNumber": p["pkkNumber"], "categoryId": category_id, "categoryCode": p["categoryName"]})
    return prefill


def render_wizard(existing_config=None, pkk_profiles=None):
    centers_json = json.dumps(WORD_CENTERS, ensure_ascii=False).replace("</", "<\\/")
    page = WIZARD_PAGE.replace("__CENTERS_JSON__", centers_json)
    page = page.replace("__CENTER_COUNT__", str(len(WORD_CENTERS)))
    categories_json = json.dumps(CATEGORIES, ensure_ascii=False).replace("</", "<\\/")
    page = page.replace("__CATEGORIES_JSON__", categories_json)
    pkk_profiles_json = json.dumps(pkk_profiles or [], ensure_ascii=False).replace("</", "<\\/")
    page = page.replace("__PKK_PROFILES_JSON__", pkk_profiles_json)
    ntfy_topic = existing_config["ntfy_topic"] if existing_config else "ik-" + secrets.token_urlsafe(24)
    page = page.replace("__NTFY_TOPIC__", ntfy_topic)
    existing_json = (
        json.dumps(existing_config, ensure_ascii=False).replace("</", "<\\/")
        if existing_config else "null"
    )
    page = page.replace("__EXISTING_CONFIG_JSON__", existing_json)
    return page.encode("utf-8")


class AppHandler(http.server.BaseHTTPRequestHandler):
    logger = None
    dash_status = None
    wake_event = None

    def log_message(self, format, *args):
        pass

    def _send(self, code, body, content_type="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _read_json_body(self):
        """Parse the request body as JSON. On bad JSON, send a 400 and return
        None so the caller can just `if payload is None: return`."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid request."})
            return None

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if notifier.CONFIG_FILE.exists():
                self._send(200, DASHBOARD_PAGE)
            elif not notifier.SESSION_FILE.exists():
                # First run, not logged in yet: get the QR login out of the
                # way first so the wizard that follows can prefill the PKK
                # number/category instead of asking for them blind.
                self._send(200, LOGIN_PAGE)
            else:
                self._send(200, render_wizard(pkk_profiles=build_pkk_prefill()))
        elif self.path == "/setup":
            # The login screen's "skip" link, and a stable direct URL: the
            # plain wizard with no PKK prefill, regardless of session state.
            self._send(200, render_wizard())
        elif self.path == "/settings":
            if notifier.CONFIG_FILE.exists():
                self._send(200, render_wizard(notifier.load_json(notifier.CONFIG_FILE)))
            else:
                self._send(200, render_wizard())
        elif self.path == "/login-status":
            self._send_json(200, {
                "ready": notifier.SESSION_FILE.exists(),
                "in_progress": notifier.auto_refresh_in_progress(),
            })
        elif self.path == "/status.json":
            data = notifier.STATUS_FILE.read_bytes() if notifier.STATUS_FILE.exists() else dashboard_server.EMPTY_STATUS
            self._send(200, data, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/setup":
            self._handle_setup()
        elif self.path == "/login-start":
            self._handle_login_start()
        elif self.path == "/shutdown":
            self._send_json(200, {"ok": True})
            os._exit(0)
        elif self.path == "/manual-login":
            self._handle_manual_login()
        elif self.path == "/relogin-now":
            self._handle_relogin_now()
        elif self.path == "/pause":
            self._set_paused(True)
        elif self.path == "/resume":
            self._set_paused(False)
        elif self.path == "/test-push":
            self._handle_test_push()
        elif self.path == "/reset-account":
            self._handle_reset_account()
        else:
            self._send(404, b"not found", "text/plain")

    def _set_paused(self, paused):
        """Writes both the flag file and status.json synchronously, so the
        dashboard and the headline's pause/resume icon reflect the change
        the instant it's clicked instead of waiting for the poll loop's
        next tick (which could be up to INTERVAL seconds away).
        """
        notifier.set_paused(paused)
        AppHandler.dash_status["paused"] = paused
        notifier.save_status(AppHandler.dash_status)
        self._send_json(200, {"ok": True, "paused": paused})

    def _handle_manual_login(self):
        """Backing handler for the 'Open browser' button: probes the session
        live and either opens the Chrome+QR relogin (forced, so a forgotten
        QR window from a previous session can't silently block it — see
        trigger_auto_refresh()'s docstring) or a plain logged-in browser
        tab. auto_click=False here on purpose: this button is for opening
        the site or troubleshooting, not for the reschedule flow, so unlike
        the automatic urgent-slot-hit trigger it must NOT click through to
        the date-picker — it should just land on the site, logged in.
        """
        config = notifier.load_json(notifier.CONFIG_FILE) if notifier.CONFIG_FILE.exists() else {}
        if check_session_valid():
            outcome = notifier.trigger_open_browser(AppHandler.logger, config, auto_click=False)
            messages = {
                "launched": "Session looks valid — opening a logged-in browser tab.",
                "already_running": "A logged-in browser tab is already open.",
                "disabled": "Session looks valid, but auto_open_browser is turned off in Settings.",
                "launch_failed": "Session looks valid, but the browser failed to launch — check the log.",
                "no_chromium_browser": "Session looks valid, but no Chrome, Edge, or other "
                    "Chromium-based browser was found on this machine — install one to continue.",
            }
        else:
            outcome = notifier.trigger_auto_refresh(
                AppHandler.logger, config, force=True, notify_phone=False
            )
            messages = {
                "launched": "Session looks expired — opening Chrome for a fresh QR login.",
                "disabled": "Session looks expired, but auto_refresh_chrome is turned off in Settings.",
                "launch_failed": "Session looks expired, but Chrome failed to launch — check the log.",
                "no_chromium_browser": "Session looks expired, but no Chrome, Edge, or other "
                    "Chromium-based browser was found on this machine — install one to continue.",
            }
        self._send_json(200, {"ok": True, "action": outcome, "message": messages.get(outcome, "Done.")})

    def _handle_relogin_now(self):
        """Backing handler for the small refresh icon next to the dashboard's
        session-expiry estimate. Unlike _handle_manual_login(), this always
        forces a fresh QR login regardless of whether the current session
        still passes refresh - the whole point is resetting the ~hour
        estimate on demand, not recovering from a dead one.
        """
        config = notifier.load_json(notifier.CONFIG_FILE) if notifier.CONFIG_FILE.exists() else {}
        prior_captured_at = None
        if notifier.SESSION_FILE.exists():
            try:
                prior_captured_at = notifier.load_json(notifier.SESSION_FILE).get("captured_at")
            except Exception:
                pass
        outcome = notifier.trigger_auto_refresh(AppHandler.logger, config, force=True, notify_phone=False)
        if outcome == "launched":
            threading.Thread(
                target=_wait_for_relogin_and_wake,
                args=(prior_captured_at, AppHandler.wake_event),
                daemon=True,
            ).start()
        messages = {
            "launched": "Opening Chrome for a fresh QR login.",
            "disabled": "auto_refresh_chrome is turned off in Settings.",
            "launch_failed": "Chrome failed to launch — check the log.",
            "no_chromium_browser": "No Chrome, Edge, or other Chromium-based browser was found "
                "on this machine — install one to continue.",
        }
        self._send_json(200, {"ok": True, "action": outcome, "message": messages.get(outcome, "Done.")})

    def _handle_login_start(self):
        """Backs the login screen's button: launches Chrome for the QR scan
        before any config exists yet. force=True for the same reason the
        "Open browser" button uses it (see trigger_auto_refresh's docstring)
        — a stale lock left by a forgotten QR window must not silently
        no-op a user's own deliberate click on their very first run.
        """
        outcome = notifier.trigger_auto_refresh(
            AppHandler.logger, {}, force=True, notify_phone=False
        )
        messages = {
            "no_chromium_browser": "No Chrome, Edge, or other Chromium-based browser was found "
                "on this machine. Install one and try again.",
            "launch_failed": "Could not open Chrome — try the manual option below.",
        }
        self._send_json(200, {"ok": True, "action": outcome, "message": messages.get(outcome)})

    def _handle_setup(self):
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            config = build_config(payload)
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        notifier.save_json(notifier.CONFIG_FILE, config)
        needs_login = not notifier.SESSION_FILE.exists()
        self._send_json(200, {"ok": True, "needs_login": needs_login})
        if needs_login:
            AppHandler.logger.info("outcome=setup_complete detail=triggering_login")
        # Wake the already-running poll loop rather than waiting for its
        # current cycle to time out (up to the *old* poll_interval_seconds
        # away) -- otherwise the dashboard the user's about to land on would
        # still show whatever stale status predates this config (e.g.
        # "Missing config.json"), and the countdown would keep counting down
        # the interval from before this save. Waking the real loop thread
        # (rather than spawning a second one-off run_check() here) also
        # means there's only ever one thread touching dash_status/status.json
        # at a time. run_check() itself calls trigger_auto_refresh() when
        # session.json is still missing, so this covers the needs_login case
        # too without a separate explicit call.
        AppHandler.wake_event.set()

    def _handle_test_push(self):
        """Backs the Alerts section's "Send test push" button. Takes the
        topic straight from the request body (not the saved config) so it
        works before the form has ever been saved, same as the readonly
        ntfy_topic field itself is populated client-side before any save.
        """
        payload = self._read_json_body()
        if payload is None:
            return
        topic = (payload.get("topic") or "").strip()
        if not topic:
            self._send_json(400, {"ok": False, "error": "No notification topic set yet."})
            return
        notifier.push_ntfy(
            AppHandler.logger, topic,
            "info-kierowca: test notification",
            "This is what a real alert will look like.",
            priority="default",
        )
        self._send_json(200, {"ok": True})

    def _handle_reset_account(self):
        """Backs the settings page's "Reset account" button: clears
        config.json and session.json so the app falls straight back to the
        login-first screen (see do_GET's "/" routing) instead of the user
        having to go find and delete those files by hand to switch accounts
        or recover from a broken setup.
        """
        notifier.CONFIG_FILE.unlink(missing_ok=True)
        notifier.SESSION_FILE.unlink(missing_ok=True)
        AppHandler.logger.info("outcome=account_reset")
        self._send_json(200, {"ok": True})


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_internal_auto_refresh():
    """Dispatch target for the frozen-binary re-invocation in
    notifier.trigger_auto_refresh() — see its docstring for why this exists.
    """
    sys.argv = [arg for arg in sys.argv if arg != "--internal-auto-refresh"]
    auto_refresh_session.main()


def run_internal_open_browser():
    """Dispatch target for the frozen-binary re-invocation in
    notifier.trigger_open_browser() — see its docstring for why this exists.
    """
    sys.argv = [arg for arg in sys.argv if arg != "--internal-open-browser"]
    open_logged_in_browser.main()


def main():
    if "--internal-auto-refresh" in sys.argv:
        run_internal_auto_refresh()
        return
    if "--internal-open-browser" in sys.argv:
        run_internal_open_browser()
        return

    if already_running():
        webbrowser.open(f"http://{HOST}:{PORT}/")
        return

    logger = notifier.setup_logger()
    dash_status = notifier.load_status()
    AppHandler.logger = logger
    AppHandler.dash_status = dash_status

    stop_event = threading.Event()
    wake_event = threading.Event()
    AppHandler.wake_event = wake_event
    poll_thread = threading.Thread(
        target=notifier.loop,
        args=(logger, dash_status, INTERVAL, stop_event, wake_event),
        daemon=True,
    )
    poll_thread.start()

    try:
        httpd = ThreadingServer((HOST, PORT), AppHandler)
    except OSError as e:
        # already_running() only returns True for a listener that answers our
        # own /status.json. Anything else on the port (a crashed instance
        # mid-shutdown, an unrelated dev server) lands here — and the release
        # binary is built --windowed, so an unhandled traceback would mean
        # double-clicking the app appears to do nothing at all.
        stop_event.set()
        notifier.notify(
            "info-kierowca: can't start",
            f"Port {PORT} is already in use by another program.",
            "critical",
        )
        print(f"Can't start: port {PORT} is already in use ({e}).", file=sys.stderr)
        sys.exit(1)
    webbrowser.open(f"http://{HOST}:{PORT}/")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        httpd.shutdown()


if __name__ == "__main__":
    main()
