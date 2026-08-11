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

# Profil Zaufany can refresh without user interaction, so start shortly before
# the observed absolute session ceiling instead of waiting for the API to reject
# the old cookies. Five minutes leaves room for the normal 60s and 120s retry
# cooldowns when failures are detected quickly. mObywatel deliberately remains
# expiry-driven because an early refresh would ask the user to scan a QR code.
PZ_PROACTIVE_RELOGIN_LEAD_SECONDS = 5 * 60

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

# The site itself won't show slots further out than this, so there's no
# benefit to making it configurable — it's a hard line on info-kierowca.pl,
# not a user preference.
MAX_DAYS_AHEAD = 31


def search_start_date(value, *, today=None):
    """Return the safe lower search bound for a config value.

    This is intentionally separate from ``current_slot_date``: it selects
    which available dates are worth considering, rather than deciding whether
    a slot improves the user's existing booking. Missing or malformed values
    preserve the historical behaviour of searching from today.
    """
    today = today or date.today()
    horizon = today + timedelta(days=MAX_DAYS_AHEAD)
    if not value:
        return today
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None:
        return today
    return min(max(parsed, today), horizon)

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
    auth_launch.trigger_auto_refresh(logger, config)


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
    if dash_status.get("paused") != paused:
        dash_status["paused"] = paused
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
        auth_launch.trigger_auto_refresh(logger, config)
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
        auth_launch.trigger_auto_refresh(logger, config)

    # 1. Keep the session alive.
    status, body, _headers = client.do_request(client.REFRESH_URL, session, method="GET")
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

    # 2. Search for slots. The API gets this lower bound, and the local filter
    # below applies it again in case the endpoint returns an older slot.
    now = datetime.now()
    lower_date = search_start_date(config.get("search_start_date"), today=now.date())
    payload = {
        "startDate": lower_date.isoformat(),
        "organizationId": build_search_organization_ids(config),
        "category": config["category"],
        "profileNumber": config["profile_number"],
        "profileType": "Pkk",
    }
    status, body, _headers = client.do_request(
        client.SEARCH_URL, session, method="POST", json_body=payload
    )

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

    max_date = now + timedelta(days=MAX_DAYS_AHEAD)
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
            dt_str, places = exam_slot_fields(exam, exam_type)
            if not dt_str:
                continue
            dt = datetime.fromisoformat(dt_str)
            if lower_date <= dt.date() and dt <= max_date and earliest_hour <= dt.hour < latest_hour:
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
            if push_signature(fastest) != dash_status.get("last_push_signature"):
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


def jittered_wait(interval):
    return interval + random.uniform(0, interval * POLL_JITTER_FRACTION)


def loop(logger, dash_status, interval=None, stop_event=None, wake_event=None):
    """Check on a timer until stop_event is set (or forever, if none given).

    Factored out of main()'s --loop branch so the app module can run this in a
    background thread instead of shelling out to `python notifier.py --loop`
    as a subprocess — stop_event lets that thread be told to exit cleanly.

    `interval` is only the fallback default passed to configured_poll_interval()
    each cycle (e.g. from --interval on the CLI); once config.json has its own
    poll_interval_seconds, that value wins.

    `wake_event`, if given, lets the app module's /setup handler cut the current wait
    short the instant a new poll_interval_seconds is saved, instead of the
    dashboard's countdown (and the actual next check) staying stuck on
    whatever interval was configured when this cycle's wait started.

    It's cleared right *before* each wait rather than right after — a save
    that lands while run_check() is still running would otherwise survive
    to the wait() call below unconsumed (Event.wait() returning early
    doesn't itself clear the flag), making it return instantly and then get
    cleared by the old post-wait clear(), triggering one wasted immediate
    extra check even though this cycle's wait_s already reflects the fresh
    config (configured_poll_interval() re-reads config.json every call).
    Clearing right before wait() discards exactly that stale signal while
    still catching a save that happens *during* the wait itself: wait()
    returns early as intended, and the flag it leaves set is what the next
    loop iteration's pre-wait clear() consumes.
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
        wake_event.clear()
        wake_event.wait(wait_s)


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
