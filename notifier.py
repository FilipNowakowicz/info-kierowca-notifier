#!/usr/bin/env python3
"""Notification-only slot checker for info-kierowca.pl.

Never books or reserves anything. Reads two endpoints only:
  - GET  /bknd/auth/api/v1/jwt/refresh                       (keep session alive)
  - POST /bknd/exam/api/v1/Schedules/user/MultipleCentersExams (read slot data)
"""
import argparse
import functools
import json
import logging
import logging.handlers
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import auto_refresh_session
import open_logged_in_browser
from paths import (  # noqa: F401  (re-exported: other modules read these off notifier)
    AUTO_REFRESH_LOG_FILE,
    CONFIG_FILE,
    LOG_FILE,
    PAUSE_FILE,
    RESCHEDULE_CONFIRM_COOLDOWN_FILE,
    RESCHEDULE_LOG_FILE,
    SESSION_FILE,
    STATE_DIR,
    STATUS_FILE,
    WORD_CENTERS_FILE,
    __version__,
    empty_status,
)

MAX_HISTORY = 200

# The search endpoint rejects anything but exactly 5 organizationIds
# ("Exactly 5 exam centers must be provided when searching for the fastest
# terms"), even though the user may only want to watch 1-2. The extra slots
# are padded with other real center ids the user doesn't care about — results
# from any center not in organization_ids are discarded below, so which ones
# the padding picks doesn't matter.
SEARCH_ORG_ID_COUNT = 5

# Adjustable via app.py's Settings (poll_interval_seconds in config.json).
# MIN is a hard floor, not a UI default — a deliberate good-citizen limit on an
# undocumented API; don't lower it without an explicit request. MAX is a sanity
# cap so "watching" doesn't become effectively "not watching".
DEFAULT_POLL_INTERVAL_SECONDS = 60
MIN_POLL_INTERVAL_SECONDS = 15
MAX_POLL_INTERVAL_SECONDS = 1800

# The access token in session.json (__Secure-PUDOJT) carries its own 900s
# iat/exp and is silently renewed every refresh call above - it is not what
# eventually forces a relogin. The relogin wall observed live (2026-07-19,
# consistent across several hours) is a separate, absolute ~3600s session
# ceiling tied to the original QR auth ("sid" claim), invisible in either
# session cookie's own claims and not extendable by refreshing more often.
# This constant is only used to estimate/display when that wall will hit
# (dashboard, see session_expires_estimate) - it is not documented by the
# API and not enforced by this code.
SESSION_ESTIMATED_LIFETIME_SECONDS = 3600

# Applied on top of the configured interval, never subtracted - so the
# effective cadence never goes below what the user picked (or the floor
# above). Expressed as a fraction of the interval rather than a flat number
# of seconds, so the randomness scales with whatever interval is chosen
# instead of becoming relatively bigger at short intervals and negligible at
# long ones.
POLL_JITTER_FRACTION = 0.15


# Cached: word_centers.json is static data shipped with the code, never
# changes at runtime, yet build_search_organization_ids() reads it every poll
# cycle to pick filler ids whenever fewer than 5 centers are watched (the
# common case). Callers copy the list via a comprehension before shuffling, so
# the cached list itself is never mutated.
@functools.lru_cache(maxsize=1)
def load_word_center_ids():
    try:
        with open(WORD_CENTERS_FILE, encoding="utf-8") as f:
            return [c["id"] for c in json.load(f)]
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def build_search_organization_ids(config):
    """Pad the configured centers to exactly SEARCH_ORG_ID_COUNT for the search call."""
    wanted = list(dict.fromkeys(config["organization_ids"]))
    if len(wanted) >= SEARCH_ORG_ID_COUNT:
        return wanted[:SEARCH_ORG_ID_COUNT]
    filler_pool = [c for c in load_word_center_ids() if c not in wanted]
    random.shuffle(filler_pool)
    return wanted + filler_pool[: SEARCH_ORG_ID_COUNT - len(wanted)]

BASE = "https://info-kierowca.pl"
REFRESH_URL = f"{BASE}/bknd/auth/api/v1/jwt/refresh"
SEARCH_URL = f"{BASE}/bknd/exam/api/v1/Schedules/user/MultipleCentersExams"
# Traced from the site's own main-*.js (pkkProfilesResource(), used by its
# "check documents"/reservation forms to resolve a PKK number to a license
# category) — used by app.py's setup wizard to prefill the PKK number and
# category from the account instead of asking the user to type them in.
PKK_PROFILES_URL = f"{BASE}/bknd/status/api/v1/pkk/get_profiles"

NTFY_URL = "https://ntfy.sh"

# The site itself won't show slots further out than this, so there's no
# benefit to making it configurable — it's a hard line on info-kierowca.pl,
# not a user preference.
MAX_DAYS_AHEAD = 31

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 15


def setup_logger():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("info-kierowca-notifier")
    logger.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    return logger


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    """Atomically write `data` to `path`.

    The temp file name carries the writing thread's id: status.json and
    session.json are both written from the poll thread *and* from app.py's
    HTTP threads (pause/resume, "Open browser", saving settings), and a single
    fixed "<name>.tmp" let two concurrent writers scribble over each other's
    half-written temp file — one would then rename the other's partial JSON
    into place and the loser's own rename would raise FileNotFoundError.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(path)
        path.chmod(0o600)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def load_status():
    if STATUS_FILE.exists():
        try:
            return load_json(STATUS_FILE)
        except Exception:
            pass
    return empty_status()


def save_status(status):
    save_json(STATUS_FILE, status)


def is_paused():
    return PAUSE_FILE.exists()


def set_paused(paused):
    if paused:
        PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PAUSE_FILE.touch()
    else:
        PAUSE_FILE.unlink(missing_ok=True)


def fastest_of(hits):
    return min(hits, key=lambda h: h["datetime"]) if hits else None


def short_word(name):
    prefix = "WORD Warszawa M/E "
    return name[len(prefix):] if name.startswith(prefix) else name


def is_urgent(fastest_dt, config):
    """Whether fastest_dt is strictly before the date of the user's current
    slot — a different time on the same day does not count as urgent.

    Strict rather than inclusive so that once auto_confirm_reschedule updates
    current_slot_date to a newly-booked date (see trigger_open_browser()), a
    different time slot that same day can't immediately re-trigger, chasing
    minor same-day changes instead of settling. The dashboard/history still
    show every hit found regardless of urgency; this only gates the phone
    push / auto-browser trigger.
    """
    current_slot_date = config["current_slot_date"]
    cutoff = datetime.fromisoformat(current_slot_date).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return fastest_dt < cutoff


def update_status(status, outcome, message="", current_hits=None, urgent=False):
    status["last_check"] = datetime.now().isoformat()
    status["outcome"] = outcome
    status["message"] = message
    status["urgent"] = urgent
    if current_hits is not None:
        status["current_hits"] = current_hits
        signature = fastest_of(current_hits)
        if signature != status.get("last_signature"):
            # Only the fastest hit is stored, not the whole list: that is the
            # only field either dashboard ever reads back out of history, and
            # a busy check can return dozens of hits that would otherwise be
            # rewritten every 60s and re-parsed by the page every 5s. Older
            # entries carrying the full "hits" list still render — see
            # dashboard_server.py's PAGE, which falls back to them.
            status.setdefault("history", []).append(
                {"seen_at": status["last_check"], "fastest": signature}
            )
            status["history"] = status["history"][-MAX_HISTORY:]
            status["last_signature"] = signature
    save_status(status)


def notify(summary, body, urgency="normal"):
    """Desktop notification. notify-send is Linux-only; osascript is macOS's
    always-available equivalent (see auto_refresh_session.notify_desktop()'s
    own docstring for the same fix and its UNVERIFIED caveat — no live Mac
    to test on). No-op if neither is available (e.g. some other OS)."""
    if sys.platform == "darwin":
        script = f"display notification {json.dumps(body)} with title {json.dumps(summary)}"
        try:
            subprocess.run(["osascript", "-e", script], check=False)
        except FileNotFoundError:
            pass
        return
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-a", "info-kierowca-notifier", summary, body],
            check=False,
        )
    except FileNotFoundError:
        pass


def push_ntfy(logger, topic, title, message, priority="default", tags=None):
    """POST a plain notification (no cookies, no PKK) to ntfy.sh. Best-effort."""
    if not topic:
        return
    url = f"{NTFY_URL}/{topic}"
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    req = urllib.request.Request(url, data=message.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT):
            pass
    except Exception as e:
        logger.info("outcome=push_failed detail=%r", str(e))


AUTO_REFRESH_SCRIPT = Path(__file__).parent / "auto_refresh_session.py"
AUTO_REFRESH_LOCK = auto_refresh_session.LOCK_FILE


# Outcome vocabulary returned by trigger_auto_refresh() and
# trigger_open_browser(). Named constants — rather than bare string literals
# re-spelled in app.py's login handlers — so the producer and every consumer
# key off one source; a renamed or added outcome is then a grep away instead of
# a silent fall-through to a generic message. TRIGGER_OUTCOMES is the full set.
TRIGGER_DISABLED = "disabled"
TRIGGER_NO_BROWSER = "no_chromium_browser"
TRIGGER_ALREADY_RUNNING = "already_running"
TRIGGER_LAUNCHED = "launched"
TRIGGER_LAUNCH_FAILED = "launch_failed"
TRIGGER_OUTCOMES = (
    TRIGGER_DISABLED,
    TRIGGER_NO_BROWSER,
    TRIGGER_ALREADY_RUNNING,
    TRIGGER_LAUNCHED,
    TRIGGER_LAUNCH_FAILED,
)


def trigger_auto_refresh(logger, config, force=False, notify_phone=True):
    """Best-effort: launch auto_refresh_session.py to relogin via Chrome+QR.

    Detached so it survives this (oneshot) process exiting — on systemd it's
    handed off via `systemd-run --user` so it isn't killed when this unit's
    cgroup is torn down at exit; elsewhere a plain detached Popen is enough.
    Guarded by auto_refresh_session.py's own lock file so a stuck relogin
    doesn't get relaunched on every subsequent 60s tick.

    Inside a PyInstaller-frozen build, sys.executable is the bundled binary
    itself (not a Python interpreter that can run a loose .py file) and
    AUTO_REFRESH_SCRIPT has no file on disk to point at — so instead we
    re-invoke the binary with a hidden flag that app.py dispatches straight
    to auto_refresh_session.main(), keeping it a separate detached process.

    force=True (the manual "Open browser" button) kills whatever's holding the
    lock and relaunches anyway. This exists because the lock has no timeout
    (auto_refresh_session.py waits indefinitely for a QR scan) and survives
    app.py restarts, since the Chrome+QR process it guards is detached —
    the most common way this bites someone is a QR window left open and
    forgotten from a previous session (confirmed live: a lock stayed held
    for ~10 hours), which silently no-ops every later auto-trigger,
    including the very next app launch, with no visible sign why. The
    automatic path stays conservative (never force); force is opt-in so a
    background retry never kills a window the user is mid-scan on.

    notify_phone=False (the manual "Open browser"/login-screen buttons) skips
    the ntfy push telling the user to go scan a QR — they just clicked a
    button and are already sitting in front of Chrome watching it open, so a
    phone notification saying the same thing is just noise (and, worse, reads
    as an alert when nothing's actually wrong). The automatic auth_expired
    path keeps the push, since that's the one case where the user genuinely
    isn't watching and needs the nudge. The desktop notification still fires
    either way — it's local and harmless.

    Returns one of the TRIGGER_* outcome constants.
    """
    if not config.get("auto_refresh_chrome", True):
        return TRIGGER_DISABLED
    if not auto_refresh_session.chrome_available():
        logger.info("outcome=auto_refresh_no_browser detail=no_chromium_found")
        return TRIGGER_NO_BROWSER
    if AUTO_REFRESH_LOCK.exists():
        try:
            pid = int(AUTO_REFRESH_LOCK.read_text().strip())
            os.kill(pid, 0)
            if not force:
                logger.info("outcome=auto_refresh_skipped detail=already_running pid=%s", pid)
                return TRIGGER_ALREADY_RUNNING
            logger.info("outcome=auto_refresh_force_restart detail=killing_stale pid=%s", pid)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            else:
                # Wait for it to actually go before relaunching. It has a
                # SIGTERM handler to run (closing its Chrome, which still
                # holds the shared --user-data-dir), and the systemd path
                # reuses a fixed --unit name that systemd-run refuses to
                # reissue while the old unit is still deactivating.
                for _ in range(50):  # ~5s
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                    time.sleep(0.1)
                else:
                    logger.info("outcome=auto_refresh_force_restart detail=still_alive pid=%s", pid)
        except ValueError:
            pass  # stale lock — let auto_refresh_session.py sort it out
        except OSError:
            pass  # pid is gone — stale lock, safe to relaunch
        AUTO_REFRESH_LOCK.unlink(missing_ok=True)
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--internal-auto-refresh"]
    else:
        if not AUTO_REFRESH_SCRIPT.exists():
            return TRIGGER_LAUNCH_FAILED
        python = sys.executable
        if shutil.which("systemd-run"):
            cmd = [
                "systemd-run", "--user", "--collect",
                "--unit=info-kierowca-auto-refresh",
                "--description=info-kierowca.pl auto session refresh",
                python, str(AUTO_REFRESH_SCRIPT),
            ]
        else:
            cmd = [python, str(AUTO_REFRESH_SCRIPT)]
    if not notify_phone:
        cmd.append("--no-phone-push")
    try:
        AUTO_REFRESH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUTO_REFRESH_LOG_FILE, "a") as logf:
            logf.write(f"\n--- {datetime.now().isoformat()} launching: {cmd!r} ---\n")
            logf.flush()
            subprocess.Popen(
                cmd, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True
            )
        logger.info("outcome=auto_refresh_launched")
        return TRIGGER_LAUNCHED
    except Exception as e:
        logger.info("outcome=auto_refresh_launch_failed detail=%r", str(e))
        return TRIGGER_LAUNCH_FAILED


def auto_refresh_in_progress():
    """Whether a launched auto_refresh_session.py is still alive and holding
    AUTO_REFRESH_LOCK — used by app.py's login screen to tell "still waiting
    on you to scan" apart from "Chrome was closed/crashed before you scanned,
    give up waiting and let the user retry" (see wait_for_cookies's docstring
    in auto_refresh_session.py: that process releases the lock and exits the
    moment its own Chrome disappears, whether from a scan, a close, or a
    crash — so the lock's liveness is exactly the signal we need here).
    """
    if not AUTO_REFRESH_LOCK.exists():
        return False
    try:
        pid = int(AUTO_REFRESH_LOCK.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


OPEN_BROWSER_SCRIPT = Path(__file__).parent / "open_logged_in_browser.py"
OPEN_BROWSER_PORT = open_logged_in_browser.DEFAULT_PORT

# How long after an attempted final-confirm click (see
# open_logged_in_browser.try_select_target_slot(), which writes
# RESCHEDULE_CONFIRM_COOLDOWN_FILE right before that click) trigger_open_browser()
# holds off passing --confirm-reschedule again. Without it, a confirm attempt
# whose own post-click verification timed out (so current_slot_date never got
# updated) could let the very next poll cycle attempt another confirm on some
# other nearby slot — a real reservation change, possibly to a worse date,
# before any human has had a chance to notice and step in. Not user-configurable
# — this is a safety margin, not a tunable.
RESCHEDULE_CONFIRM_COOLDOWN_SECONDS = 900


def confirm_reschedule_cooldown_active():
    """Whether a --confirm-reschedule attempt happened recently enough that
    trigger_open_browser() should hold off passing that flag again. Missing or
    unparseable RESCHEDULE_CONFIRM_COOLDOWN_FILE just means no recent attempt
    is known — not a hard stop, so a fresh install/state dir behaves as if the
    cooldown already elapsed.
    """
    try:
        last = datetime.fromisoformat(RESCHEDULE_CONFIRM_COOLDOWN_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return False
    return (datetime.now() - last).total_seconds() < RESCHEDULE_CONFIRM_COOLDOWN_SECONDS


def trigger_open_browser(logger, config, auto_click=True, target_hit=None):
    """Best-effort: launch open_logged_in_browser.py so a pre-authenticated
    tab is already open by the moment the push notification lands — skips
    the login step that otherwise costs you the fastest-moving slots.

    Skipped if something's already answering on OPEN_BROWSER_PORT (its own
    dedicated debug port) so a slot that keeps reappearing under a new
    signature doesn't pile up duplicate Chrome windows — you'll just have
    the one from the first hit to work with.

    Same frozen-build re-invocation trick as trigger_auto_refresh() — see
    its docstring — since sys.executable is the bundled binary itself
    inside a PyInstaller build, not a Python interpreter that can run a
    loose .py file.

    auto_click=False (the manual "Open browser" button when the session is
    still valid) passes --no-auto-click through, so it just opens the
    logged-in tab without clicking through to the reschedule date-picker —
    that click-through is only wanted for the automatic urgent-slot-hit
    path, which keeps the default auto_click=True.

    target_hit, when given together with auto_click and config's
    experimental, default-off "auto_select_slot" flag, is one of
    run_check()'s hit_dicts (word/exam_type/datetime/places) — passed
    through as --target-slot JSON so open_logged_in_browser.py can also try
    to expand that date's slot group, select the matching time radio
    button, and click through to the summary/review screen, past the plain
    date-picker screen.

    A second, separate, also default-off flag — config's
    "auto_confirm_reschedule" — additionally passes --confirm-reschedule,
    which (only once auto_select_slot has already landed on the summary
    screen, and only after open_logged_in_browser.py itself re-verifies
    that screen matches the intended slot) clicks through the final
    "Potwierdź i przejdź dalej" confirm button — actually submitting the
    reservation change. auto_confirm_reschedule alone, without
    auto_select_slot, does nothing (no --target-slot means
    open_logged_in_browser.py never reaches that screen to confirm on).
    UNVERIFIED against the live site as of 2026-07-20, by explicit user
    request that same day — see open_logged_in_browser.py's own docstrings
    for exactly what it does and does not click, and the verification step
    that gates the final click. Both flags are omitted entirely (no
    --target-slot/--confirm-reschedule at all) whenever off, so a config
    predating this feature behaves identically to before.

    --confirm-reschedule is further gated by confirm_reschedule_cooldown_active()
    (see its own docstring) — even with auto_confirm_reschedule on, it's
    withheld (falling back to --target-slot alone, same as auto_select_slot
    without auto_confirm_reschedule) if a confirm attempt was made too
    recently, regardless of whether that attempt's own outcome is known.

    The launched subprocess's stdout/stderr go to RESCHEDULE_LOG_FILE
    (append mode) rather than DEVNULL — this is a
    detached, fire-and-forget launch with no other way for its outcome to
    reach anyone, and open_logged_in_browser.py's own print()s are the only
    record of what an auto-triggered run actually did, especially the
    "couldn't verify automatically — check yourself" messages past the
    confirm click.

    Returns one of the TRIGGER_* outcome constants. No force option here
    (unlike trigger_auto_refresh) — forcing would mean launching a second
    Chrome on the same fixed debug port an already-open one is using, which
    is fragile rather than useful; if one's already open that's already the
    outcome a caller wants.
    """
    if not config.get("auto_open_browser", True):
        return TRIGGER_DISABLED
    if not auto_refresh_session.chrome_available():
        logger.info("outcome=open_browser_no_browser detail=no_chromium_found")
        return TRIGGER_NO_BROWSER
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{OPEN_BROWSER_PORT}/json/version", timeout=1)
        logger.info("outcome=open_browser_skipped detail=already_running")
        return TRIGGER_ALREADY_RUNNING
    except Exception:
        pass  # nothing listening on that port -> safe to launch
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--internal-open-browser"]
    else:
        if not OPEN_BROWSER_SCRIPT.exists():
            return TRIGGER_LAUNCH_FAILED
        cmd = [sys.executable, str(OPEN_BROWSER_SCRIPT)]
    if not auto_click:
        cmd.append("--no-auto-click")
    elif target_hit is not None and config.get("auto_select_slot", False):
        cmd += ["--target-slot", json.dumps(target_hit)]
        if config.get("auto_confirm_reschedule", False):
            if confirm_reschedule_cooldown_active():
                logger.info("outcome=confirm_reschedule_skipped detail=cooldown_active")
            else:
                cmd.append("--confirm-reschedule")
    try:
        RESCHEDULE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RESCHEDULE_LOG_FILE, "a") as logf:
            logf.write(f"\n--- {datetime.now().isoformat()} launching: {cmd!r} ---\n")
            logf.flush()
            subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        logger.info("outcome=open_browser_launched")
        return TRIGGER_LAUNCHED
    except Exception as e:
        logger.info("outcome=open_browser_launch_failed detail=%r", str(e))
        return TRIGGER_LAUNCH_FAILED


def cookie_header(session):
    return "; ".join(f"{k}={v}" for k, v in session.get("cookies", {}).items())


def cookie_is_deletion(value, attrs):
    """Whether a Set-Cookie is the server clearing the cookie rather than
    setting one. Servers expire a cookie by sending it back empty and/or with
    Max-Age=0 / an Expires in the past."""
    if not value:
        return True
    lowered = attrs.lower()
    if "max-age=0" in lowered.replace(" ", ""):
        return True
    return "expires=thu, 01 jan 1970" in lowered


def parse_set_cookies(headers, session):
    """Merge Set-Cookie headers into session["cookies"].

    Deletions must actually delete: a logout/invalidate response carrying
    `__Secure-PUDOJT=; Expires=Thu, 01 Jan 1970 ...` would otherwise be stored
    as an empty-string cookie, leaving session.json looking complete to
    open_logged_in_browser.py's COOKIE_NAMES check — which then injects blank
    cookies and opens a logged-*out* tab instead of reporting the problem.
    """
    if headers is None:
        return
    for raw in headers.get_all("Set-Cookie") or []:
        name, _, rest = raw.partition("=")
        value, _, attrs = rest.partition(";")
        name = name.strip()
        cookies = session.setdefault("cookies", {})
        if cookie_is_deletion(value, attrs):
            cookies.pop(name, None)
        else:
            cookies[name] = value


def do_request(url, session, method="GET", json_body=None):
    data = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie_header(session),
        "Referer": f"{BASE}/reservation",
        "Origin": BASE,
    }
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            parse_set_cookies(resp.headers, session)
            return resp.status, body, resp.headers
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, body, e.headers
    except urllib.error.URLError as e:
        return None, str(e).encode(), None


def fetch_pkk_profiles(session):
    """Best-effort lookup of the account's PKK profile(s) — used by app.py's
    setup wizard to prefill the PKK number/category right after QR login
    instead of asking the user to type a PKK number in blind. The endpoint
    also returns pesel/name/birthDate; only pkkNumber/categoryName are kept,
    matching this project's minimal-footprint stance on PII. Returns []
    on any failure (session not ready yet, unexpected shape, etc.) so a
    fetch hiccup just falls back to manual entry rather than blocking setup.
    """
    try:
        status, body, _headers = do_request(PKK_PROFILES_URL, session, method="GET")
        if status != 200:
            return []
        profiles = json.loads(body)
        return [
            {"pkkNumber": p["pkkNumber"], "categoryName": p["categoryName"]}
            for p in profiles
            if isinstance(p, dict) and p.get("pkkNumber") and p.get("categoryName")
        ]
    except Exception:
        return []


def _handle_auth_expired(logger, dash_status, config, status, stage):
    """Shared response to an auth-failure status in either run_check() stage:
    log it, fire the critical 'session expired' notification, mark the
    dashboard status, and kick off an auto-relogin. Only the log's stage label
    and the status message differ per stage; which status codes count as an
    auth failure stays each stage's own decision (see the call sites — refresh
    also treats 404, both fold in 500)."""
    logger.info("outcome=auth_expired status=%s stage=%s", status, stage)
    notify(
        "info-kierowca: session expired",
        "Log back in via browser and update session.json",
        "critical",
    )
    update_status(dash_status, "auth_expired", f"Session expired during {stage}")
    trigger_auto_refresh(logger, config)


def run_check(logger, dash_status):
    """Note: pausing/resuming itself is applied instantly by app.py's
    /pause and /resume handlers (they write dash_status/status.json
    directly) — this check just stops the real work from running while
    paused. It deliberately leaves outcome/message untouched instead of
    overwriting them with a "paused" outcome, so status.json still holds
    the last real result underneath and Resume doesn't have to wait for a
    fresh check to stop showing "Paused".
    """
    paused = is_paused()
    if dash_status.get("paused") != paused:
        dash_status["paused"] = paused
        save_status(dash_status)
    if paused:
        return

    # No desktop notification here: "no config yet" is the normal state during
    # first-run setup and right after app.py's Reset account, where the poll
    # thread keeps ticking while the user sits on the login screen — notifying
    # meant a critical popup every INTERVAL seconds. The dashboard already
    # shows it. Caught rather than exists()-checked because Reset account can
    # unlink the file from an HTTP thread between the check and the read.
    try:
        config = load_json(CONFIG_FILE)
    except FileNotFoundError:
        logger.info("outcome=setup_incomplete detail=missing_config")
        update_status(dash_status, "setup_incomplete", "Waiting for setup to be completed")
        return

    if not SESSION_FILE.exists():
        logger.info("outcome=auth_missing")
        dash_status["session_expires_estimate"] = None
        notify(
            "info-kierowca: no session",
            "session.json missing — log in via browser and populate cookies",
            "critical",
        )
        update_status(dash_status, "auth_expired", "session.json missing")
        trigger_auto_refresh(logger, config)
        return
    session = load_json(SESSION_FILE)
    captured_at = session.get("captured_at")
    dash_status["session_expires_estimate"] = (
        datetime.fromtimestamp(captured_at + SESSION_ESTIMATED_LIFETIME_SECONDS).isoformat()
        if captured_at
        else None
    )

    # 1. Keep the session alive.
    status, body, _headers = do_request(REFRESH_URL, session, method="GET")
    if status == 204:
        save_json(SESSION_FILE, session)
        logger.info("outcome=refresh_ok status=%s", status)
    elif status is None:
        # do_request returns None for URLError — i.e. we never reached the
        # server (offline, DNS, laptop lid closed). That is not an
        # "unexpected response" and must not fire a critical notification
        # every tick for the duration of an outage; the next check retries.
        detail = body[:200].decode(errors="replace") if body else ""
        logger.info("outcome=network_error stage=refresh detail=%r", detail)
        update_status(dash_status, "network_error", "Can't reach info-kierowca.pl — will retry")
        return
    elif status in (401, 403, 404, 500):
        # 500 is grouped with the real auth failures (same as the search stage
        # below): confirmed live 2026-07-18 that a 500 from the refresh endpoint
        # is just an expired-cookie response, not a transient upstream error, so
        # it must relogin rather than display as a generic "something's wrong".
        _handle_auth_expired(logger, dash_status, config, status, "refresh")
        return
    else:
        # Other 5xx: a transient upstream error is not an expired session,
        # and must not pop a QR window onto the user's desktop.
        detail = body[:200].decode(errors="replace") if body else ""
        logger.info("outcome=unexpected status=%s stage=refresh detail=%r", status, detail)
        update_status(dash_status, "unexpected", f"Refresh call returned {status}")
        return

    # 2. Search for slots.
    payload = {
        "startDate": datetime.now().strftime("%Y-%m-%d"),
        "organizationId": build_search_organization_ids(config),
        "category": config["category"],
        "profileNumber": config["profile_number"],
        "profileType": "Pkk",
    }
    status, body, _headers = do_request(SEARCH_URL, session, method="POST", json_body=payload)

    if status is None:
        # Never reached the server — see the matching branch in the refresh
        # stage above. Log and retry next tick rather than alerting.
        detail = body[:200].decode(errors="replace") if body else ""
        logger.info("outcome=network_error stage=search detail=%r", detail)
        update_status(dash_status, "network_error", "Can't reach info-kierowca.pl — will retry")
        return
    # 500 is in the auth set here too (see the refresh stage above): a 500
    # from the search endpoint has in practice always turned out to be the
    # same underlying cookie expiry. See docs/ADVANCED.md's auto-relogin note.
    if status in (401, 403, 500):
        _handle_auth_expired(logger, dash_status, config, status, "search")
        return
    if status != 200:
        # 5xx included: transient upstream errors are not an expired session.
        detail = body[:200].decode(errors="replace") if body else ""
        logger.info("outcome=unexpected status=%s stage=search detail=%r", status, detail)
        update_status(dash_status, "unexpected", f"Search call returned {status}")
        return

    try:
        results = json.loads(body)
        assert isinstance(results, list)
    except Exception:
        detail = body[:200].decode(errors="replace") if body else ""
        logger.info("outcome=unparseable status=%s detail=%r", status, detail)
        notify(
            "info-kierowca: unexpected response shape",
            "Search response wasn't the expected JSON — CAPTCHA? layout change? check manually",
            "critical",
        )
        update_status(dash_status, "unexpected", "Response wasn't the expected JSON shape")
        return

    save_json(SESSION_FILE, session)

    max_date = datetime.now() + timedelta(days=MAX_DAYS_AHEAD)
    wanted_types = set(config["exam_types"])
    watch_ids = set(config["organization_ids"])
    # Hour-of-day preference (wizard's dual-handle slider) — [earliest, latest)
    # against dt.hour, so a config predating this feature (both keys absent)
    # defaults to the full day and filters nothing.
    earliest_hour = config.get("earliest_slot_hour", 0)
    latest_hour = config.get("latest_slot_hour", 24)
    hits = []
    for word in results:
        if word.get("wordId") not in watch_ids:
            continue
        for exam in word.get("examCollectionForDay", []):
            exam_type = exam.get("examType")
            if exam_type not in wanted_types:
                continue
            dt_str = exam.get("theoryDateTime") or exam.get("practiceDateTime")
            if not dt_str:
                continue
            dt = datetime.fromisoformat(dt_str)
            if dt <= max_date and earliest_hour <= dt.hour < latest_hour:
                places = exam.get("placeTheoryAmount") or exam.get("placePracticeAmount")
                hits.append((word.get("wordName"), exam_type, dt, places))

    hits.sort(key=lambda h: h[2])
    hit_dicts = [
        {"word": w, "exam_type": t, "datetime": dt.isoformat(), "places": n}
        for w, t, dt, n in hits
    ]

    if hits:
        exam_labels = {"Theoretical": "theory", "Practice": "practice"}
        lines = [
            "{} — {} · {} spots ({})".format(
                w, dt.strftime("%a %d %b %Y, %H:%M"), n, exam_labels.get(t, t)
            )
            for w, t, dt, n in hits
        ]
        logger.info("outcome=slot_found status=%s detail=%r", status, "; ".join(lines))

        fastest = fastest_of(hit_dicts)
        urgent = is_urgent(datetime.fromisoformat(fastest["datetime"]), config)
        if urgent:
            if fastest != dash_status.get("last_push_signature"):
                if config.get("phone_alerts", True):
                    push_body = "{} · {} · {} spots".format(
                        datetime.fromisoformat(fastest["datetime"]).strftime("%a %d %b, %H:%M"),
                        short_word(fastest["word"]),
                        fastest["places"],
                    )
                    push_ntfy(
                        logger,
                        config.get("ntfy_topic"),
                        "Slot within range!",
                        push_body,
                        priority="urgent",
                    )
                    logger.info("outcome=push_sent detail=%r", fastest)
                dash_status["last_push_signature"] = fastest
                trigger_open_browser(logger, config, target_hit=fastest)
        else:
            dash_status["last_push_signature"] = None

        update_status(dash_status, "slot_found", "", hit_dicts, urgent=urgent)
    else:
        logger.info("outcome=no_slot status=%s", status)
        dash_status["last_push_signature"] = None
        update_status(dash_status, "no_slot", "", hit_dicts)


def configured_poll_interval(default=DEFAULT_POLL_INTERVAL_SECONDS):
    """Read config.json's poll_interval_seconds fresh every call, so a
    Settings save (app.py's /setup) takes effect on the very next wait
    instead of needing the poll thread restarted. `default` is only used
    when config.json doesn't exist yet or predates this setting."""
    try:
        config = load_json(CONFIG_FILE)
    except FileNotFoundError:
        return default
    seconds = config.get("poll_interval_seconds", default)
    return min(MAX_POLL_INTERVAL_SECONDS, max(MIN_POLL_INTERVAL_SECONDS, seconds))


def jittered_wait(interval):
    return interval + random.uniform(0, interval * POLL_JITTER_FRACTION)


def loop(logger, dash_status, interval=None, stop_event=None, wake_event=None):
    """Check on a timer until stop_event is set (or forever, if none given).

    Factored out of main()'s --loop branch so app.py can run this in a
    background thread instead of shelling out to `python notifier.py --loop`
    as a subprocess — stop_event lets that thread be told to exit cleanly.

    `interval` is only the fallback default passed to configured_poll_interval()
    each cycle (e.g. from --interval on the CLI); once config.json has its own
    poll_interval_seconds, that value wins.

    `wake_event`, if given, lets app.py's /setup handler cut the current wait
    short the instant a new poll_interval_seconds is saved, instead of the
    dashboard's countdown (and the actual next check) staying stuck on
    whatever interval was configured when this cycle's wait started — set it
    and it's cleared right after waking so the *next* cycle's wait isn't
    accidentally skipped too.
    """
    if stop_event is None:
        stop_event = threading.Event()
    if wake_event is None:
        wake_event = threading.Event()
    default_interval = interval or DEFAULT_POLL_INTERVAL_SECONDS
    logger.info("outcome=loop_start interval=%s", default_interval)
    while not stop_event.is_set():
        try:
            run_check(logger, dash_status)
        except Exception:
            logger.exception("outcome=crash stage=run_check")
        wait_s = jittered_wait(configured_poll_interval(default_interval))
        # The exact resolved wait (post-jitter) so the dashboard's countdown
        # can show precisely when the next check will fire instead of
        # guessing from the base interval alone.
        dash_status["next_check_at"] = (datetime.now() + timedelta(seconds=wait_s)).isoformat()
        save_status(dash_status)
        wake_event.wait(wait_s)
        wake_event.clear()


def main():
    parser = argparse.ArgumentParser(description="info-kierowca.pl slot checker")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously instead of once — no systemd/cron/Task Scheduler needed",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=(
            "Seconds between checks in --loop mode (default: 60). Only used "
            "as a fallback when config.json has no poll_interval_seconds "
            "(set via app.py's Settings page)."
        ),
    )
    args = parser.parse_args()

    logger = setup_logger()
    dash_status = load_status()

    if args.loop:
        loop(logger, dash_status, args.interval)
    else:
        run_check(logger, dash_status)


if __name__ == "__main__":
    main()
