"""Launch and rate-limit the detached reschedule helper."""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from info_kierowca_notifier.booking import reschedule as open_logged_in_browser
from info_kierowca_notifier.browser import chrome
from info_kierowca_notifier.paths import (
    RESCHEDULE_CONFIRM_COOLDOWN_FILE,
    RESCHEDULE_LOG_FILE,
)

TRIGGER_DISABLED = "disabled"
TRIGGER_NO_BROWSER = "no_chromium_browser"
TRIGGER_ALREADY_RUNNING = "already_running"
TRIGGER_LAUNCHED = "launched"
TRIGGER_LAUNCH_FAILED = "launch_failed"

OPEN_BROWSER_SCRIPT = Path(open_logged_in_browser.__file__)
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
        raw = RESCHEDULE_CONFIRM_COOLDOWN_FILE.read_text().strip()
        if raw.startswith("{"):
            raw = json.loads(raw)["attempted_at"]
        last = datetime.fromisoformat(raw)
    except (FileNotFoundError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return (datetime.now() - last).total_seconds() < RESCHEDULE_CONFIRM_COOLDOWN_SECONDS


def _restrict_log_permissions(path):
    """Owner-only, like every other file this project writes under STATE_DIR.

    RESCHEDULE_LOG_FILE was created by a plain open(..., "a") and so inherited
    the umask (0644 on a typical desktop) while config.json, session.json,
    status.json and the diagnostic traces are all 0600. It records the exam
    slot being chased, the centre, and every abstention reason from the click
    helpers — not credentials, but not something to leave world-readable on a
    shared machine either. Best-effort: a failure here must never stop the
    launch it is only annotating.
    """
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def trigger_open_browser(logger, config, auto_click=True, target_hit=None):
    """Best-effort: launch booking.reschedule so a pre-authenticated
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
    through as --target-slot JSON so booking.reschedule can also try
    to expand that date's slot group, select the matching time radio
    button, and click through to the summary/review screen, past the plain
    date-picker screen.

    A second, separate, also default-off flag — config's
    "auto_confirm_reschedule" — additionally passes --confirm-reschedule,
    which (only once auto_select_slot has already landed on the summary
    screen, and only after booking.reschedule itself re-verifies
    that screen matches the intended slot) clicks through the final
    "Potwierdź i przejdź dalej" confirm button — actually submitting the
    reservation change. auto_confirm_reschedule alone, without
    auto_select_slot, does nothing (no --target-slot means
    booking.reschedule never reaches that screen to confirm on).
    UNVERIFIED against the live site as of 2026-07-20, by explicit user
    request that same day — see booking.reschedule's own docstrings
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
    reach anyone, and booking.reschedule's own print()s are the only
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
    if not chrome.chrome_available():
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
            # The cooldown is armed HERE, before the child exists — not by the
            # child right before its submit click, which is where it used to be
            # written. That left roughly a minute and a half (Chrome start,
            # /cases load, baseline capture, up to four 20s click waits) in
            # which the only thing standing between two concurrent confirm
            # flows was the port-9555 probe above — and that probe fails *open*
            # for the whole of Chrome's startup, so a second poll cycle in that
            # window happily launched a duplicate. Arming first means the
            # window is closed from the instant we decide to try.
            #
            # The cost is that a run which never reaches the submit click would
            # burn 15 minutes of confirm attempts for nothing, so
            # reschedule.try_select_target_slot() calls release_confirm_cooldown()
            # on every path that gives up before submitting. A crash between
            # here and there leaves the gate armed, which is the safe direction.
            elif not open_logged_in_browser.record_confirm_cooldown("LAUNCHED"):
                logger.info("outcome=confirm_reschedule_skipped detail=cooldown_unwritable")
            else:
                cmd.append("--confirm-reschedule")
    try:
        RESCHEDULE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RESCHEDULE_LOG_FILE, "a") as logf:
            _restrict_log_permissions(RESCHEDULE_LOG_FILE)
            logf.write(f"\n--- {datetime.now().isoformat()} launching: {cmd!r} ---\n")
            logf.flush()
            subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        logger.info("outcome=open_browser_launched")
        return TRIGGER_LAUNCHED
    except Exception as e:
        logger.info("outcome=open_browser_launch_failed detail=%r", str(e))
        return TRIGGER_LAUNCH_FAILED


