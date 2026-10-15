#!/usr/bin/env python3
"""Notification-only slot checker for info-kierowca.pl.

Never books or reserves anything. Reads these endpoints only:
  - GET  /bknd/auth/api/v1/jwt/refresh                       (keep session alive)
  - POST /bknd/exam/api/v1/Schedules/user/MultipleCentersExams (read slot data)
  - POST /bknd/exam/api/v1/Schedules/user/OneCenterExam
"""
import argparse
import functools
import json
import logging
import logging.handlers
import os
import random
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

from info_kierowca_notifier import client
from info_kierowca_notifier.auth import launch as auth_launch
from info_kierowca_notifier import ntfy_transport
from info_kierowca_notifier.booking import launch as booking_launch
from info_kierowca_notifier import tls_transport
from info_kierowca_notifier.paths import (  # noqa: F401  (re-exported: other modules read these off notifier)
    CONFIG_FILE,
    LOG_FILE,
    PAUSE_FILE,
    SESSION_FILE,
    STATUS_FILE,
    WORD_CENTERS_FILE,
    __version__,
    empty_status,
    ensure_state_dir,
)

MAX_HISTORY = 200
NTFY_TIMEOUT = 15

# The search endpoint rejects anything but exactly 5 organizationIds
# ("Exactly 5 exam centers must be provided when searching for the fastest
# terms"), even though the user may only want to watch 1-2. The extra slots
# are padded with other real center ids the user doesn't care about — results
# from any center not in organization_ids are discarded below, so which ones
# the padding picks doesn't matter.
SEARCH_ORG_ID_COUNT = 5

# Adjustable via the app module's Settings (poll_interval_seconds in config.json).
# MIN is a hard floor, not a UI default — a deliberate good-citizen limit on an
# undocumented API; don't lower it without an explicit request. MAX is a sanity
# cap so "watching" doesn't become effectively "not watching".
DEFAULT_POLL_INTERVAL_SECONDS = 420
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

# Profil Zaufany can refresh without user interaction, so start shortly before
# the observed absolute session ceiling instead of waiting for the API to reject
# the old cookies. Five minutes leaves room for the normal 60s and 120s retry
# cooldowns when failures are detected quickly. mObywatel deliberately remains
# expiry-driven because an early refresh would ask the user to scan a QR code.
PZ_PROACTIVE_RELOGIN_LEAD_SECONDS = 5 * 60

RATE_LIMIT_MAX_SEARCHES = 10
RATE_LIMIT_WINDOW_SECONDS = 60 * 60
RATE_LIMIT_SAFETY_SECONDS = 5
PAUSE_RECHECK_SECONDS = 5



# Cached: word_centers.json is static data shipped with the code, never
# changes at runtime, yet build_search_organization_ids() reads it every poll
# cycle to pick filler ids whenever fewer than 5 centers are watched (the
# common case). Callers copy the list via a comprehension before shuffling, so
# the cached list itself is never mutated.
@functools.lru_cache(maxsize=1)

def load_word_centers():
    try:
        with open(WORD_CENTERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

def load_word_center_ids():
    return [
        c["id"]
        for c in load_word_centers()
        if "id" in c
    ]

def build_search_organization_ids(config):
    """Pad the configured centers to exactly SEARCH_ORG_ID_COUNT for the search call."""
    wanted = list(dict.fromkeys(config["organization_ids"]))
    if len(wanted) >= SEARCH_ORG_ID_COUNT:
        return wanted[:SEARCH_ORG_ID_COUNT]
    filler_pool = [c for c in load_word_center_ids() if c not in wanted]
    random.shuffle(filler_pool)
    return wanted + filler_pool[: SEARCH_ORG_ID_COUNT - len(wanted)]

# info-kierowca.pl currently allows the search start date from two days
# ahead. The Settings UI and app-side validation additionally cap selection
# at six calendar months ahead, matching the site's date picker. The poller
# must not silently rewrite a valid saved startDate to an older date.
SEARCH_START_MIN_DAYS_AHEAD = 2


def search_start_date(value, *, today=None):
    """Return the configured lower search bound without a 31-day clamp.

    Invalid/missing legacy values fall back to the site's earliest selectable
    start date (today + 2 days). Valid values are never silently moved
    backwards; app.build_config() owns the six-month validation.
    """
    today = today or date.today()
    minimum = today + timedelta(days=SEARCH_START_MIN_DAYS_AHEAD)

    if not value:
        return minimum

    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None

    if parsed is None:
        return minimum

    return max(parsed, minimum)

class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """chmod 0600 the log file every time it's (re)opened.

    Every other state file this project writes (config.json, session.json,
    status.json) is 0600; plain ``logging.handlers.RotatingFileHandler``
    creates its file with umask-default permissions (typically 0644) since
    it just calls the stdlib ``open()``. Overriding ``_open()`` — rather than
    chmod'ing once after construction — also covers ``doRollover()``, which
    calls ``_open()`` again after each rotation to reopen the fresh base
    file; a one-time chmod at startup would only hold until the first
    rotation.
    """

    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


def setup_logger():
    ensure_state_dir()
    logger = logging.getLogger("info-kierowca-notifier")
    logger.setLevel(logging.INFO)
    handler = _PrivateRotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    try:
        tls = tls_transport.diagnostics()
        logger.info(
            "tls backend=%s platform=%s openssl=%s app_ca_override=%s ssl_cert_file=%s ssl_cert_dir=%s bundled_ca=%s",
            tls.backend,
            tls.platform,
            tls.openssl,
            tls.app_ca_bundle_configured,
            tls.ssl_cert_file_configured,
            tls.ssl_cert_dir_configured,
            tls.bundled_ca_available,
        )
    except tls_transport.TLSConfigurationError as exc:
        logger.warning("outcome=tls_configuration_error detail=%s", exc)
    return logger


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    """Atomically write `data` to `path` at 0600 from the moment it exists —
    no window where it's readable by other users.

    The temp file name carries the writing thread's id: status.json and
    session.json are both written from the poll thread *and* from the app module's
    HTTP threads (pause/resume, "Open browser", saving settings), and a single
    fixed "<name>.tmp" let two concurrent writers scribble over each other's
    half-written temp file — one would then rename the other's partial JSON
    into place and the loser's own rename would raise FileNotFoundError.

    The old plain ``open(tmp, "w")`` created the temp file at the umask
    default (typically 0644) and only chmod'd *after* the JSON was written
    and renamed into place — a brief but real window where a freshly
    (re)written config.json/session.json was world-readable. os.open()'s
    own mode argument, like os.open(path, flags, 0o600) in
    booking.transaction's DiagnosticRecorder.save(), applies before any data
    is written, and os.rename (Path.replace) carries that mode onto `path`
    when it lands — so `path` is 0600 the instant it appears, with no
    separate chmod needed afterward.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(path)
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
        ensure_state_dir()
        PAUSE_FILE.touch()
    else:
        PAUSE_FILE.unlink(missing_ok=True)


def fastest_of(hits):
    return min(hits, key=lambda h: h["datetime"]) if hits else None


def push_signature(hit):
    """Identity of a hit dict for push/trigger de-duplication.

    Deliberately excludes "places": comparing the full hit dict meant a
    spot count changing on an otherwise-identical slot (e.g. 3 -> 2, someone
    else grabbing one) looked like a brand-new hit, re-firing the urgent
    phone push and another trigger_open_browser() --target-slot call for a
    slot already being worked on — only the port-9555-busy check in
    booking.launch stopped a literal duplicate Chrome window on top of that.

    Returns a list, not a tuple: dash_status["last_push_signature"] is
    persisted to status.json and reloaded on the next process start (see
    load_status()) — json round-trips a tuple back as a list, and a tuple
    never compares equal to a list, so a tuple here would make the very
    first comparison after any restart spuriously "different" and re-fire.
    """
    return [hit["word"], hit["exam_type"], hit["datetime"]]


def short_word(name):
    prefix = "WORD Warszawa M/E "
    return name[len(prefix):] if name.startswith(prefix) else name


# Maps a hit's already-known exam_type to the (dateTime, placeAmount) field
# pair the search API keys by exam type. Used instead of the previous
# `exam.get("theoryDateTime") or exam.get("practiceDateTime")`-style
# fallback, which always preferred the theory fields whenever both were
# present on the same record — even for a Practice hit — silently attaching
# the wrong datetime/places to it. That datetime also feeds
# --target-slot's row-matching and confirm-time verification in
# booking/reschedule.py, so picking the wrong one is not just a display bug.
EXAM_TYPE_FIELDS = {
    "Theoretical": ("theoryDateTime", "placeTheoryAmount"),
    "Practice": ("practiceDateTime", "placePracticeAmount"),
}

EXAM_ID_FIELDS = {
    "Theoretical": "theoryId",
    "Practice": "practiceId",
}


def exam_slot_fields(exam, exam_type):
    """Return (dt_str, places) for `exam`, keyed explicitly off `exam_type`
    (already established by the caller) rather than an or-fallback across
    both exam types' fields. Returns (None, None) for an exam_type this
    project doesn't recognize, so the caller can skip it exactly like a
    missing dt_str already does."""
    fields = EXAM_TYPE_FIELDS.get(exam_type)
    if fields is None:
        return None, None
    dt_field, places_field = fields
    return exam.get(dt_field), exam.get(places_field)


def parse_multi_center_hits(results, config, lower_date, now):
    """Parse MultipleCentersExams response without a local 31-day cap."""
    wanted_types = set(config["exam_types"])
    watch_ids = set(config["organization_ids"])
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

            dt_str, places = exam_slot_fields(exam, exam_type)
            if not dt_str:
                continue

            dt = datetime.fromisoformat(dt_str)

            if (
                lower_date <= dt.date()
                and earliest_hour <= dt.hour < latest_hour
            ):
                hits.append(
                    (word.get("wordName"), exam_type, dt, places,
                     word.get("wordId"), exam.get(EXAM_ID_FIELDS.get(exam_type)))
                )

    return hits


def parse_single_center_hits(result, config, lower_date):
    """Parse OneCenterExam response.

    Unlike MultipleCentersExams, OneCenterExam returns one top-level object.
    Its examCollectionForDay contains day objects whose examCollections hold
    the actual exam records. No 31-day upper limit is applied here.
    """
    wanted_types = set(config["exam_types"])
    earliest_hour = config.get("earliest_slot_hour", 0)
    latest_hour = config.get("latest_slot_hour", 24)

    hits = []

    for day in result.get("examCollectionForDay", []):
        for exam in day.get("examCollections", []):
            word_name = (
                exam.get("organizationName")
                or f"WORD #{config['organization_ids'][0]}"
            )
            exam_type = exam.get("examType")
            if exam_type not in wanted_types:
                continue

            dt_str, places = exam_slot_fields(exam, exam_type)
            if not dt_str:
                continue

            dt = datetime.fromisoformat(dt_str)

            if (
                lower_date <= dt.date()
                and earliest_hour <= dt.hour < latest_hour
            ):
                hits.append(
                    (word_name, exam_type, dt, places,
                     config["organization_ids"][0], exam.get(EXAM_ID_FIELDS.get(exam_type)))
                )

    return hits


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
            # web.server's PAGE, which falls back to them.
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
    """Send an ntfy push and return a safe, structured delivery outcome."""
    outcome = ntfy_transport.push_ntfy(
        topic, title, message, priority=priority, tags=tags, timeout=NTFY_TIMEOUT
    )
    if outcome.ok:
        logger.info("outcome=push_sent status=%s", outcome.http_status)
    else:
        logger.info("outcome=push_failed reason=%s status=%s", outcome.kind, outcome.http_status)
    return outcome


def _handle_auth_expired(logger, dash_status, config, status, stage):
    """Shared response to an auth-failure status in either run_check() stage:
    log it, fire the critical 'session expired' notification, mark the
    dashboard status, and kick off an auto-relogin. Only the log's stage label
    and the status message differ per stage; which status codes count as an
    auth failure stays each stage's own decision (see the call sites — refresh
    also treats 404, both fold in 500)."""
    logger.info("outcome=auth_expired status=%s stage=%s", status, stage)
    if config.get("login_method", "mobywatel") != "profil_zaufany":
        notify(
            "info-kierowca: session expired",
            "Log back in via browser and update session.json",
            "critical",
        )
    update_status(dash_status, "auth_expired", f"Session expired during {stage}")
    return auth_launch.trigger_auto_refresh(logger, config)


def _auth_wait_requested(trigger_outcome):
    """Whether a due poll should wait for the in-flight relogin and retry.

    Only an actually launched helper (or one already running) can produce a
    fresh session shortly.  Backoff/disabled/manual-required outcomes must not
    turn the poll loop into a tight retry loop.
    """
    return trigger_outcome in {
        auth_launch.TRIGGER_LAUNCHED,
        auth_launch.TRIGGER_ALREADY_RUNNING,
        auth_launch.TRIGGER_MANUAL_RETRY_LAUNCHED,
        auth_launch.TRIGGER_RESTART_LAUNCHED,
    }


def _auth_pending(prior_captured_at):
    return ("auth_pending", prior_captured_at)


def should_proactively_relogin(config, captured_at, *, now=None):
    """Whether a PZ session is inside its pre-expiry automatic-login window."""
    if (config.get("login_method", "mobywatel") != "profil_zaufany"
            or not config.get("auto_refresh_chrome", True)
            or isinstance(captured_at, bool)
            or not isinstance(captured_at, (int, float))):
        return False
    if now is None:
        now = time.time()
    expires_at = captured_at + SESSION_ESTIMATED_LIFETIME_SECONDS
    remaining = expires_at - now
    return 0 < remaining <= PZ_PROACTIVE_RELOGIN_LEAD_SECONDS


def _header_value(headers, name):
    """Return one response header case-insensitively, or None."""
    if headers is None:
        return None
    try:
        return headers.get(name)
    except Exception:
        return None


def _parse_rate_limit_reset(headers):
    """Parse x-ratelimit-reset as a Unix timestamp when the server sends it."""
    raw = _header_value(headers, "x-ratelimit-reset")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # Milliseconds are tolerated defensively even though the observed API uses seconds.
    if value > 10_000_000_000:
        value /= 1000.0
    return value if value > 0 else None


def _parse_retry_after(headers, now_ts=None):
    """Return an absolute Unix timestamp from Retry-After, if usable."""
    raw = _header_value(headers, "Retry-After")
    if raw is None:
        return None
    if now_ts is None:
        now_ts = time.time()
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return now_ts + seconds if seconds >= 0 else None


def _spots_label(count):
    """Return an English slot-count label for logs and push notifications."""
    return f"{count} {'spot' if count == 1 else 'spots'}"


def _rate_limit_log_fields(headers):
    return (
        _header_value(headers, "x-ratelimit-limit"),
        _header_value(headers, "x-ratelimit-remaining"),
        _header_value(headers, "x-ratelimit-reset"),
        _header_value(headers, "Retry-After"),
    )


def _successful_search_times(dash_status, now_ts=None):
    """Keep only accepted searches still relevant to the rolling 60-minute window."""
    if now_ts is None:
        now_ts = time.time()
    cutoff = now_ts - RATE_LIMIT_WINDOW_SECONDS
    values = []
    for value in dash_status.get("successful_search_times", []):
        try:
            ts = float(value)
        except (TypeError, ValueError):
            continue
        if ts > cutoff:
            values.append(ts)
    values.sort()
    return values[-RATE_LIMIT_MAX_SEARCHES:]


def _record_successful_search(dash_status, now_ts=None):
    if now_ts is None:
        now_ts = time.time()
    values = _successful_search_times(dash_status, now_ts)
    values.append(now_ts)
    dash_status["successful_search_times"] = values[-RATE_LIMIT_MAX_SEARCHES:]
    dash_status["last_successful_search"] = datetime.fromtimestamp(now_ts).isoformat()
    dash_status["rate_limit_until"] = None


def clear_expired_rate_limit_status(dash_status, now_ts=None):
    """Drop a stale rate-limit banner once its server-defined cooldown passed.

    This is intentionally independent of whether checking is currently paused.
    A user may pause before ``rate_limit_until`` and resume after it; in that
    case the old "search resumes at ..." message must not reappear merely
    because no new search ran while paused.  Current hits are left untouched
    in status history, but are no longer presented as rate-limit-stale results.
    """
    if now_ts is None:
        now_ts = time.time()

    value = dash_status.get("rate_limit_until")
    try:
        until = float(value) if value else None
    except (TypeError, ValueError):
        until = None

    if not until or now_ts < until:
        return False

    expired_at = until
    dash_status["rate_limit_until"] = None
    if dash_status.get("outcome") == "rate_limited":
        previous = dash_status.pop("rate_limit_previous_outcome", None)
        if previous not in ("slot_found", "no_slot"):
            # Compatibility with status.json written by older builds: if the
            # last successful snapshot contains slots, restore slot_found; if
            # we know a successful search happened but it contained none,
            # restore no_slot.  Do not invent a "first check" state.
            if dash_status.get("current_hits"):
                previous = "slot_found"
            elif dash_status.get("last_successful_search"):
                previous = "no_slot"
        if previous in ("slot_found", "no_slot"):
            dash_status["outcome"] = previous
        dash_status["message"] = ""

    # If the server cooldown expired while the user deliberately had checking
    # paused, remember that fact until Resume.  The UI can then ask whether to
    # run one check immediately or begin a normal fresh polling interval.
    if dash_status.get("paused"):
        dash_status["expired_rate_limit_pending_choice"] = True
        dash_status["expired_rate_limit_at"] = expired_at
    return True


def _calculate_rate_limit_until(dash_status, headers, now_ts=None):
    """Calculate when search may resume after HTTP 429.

    Server-provided timing is authoritative.  When both x-ratelimit-reset and
    Retry-After are usable, wait until the later of the two and add a small
    safety margin.  The locally recorded accepted-search timestamps are used
    only as a fallback when the server provides no usable resume information.
    """
    if now_ts is None:
        now_ts = time.time()

    server_candidates = []

    server_reset = _parse_rate_limit_reset(headers)
    if server_reset and server_reset > now_ts:
        server_candidates.append(server_reset)

    retry_after = _parse_retry_after(headers, now_ts)
    if retry_after and retry_after > now_ts:
        server_candidates.append(retry_after)

    if server_candidates:
        return max(server_candidates) + RATE_LIMIT_SAFETY_SECONDS

    # Fallback only: preserve the local accepted-search history for cases where
    # the server omits both usable rate-limit timing headers.
    values = _successful_search_times(dash_status, now_ts)
    dash_status["successful_search_times"] = values
    if len(values) >= RATE_LIMIT_MAX_SEARCHES:
        return values[0] + RATE_LIMIT_WINDOW_SECONDS + RATE_LIMIT_SAFETY_SECONDS

    return now_ts + RATE_LIMIT_WINDOW_SECONDS + RATE_LIMIT_SAFETY_SECONDS


def run_check(logger, dash_status):
    """Note: pausing/resuming itself is applied instantly by the app module's
    /pause and /resume handlers (they write dash_status/status.json
    directly) — this check just stops the real work from running while
    paused. It deliberately leaves outcome/message untouched instead of
    overwriting them with a "paused" outcome, so status.json still holds
    the last real result underneath and Resume doesn't have to wait for a
    fresh check to stop showing "Paused".
    """
    paused = is_paused()
    status_changed = False
    if dash_status.get("paused") != paused:
        dash_status["paused"] = paused
        status_changed = True

    # Expiry is wall-clock based, not search based.  Clear a stale 429 banner
    # even while paused so resuming after the cooldown cannot resurrect an old
    # "search resumes at ..." message.
    if clear_expired_rate_limit_status(dash_status):
        status_changed = True

    if status_changed:
        save_status(dash_status)
    if paused:
        return

    # No desktop notification here: "no config yet" is the normal state during
    # first-run setup and right after the app module's Reset account, where the poll
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

    # Recomputed fresh every tick from persisted backoff state (see
    # automatic_relogin_paused()'s own docstring for why this isn't instead
    # latched from trigger_auto_refresh()'s return value in one branch below)
    # so the dashboard's "manual retry required" messaging can't go stale
    # behind an unrelated network_error/refresh_ok tick, and clears itself
    # the moment RetryBackoff.record_success() does (any successful login).
    dash_status["relogin_manual_required"] = auth_launch.automatic_relogin_paused(config)

    if not SESSION_FILE.exists():
        logger.info("outcome=auth_missing")
        dash_status["session_expires_estimate"] = None
        if config.get("login_method", "mobywatel") != "profil_zaufany":
            notify(
                "info-kierowca: no session",
                "session.json missing — log in via browser and populate cookies",
                "critical",
            )
        update_status(dash_status, "auth_expired", "session.json missing")
        trigger_outcome = auth_launch.trigger_auto_refresh(logger, config)
        if _auth_wait_requested(trigger_outcome):
            return _auth_pending(None)
        return
    required_config = {"organization_ids", "category", "profile_number", "exam_types",
                       "ntfy_topic", "current_slot_date"}
    if not required_config <= set(config):
        logger.info("outcome=setup_incomplete detail=booking_config_incomplete")
        update_status(dash_status, "setup_incomplete", "Waiting for setup to be completed")
        return
    session = load_json(SESSION_FILE)
    captured_at = session.get("captured_at")
    # Only mObywatel needs this on the dashboard: that session ends in a
    # manual QR rescan, so a countdown is a genuine heads-up. PZ relogs
    # itself in proactively (see should_proactively_relogin() above) well
    # before expiry with no action needed, so the same countdown there is
    # just clutter - same reasoning as the two login_method-gated
    # notifications above/below this function.
    dash_status["session_expires_estimate"] = (
        datetime.fromtimestamp(captured_at + SESSION_ESTIMATED_LIFETIME_SECONDS).isoformat()
        if captured_at and config.get("login_method", "mobywatel") != "profil_zaufany"
        else None
    )

    # Do this before the ordinary refresh/search calls: those calls can still
    # use the current valid session while the detached PZ login obtains its
    # replacement. Subsequent poll cycles are safely gated by the helper lock
    # and persisted retry backoff in trigger_auto_refresh().
    if should_proactively_relogin(config, captured_at):
        logger.info("outcome=proactive_relogin detail=session_expiring_soon")
        trigger_outcome = auth_launch.trigger_auto_refresh(logger, config)
        if _auth_wait_requested(trigger_outcome):
            return _auth_pending(captured_at)
        return

    # 1. Keep the session alive.
    status, body, refresh_headers = client.do_request(
        client.REFRESH_URL, session, method="GET"
    )
    # Diagnostic only: compare the server-side quota before/after authentication
    # without logging cookies, auth headers, response bodies or request data.
    refresh_limit, refresh_remaining, refresh_reset, refresh_retry_after = (
        _rate_limit_log_fields(refresh_headers)
    )
    logger.info(
        "outcome=refresh_rate_limit status=%r limit=%r remaining=%r reset=%r retry_after=%r",
        status, refresh_limit, refresh_remaining, refresh_reset, refresh_retry_after,
    )
    if status == 204:
        save_json(SESSION_FILE, session)
        logger.info("outcome=refresh_ok status=%s", status)
    elif status is None:
        # do_request returns None for URLError — i.e. we never reached the
        # server (offline, DNS, laptop lid closed). That is not an
        # "unexpected response" and must not fire a critical notification
        # every tick for the duration of an outage; the next check retries.
        logger.info("outcome=network_error stage=refresh")
        update_status(dash_status, "network_error", "Can't reach info-kierowca.pl — will retry")
        return
    elif status in (401, 403, 404, 500):
        # 500 is grouped with the real auth failures (same as the search stage
        # below): confirmed live 2026-07-18 that a 500 from the refresh endpoint
        # is just an expired-cookie response, not a transient upstream error, so
        # it must relogin rather than display as a generic "something's wrong".
        trigger_outcome = _handle_auth_expired(
            logger, dash_status, config, status, "refresh"
        )
        if _auth_wait_requested(trigger_outcome):
            return _auth_pending(captured_at)
        return
    else:
        # Other 5xx: a transient upstream error is not an expired session,
        # and must not pop a QR window onto the user's desktop.
        logger.info("outcome=unexpected status=%s stage=refresh", status)
        update_status(dash_status, "unexpected", f"Refresh call returned {status}")
        return

    # 2. Search for slots.
    #
    # single -> OneCenterExam: exactly one configured WORD.
    # multi  -> MultipleCentersExams: pad to exactly 5 WORD ids.
    # Neither mode applies the former short local start-date/result clamp.
    # A real HTTP 429 pauses only the search endpoint.  Refresh above may still
    # keep the authenticated session healthy while we wait for the rolling
    # 10-per-hour search window to free a slot.
    now_ts = time.time()
    rate_limit_until = dash_status.get("rate_limit_until")
    try:
        rate_limit_until = float(rate_limit_until) if rate_limit_until else None
    except (TypeError, ValueError):
        rate_limit_until = None
    if rate_limit_until and now_ts < rate_limit_until:
        resume_iso = datetime.fromtimestamp(rate_limit_until).isoformat()
        update_status(
            dash_status,
            "rate_limited",
            "Search rate limit reached",
        )
        dash_status["rate_limit_until"] = rate_limit_until
        save_status(dash_status)
        logger.info("outcome=rate_limited stage=search resume_at=%s", resume_iso)
        return
    if rate_limit_until:
        dash_status["rate_limit_until"] = None

    # Stay under the portal's quota instead of discovering it through a 429:
    # going over has been reported to earn a ban of several hours, far longer
    # than the 429's own reset. A rolling window over our own accepted
    # searches is never more permissive than the portal's fixed hourly one.
    recent_searches = _successful_search_times(dash_status, now_ts)
    if len(recent_searches) >= RATE_LIMIT_MAX_SEARCHES:
        resume_at = recent_searches[0] + RATE_LIMIT_WINDOW_SECONDS + RATE_LIMIT_SAFETY_SECONDS
        if dash_status.get("outcome") in ("slot_found", "no_slot"):
            dash_status["rate_limit_previous_outcome"] = dash_status.get("outcome")
        update_status(dash_status, "rate_limited", "Search rate limit reached")
        dash_status["rate_limit_until"] = resume_at
        save_status(dash_status)
        logger.info(
            "outcome=rate_limited stage=search detail=local_quota resume_at=%s",
            datetime.fromtimestamp(resume_at).isoformat(),
        )
        return

    now = datetime.now()
    search_mode = config.get("search_mode", "multi")

    if search_mode not in ("single", "multi"):
        search_mode = "multi"

    lower_date = search_start_date(
        config.get("search_start_date"),
        today=now.date(),
    )

    if search_mode == "single":
        organization_ids = list(dict.fromkeys(config["organization_ids"]))

        if len(organization_ids) != 1:
            logger.info(
                "outcome=setup_incomplete detail=single_mode_requires_one_center"
            )
            update_status(
                dash_status,
                "setup_incomplete",
                "Single-center mode requires exactly one WORD center",
            )
            return

        payload = {
            "startDate": lower_date.isoformat(),
            "organizationId": [organization_ids[0]],
            "category": config["category"],
            "profileNumber": config["profile_number"],
            "profileType": "Pkk",
        }

        search_url = client.ONE_CENTER_EXAM_URL

    else:
        payload = {
            "startDate": lower_date.isoformat(),
            "organizationId": build_search_organization_ids(config),
            "category": config["category"],
            "profileNumber": config["profile_number"],
            "profileType": "Pkk",
        }

        search_url = client.SEARCH_URL

    status, body, search_headers = client.do_request(
        search_url,
        session,
        method="POST",
        json_body=payload,
    )

    if status is None:
        logger.info("outcome=network_error stage=search")
        update_status(
            dash_status,
            "network_error",
            "Can't reach info-kierowca.pl — will retry",
        )
        return

    if status in (401, 403, 500):
        trigger_outcome = _handle_auth_expired(
            logger,
            dash_status,
            config,
            status,
            "search",
        )
        if _auth_wait_requested(trigger_outcome):
            return _auth_pending(captured_at)
        return

    if status == 429:
        # Remember the last successful dashboard state so expiry of the 429
        # banner can reveal it again instead of falling into a synthetic
        # "waiting for first check" state.
        if dash_status.get("outcome") in ("slot_found", "no_slot"):
            dash_status["rate_limit_previous_outcome"] = dash_status.get("outcome")
        limit, remaining, reset, retry_after = _rate_limit_log_fields(search_headers)
        resume_at = _calculate_rate_limit_until(dash_status, search_headers)
        dash_status["rate_limit_until"] = resume_at
        logger.info(
            "outcome=rate_limited status=429 stage=search "
            "limit=%r remaining=%r reset=%r retry_after=%r resume_at=%s",
            limit, remaining, reset, retry_after,
            datetime.fromtimestamp(resume_at).isoformat(),
        )
        update_status(dash_status, "rate_limited", "Search rate limit reached")
        # update_status intentionally preserves current_hits: 429 says nothing
        # about whether the last successfully observed slots still exist.
        dash_status["rate_limit_until"] = resume_at
        save_status(dash_status)
        return

    if status != 200:
        logger.info(
            "outcome=unexpected status=%s stage=search",
            status,
        )
        update_status(
            dash_status,
            "unexpected",
            f"Search call returned {status}",
        )
        return

    # HTTP 200 consumed one accepted search request even if its JSON later turns
    # out to be malformed, so record it before parsing.
    _record_successful_search(dash_status)
    limit, remaining, reset, retry_after = _rate_limit_log_fields(search_headers)
    logger.info(
        "outcome=search_rate_limit status=200 limit=%r remaining=%r reset=%r retry_after=%r",
        limit, remaining, reset, retry_after,
    )
    # The server says this was the last allowed search: hold off until its
    # reset rather than spending the next poll on a guaranteed 429.
    if str(remaining).strip() == "0":
        server_reset = _parse_rate_limit_reset(search_headers)
        if server_reset and server_reset > time.time():
            dash_status["rate_limit_until"] = server_reset + RATE_LIMIT_SAFETY_SECONDS

    try:
        results = json.loads(body)

        if search_mode == "single":
            if not isinstance(results, dict):
                raise ValueError("OneCenterExam response is not an object")

            hits = parse_single_center_hits(
                results,
                config,
                lower_date,
            )

        else:
            if not isinstance(results, list):
                raise ValueError(
                    "MultipleCentersExams response is not a list"
                )

            hits = parse_multi_center_hits(
                results,
                config,
                lower_date,
                now,
            )

    except Exception as exc:
        logger.info(
            "outcome=unparseable status=%s mode=%s error_type=%s",
            status,
            search_mode,
            type(exc).__name__,
        )
        notify(
            "info-kierowca: unexpected response shape",
            "Search response wasn't the expected JSON — CAPTCHA? layout change? check manually",
            "critical",
        )
        update_status(
            dash_status,
            "unexpected",
            "Response wasn't the expected JSON shape",
        )
        return

    save_json(SESSION_FILE, session)

    hits.sort(key=lambda h: h[2])
    hit_dicts = [
        {
            "word": w, "exam_type": t, "datetime": dt.isoformat(), "places": n,
            "organization_id": organization_id, "exam_id": exam_id,
        }
        for w, t, dt, n, organization_id, exam_id in hits
    ]

    if hits:
        exam_labels = {"Theoretical": "theory", "Practice": "practice"}
        lines = [
            "{} — {} · {} ({})".format(
                w,
                dt.strftime("%a %d %b %Y, %H:%M"),
                _spots_label(n),
                exam_labels.get(t, t),
            )
            for w, t, dt, n, _organization_id, _exam_id in hits
        ]
        logger.info("outcome=slot_found status=%s detail=%r", status, "; ".join(lines))

        fastest = fastest_of(hit_dicts)
        urgent = is_urgent(datetime.fromisoformat(fastest["datetime"]), config)
        if urgent:
            if push_signature(fastest) != dash_status.get("last_push_signature"):
                if config.get("phone_alerts", True):
                    push_body = "{} · {} · {}".format(
                        datetime.fromisoformat(fastest["datetime"]).strftime("%a %d %b, %H:%M"),
                        short_word(fastest["word"]),
                        _spots_label(fastest["places"]),
                    )
                    push_ntfy(
                        logger,
                        config.get("ntfy_topic"),
                        "Slot within range!",
                        push_body,
                        priority="urgent",
                    )
                    logger.info("outcome=push_sent detail=%r", fastest)
                dash_status["last_push_signature"] = push_signature(fastest)
                booking_launch.trigger_open_browser(logger, config, target_hit=fastest)
        else:
            dash_status["last_push_signature"] = None

        update_status(dash_status, "slot_found", "", hit_dicts, urgent=urgent)
    else:
        logger.info("outcome=no_slot status=%s", status)
        dash_status["last_push_signature"] = None
        update_status(dash_status, "no_slot", "", hit_dicts)


def configured_poll_interval(default=DEFAULT_POLL_INTERVAL_SECONDS):
    """Read config.json's poll_interval_seconds fresh every call, so a
    Settings save (the app module's /setup) takes effect on the very next wait
    instead of needing the poll thread restarted. `default` is only used
    when config.json doesn't exist yet or predates this setting."""
    try:
        config = load_json(CONFIG_FILE)
    except FileNotFoundError:
        return default
    seconds = config.get("poll_interval_seconds", default)
    return min(MAX_POLL_INTERVAL_SECONDS, max(MIN_POLL_INTERVAL_SECONDS, seconds))


def scheduled_wait(dash_status, interval, now_ts=None):
    """Return the exact delay until the next poll.

    The configured interval is used without jitter so the countdown is
    predictable.  A server rate-limit cooldown is a hard lower bound: if it
    extends beyond the configured interval, wait until rate_limit_until.
    """
    if now_ts is None:
        now_ts = time.time()

    rate_limit_until = dash_status.get("rate_limit_until")
    try:
        rate_limit_until = float(rate_limit_until) if rate_limit_until else None
    except (TypeError, ValueError):
        rate_limit_until = None

    if rate_limit_until and rate_limit_until > now_ts:
        return max(float(interval), rate_limit_until - now_ts)

    return float(interval)


def wait_for_auth_refresh(logger, prior_captured_at, stop_event, wake_event):
    """Wait for a relogin that blocked an already-due poll.

    Return True only when session.json was replaced with a freshly captured
    session.  A Settings wake merely causes us to re-check state; it does not
    cancel the due search.  If the auth helper exits without refreshing the
    session, return False so the ordinary poll interval/backoff applies.
    """
    logger.info("outcome=poll_waiting_for_relogin detail=search_due")
    # A newly spawned helper needs a brief moment to create its lock file.
    # Without this grace period the poller can mistake "not started yet" for
    # "already failed" and incorrectly defer the due search by a full interval.
    launch_grace_until = time.monotonic() + 3.0
    while not stop_event.is_set():
        if SESSION_FILE.exists():
            try:
                current = load_json(SESSION_FILE).get("captured_at")
            except Exception:
                current = prior_captured_at
            if current != prior_captured_at and current is not None:
                logger.info("outcome=relogin_complete detail=running_due_search")
                return True

        if (
            time.monotonic() >= launch_grace_until
            and not auth_launch.auto_refresh_in_progress()
        ):
            logger.info("outcome=relogin_incomplete detail=due_search_deferred")
            return False

        # Wake periodically to notice the new session quickly.  Settings may
        # also signal this Event; that must not replace/cancel the due search.
        wake_event.wait(1.0)
        wake_event.clear()

    return False


def loop(logger, dash_status, interval=None, stop_event=None, wake_event=None):
    """Check on a timer until stop_event is set (or forever, if none given).

    Ordinary polling uses one monotonic deadline.  Pause converts that deadline
    to a process-local ``remaining`` duration and removes the public deadline;
    Resume creates a brand-new deadline from exactly that remaining duration.
    This makes Pause a real countdown pause and prevents an old wall-clock
    ``next_check_at`` from expiring in the background.

    Server rate-limit deadlines are deliberately different: ``rate_limit_until``
    is an absolute server-defined wall-clock time and therefore keeps elapsing
    while the application is paused.

    ``interval`` is only the fallback default.  The configured value is read
    fresh before every new ordinary polling cycle.
    """
    if stop_event is None:
        stop_event = threading.Event()
    if wake_event is None:
        wake_event = threading.Event()

    default_interval = interval or DEFAULT_POLL_INTERVAL_SECONDS
    logger.info(
        "outcome=loop_start interval=%s",
        configured_poll_interval(default_interval),
    )

    # Process-local by design: restarting the application starts a fresh full
    # interval, while Pause/Resume inside one process preserves this value.
    paused_poll_remaining_s = None

    while not stop_event.is_set():
        check_result = None
        try:
            check_result = run_check(logger, dash_status)
        except Exception:
            logger.exception("outcome=crash stage=run_check")

        if (
            isinstance(check_result, tuple)
            and len(check_result) == 2
            and check_result[0] == "auth_pending"
        ):
            if wait_for_auth_refresh(logger, check_result[1], stop_event, wake_event):
                # Authentication delayed a poll that was already due.
                continue

        # A server cooldown that is active after run_check is an absolute
        # wall-clock wait.  Ordinary waits are relative monotonic countdowns.
        value = dash_status.get("rate_limit_until")
        try:
            rate_limit_until = float(value) if value else None
        except (TypeError, ValueError):
            rate_limit_until = None
        now_wall = time.time()
        rate_limit_active = bool(rate_limit_until and rate_limit_until > now_wall)

        if rate_limit_active:
            wait_s = max(0.0, rate_limit_until - now_wall)
            wait_kind = "rate_limit"
            # A server cooldown supersedes any ordinary pre-pause countdown.
            paused_poll_remaining_s = None
        elif paused_poll_remaining_s is not None:
            wait_s = max(0.0, float(paused_poll_remaining_s))
            wait_kind = "ordinary"
            paused_poll_remaining_s = None
        else:
            wait_s = float(configured_poll_interval(default_interval))
            wait_kind = "ordinary"

        # Monotonic time is authoritative for ordinary countdown arithmetic;
        # wall time exists only to render the dashboard's next_check_at.
        deadline_mono = time.monotonic() + wait_s

        while not stop_event.is_set():
            if wait_kind == "rate_limit":
                # Never freeze a server deadline.  Its remaining duration is
                # recalculated from wall clock even while Pause is active.
                remaining = max(0.0, rate_limit_until - time.time())
                if remaining <= 0:
                    dash_status["next_check_at"] = None
                    save_status(dash_status)
                    # run_check() clears the expired banner and, if still
                    # paused, records that Resume needs the check-now/wait UI.
                    break

                dash_status["next_check_at"] = datetime.fromtimestamp(
                    rate_limit_until
                ).isoformat()
                save_status(dash_status)

                wake_event.clear()
                # Re-read durable state after clear() so a Pause/Resume edge
                # racing with it cannot be lost.
                woke = wake_event.wait(remaining)
                if stop_event.is_set():
                    break
                if not woke:
                    continue

                if dash_status.pop("force_check_now", False):
                    logger.info("outcome=poll_due detail=expired_rate_limit_user_check_now")
                    dash_status["next_check_at"] = None
                    save_status(dash_status)
                    break

                # Settings may change the ordinary polling interval while a
                # server cooldown is active, but rate-limit time is absolute
                # and must not be reset.  Consume the local reset request; the
                # new configured interval will naturally apply after the next
                # successful/due check.
                dash_status.pop("poll_interval_reset_to", None)
                logger.info("outcome=poll_rescheduled detail=pause_state_changed")
                # Pause/Resume does not alter rate_limit_until.  Recalculate
                # its wall-clock remainder at the top of this loop.
                continue

            # ----- ordinary local countdown -----
            if is_paused():
                # Capture exactly once when entering Pause.  From this point
                # there is no active ordinary deadline at all.
                if paused_poll_remaining_s is None:
                    paused_poll_remaining_s = max(
                        0.0, deadline_mono - time.monotonic()
                    )
                    logger.info(
                        "outcome=poll_rescheduled detail=paused remaining=%.3f",
                        paused_poll_remaining_s,
                    )

                dash_status["next_check_at"] = None
                save_status(dash_status)

                wake_event.clear()
                if not is_paused():
                    # Resume raced with clear(); do not block.
                    continue
                # Bounded: the app's Resume sets wake_event, but the standalone
                # --loop has nothing that would, so re-check the pause file
                # periodically rather than block forever.
                wake_event.wait(PAUSE_RECHECK_SECONDS)
                if stop_event.is_set():
                    break
                if is_paused():
                    # While paused, only an actual interval change replaces
                    # the frozen remainder.  Other Settings saves leave it
                    # untouched.
                    reset_to = dash_status.pop("poll_interval_reset_to", None)
                    if reset_to is not None:
                        paused_poll_remaining_s = max(0.0, float(reset_to))
                        logger.info(
                            "outcome=poll_rescheduled detail=interval_changed_paused "
                            "remaining=%.3f", paused_poll_remaining_s
                        )
                    continue

                logger.info("outcome=poll_rescheduled detail=pause_state_changed")
                # Resume always creates a *new* deadline from the frozen
                # relative duration.  The old deadline is discarded forever.
                deadline_mono = time.monotonic() + max(
                    0.0, float(paused_poll_remaining_s or 0.0)
                )
                paused_poll_remaining_s = None

            if dash_status.pop("force_check_now", False):
                logger.info("outcome=poll_due detail=expired_rate_limit_user_check_now")
                dash_status["next_check_at"] = None
                save_status(dash_status)
                break

            remaining = max(0.0, deadline_mono - time.monotonic())
            if remaining <= 0:
                dash_status["next_check_at"] = None
                save_status(dash_status)
                break

            # Derive the public wall-clock timestamp afresh from the one true
            # monotonic remaining duration.  It can therefore never retain a
            # pre-pause deadline after Resume.
            dash_status["next_check_at"] = datetime.fromtimestamp(
                time.time() + remaining
            ).isoformat()
            save_status(dash_status)

            wake_event.clear()
            if is_paused():
                # Pause raced with clear(); loop immediately and freeze from
                # the monotonic deadline rather than sleeping through it.
                continue

            woke = wake_event.wait(remaining)
            if stop_event.is_set():
                break
            if not woke:
                continue

            if is_paused():
                # The next iteration captures the exact monotonic remainder.
                continue

            if dash_status.pop("force_check_now", False):
                logger.info("outcome=poll_due detail=expired_rate_limit_user_check_now")
                dash_status["next_check_at"] = None
                save_status(dash_status)
                break

            # A Settings save resets the local countdown only when the polling
            # interval itself actually changed.  Unrelated saves preserve the
            # existing monotonic deadline.
            reset_to = dash_status.pop("poll_interval_reset_to", None)
            if reset_to is not None:
                new_interval = max(0.0, float(reset_to))
                deadline_mono = time.monotonic() + new_interval
                logger.info(
                    "outcome=poll_rescheduled detail=interval_changed "
                    "remaining=%.3f", new_interval
                )
            else:
                logger.info("outcome=poll_rescheduled detail=wake_event")


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
            "(set via the app module's Settings page)."
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
