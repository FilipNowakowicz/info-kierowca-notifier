"""Launch and control the detached authentication helper."""
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from info_kierowca_notifier.auth import relogin_control
from info_kierowca_notifier.auth import session as auto_refresh_session
from info_kierowca_notifier.auth.relogin_backoff import RetryBackoff
from info_kierowca_notifier.browser import chrome
from info_kierowca_notifier.paths import (
    AUTO_REFRESH_LOG_FILE,
    AUTO_REFRESH_RESTART_REQUEST,
    RELOGIN_BACKOFF_FILE,
    ensure_state_dir,
)

AUTO_REFRESH_SCRIPT = Path(auto_refresh_session.__file__)
AUTO_REFRESH_LOCK = auto_refresh_session.LOCK_FILE


# Outcome vocabulary returned by the authentication launch/control functions.
# Named constants — rather than bare string literals
# re-spelled in the app module's login handlers — so the producer and every consumer
# key off one source; a renamed or added outcome is then a grep away instead of
# a silent fall-through to a generic message. TRIGGER_OUTCOMES is the full set
# of authentication outcomes.
TRIGGER_DISABLED = "disabled"
TRIGGER_NO_BROWSER = "no_chromium_browser"
TRIGGER_ALREADY_RUNNING = "already_running"
TRIGGER_BACKOFF_ACTIVE = "backoff_active"
TRIGGER_LAUNCHED = "launched"
TRIGGER_MANUAL_RETRY_LAUNCHED = "manual_retry_launched"
TRIGGER_RESTART_LAUNCHED = "restart_launched"
TRIGGER_RESTART_UNAVAILABLE = "restart_unavailable"
TRIGGER_SHUTDOWN_FAILED = "shutdown_failed"
TRIGGER_LAUNCH_FAILED = "launch_failed"
TRIGGER_OUTCOMES = (
    TRIGGER_DISABLED,
    TRIGGER_NO_BROWSER,
    TRIGGER_ALREADY_RUNNING,
    TRIGGER_BACKOFF_ACTIVE,
    TRIGGER_LAUNCHED,
    TRIGGER_MANUAL_RETRY_LAUNCHED,
    TRIGGER_RESTART_LAUNCHED,
    TRIGGER_RESTART_UNAVAILABLE,
    TRIGGER_SHUTDOWN_FAILED,
    TRIGGER_LAUNCH_FAILED,
)


def trigger_auto_refresh(logger, config, force=False, notify_phone=True):
    """Best-effort: launch auth.session to relogin via Chrome+QR.

    Detached so it survives this (oneshot) process exiting — on systemd it's
    handed off via `systemd-run --user` so it isn't killed when this unit's
    cgroup is torn down at exit; elsewhere a plain detached Popen is enough.
    Guarded by auth.session's own lock file so a stuck relogin
    doesn't get relaunched on every subsequent 60s tick.

    Inside a PyInstaller-frozen build, sys.executable is the bundled binary
    itself (not a Python interpreter that can run a loose .py file) and
    AUTO_REFRESH_SCRIPT has no file on disk to point at — so instead we
    re-invoke the binary with a hidden flag that the app module dispatches straight
    to auto_refresh_session.main(), keeping it a separate detached process.

    force=True denotes a deliberate manual retry. It bypasses only persisted
    automatic-failure backoff; it never replaces an active QR-login process.
    A live lock always returns ``already_running`` so a second click cannot
    close a browser while the person is mid-scan. Dead or malformed lock files
    are still removed before launching a new process.

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
    automatic = not force
    backoff = RetryBackoff(RELOGIN_BACKOFF_FILE)
    if not config.get("auto_refresh_chrome", True):
        return TRIGGER_DISABLED
    remaining = backoff.cooldown_remaining(manual=force)
    if remaining:
        logger.info(
            "outcome=auto_refresh_skipped detail=backoff_active remaining_seconds=%d",
            remaining,
        )
        return TRIGGER_BACKOFF_ACTIVE
    if not chrome.chrome_available():
        logger.info("outcome=auto_refresh_no_browser detail=no_chromium_found")
        if automatic:
            backoff.record_failure("browser_unavailable")
        return TRIGGER_NO_BROWSER
    if AUTO_REFRESH_LOCK.exists():
        pid = relogin_control.lock_pid(AUTO_REFRESH_LOCK)
        if pid is not None and relogin_control.process_alive(pid):
            logger.info("outcome=auto_refresh_skipped detail=already_running pid=%s", pid)
            return TRIGGER_ALREADY_RUNNING
        # Malformed or stale lock — safe to remove before launching.
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
            ]
            # systemd-run starts a new user-service environment rather than
            # inheriting a caller's runtime overrides.  Keep the helper in
            # the same state home as this notifier: otherwise a preview or
            # alternate HOME writes its lock/session under the regular user
            # home while the app module polls the alternate location and concludes
            # that Chrome closed before a QR scan.
            for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
                if value := os.environ.get(name):
                    cmd.append(f"--setenv={name}={value}")
            cmd.extend([python, str(AUTO_REFRESH_SCRIPT)])
        else:
            cmd = [python, str(AUTO_REFRESH_SCRIPT)]
    if not notify_phone:
        cmd.append("--no-phone-push")
    if automatic:
        cmd.append("--automatic")
    try:
        ensure_state_dir()
        # os.open()'s own mode applies before any data is written, matching
        # notifier.save_json()'s pattern; the explicit chmod after also
        # backfills a log file created before this fix existed (mode is only
        # honored by O_CREAT when the file doesn't already exist).
        fd = os.open(AUTO_REFRESH_LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(AUTO_REFRESH_LOG_FILE, 0o600)
        with os.fdopen(fd, "a") as logf:
            logf.write(f"\n--- {datetime.now().isoformat()} launching: {cmd!r} ---\n")
            logf.flush()
            subprocess.Popen(
                cmd, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True
            )
        logger.info("outcome=auto_refresh_launched")
        return TRIGGER_MANUAL_RETRY_LAUNCHED if force else TRIGGER_LAUNCHED
    except Exception as e:
        logger.info("outcome=auto_refresh_launch_failed detail=%r", str(e))
        if automatic:
            backoff.record_failure("launch_failed")
        return TRIGGER_LAUNCH_FAILED


def restart_auto_refresh(
    logger, config, *, timeout=8, clock=time.monotonic, wall_clock=time.time,
    sleeper=time.sleep
):
    """Cooperatively replace a known active QR relogin helper.

    The active helper owns and terminates its Chrome child. This function only
    writes that helper's unguessable request token and waits for its lock to be
    released. It never signals a PID read from disk, and it never starts a
    replacement while the prior owner may still hold the profile/debug port.
    Older live PID-only locks therefore fail conservatively until that helper
    exits normally.
    """
    if not config.get("auto_refresh_chrome", True):
        return TRIGGER_DISABLED
    if not chrome.chrome_available():
        return TRIGGER_NO_BROWSER

    if not AUTO_REFRESH_LOCK.exists():
        outcome = trigger_auto_refresh(logger, config, force=True, notify_phone=False)
        return TRIGGER_RESTART_LAUNCHED if outcome == TRIGGER_MANUAL_RETRY_LAUNCHED else outcome

    owner = relogin_control.read_lock(AUTO_REFRESH_LOCK)
    pid = relogin_control.lock_pid(AUTO_REFRESH_LOCK)
    if pid is None or not relogin_control.process_alive(pid):
        AUTO_REFRESH_LOCK.unlink(missing_ok=True)
        outcome = trigger_auto_refresh(logger, config, force=True, notify_phone=False)
        return TRIGGER_RESTART_LAUNCHED if outcome == TRIGGER_MANUAL_RETRY_LAUNCHED else outcome
    if owner is None:
        logger.info("outcome=auto_refresh_restart_refused detail=unverifiable_legacy_lock")
        return TRIGGER_RESTART_UNAVAILABLE

    issued_at = wall_clock()
    relogin_control.write_restart_request(
        AUTO_REFRESH_RESTART_REQUEST, owner,
        issued_at=issued_at, expires_at=issued_at + timeout
    )
    deadline = clock() + timeout
    while clock() < deadline:
        lock_exists = AUTO_REFRESH_LOCK.exists()
        if lock_exists and relogin_control.read_lock(AUTO_REFRESH_LOCK) != owner:
            logger.info("outcome=auto_refresh_restart_failed detail=lock_owner_changed")
            relogin_control.clear_restart_request_if_owned(
                AUTO_REFRESH_RESTART_REQUEST, owner
            )
            return TRIGGER_SHUTDOWN_FAILED
        if not relogin_control.process_alive(owner.pid):
            if lock_exists:
                relogin_control.clear_if_owned(AUTO_REFRESH_LOCK, owner)
            break
        sleeper(min(0.1, max(0, deadline - clock())))
    if AUTO_REFRESH_LOCK.exists() or relogin_control.process_alive(owner.pid):
        logger.info("outcome=auto_refresh_restart_failed detail=shutdown_timeout")
        relogin_control.clear_restart_request_if_owned(
            AUTO_REFRESH_RESTART_REQUEST, owner
        )
        return TRIGGER_SHUTDOWN_FAILED

    relogin_control.clear_restart_request_if_owned(AUTO_REFRESH_RESTART_REQUEST, owner)
    outcome = trigger_auto_refresh(logger, config, force=True, notify_phone=False)
    if outcome == TRIGGER_MANUAL_RETRY_LAUNCHED:
        logger.info("outcome=auto_refresh_restart_launched")
        return TRIGGER_RESTART_LAUNCHED
    return outcome


def auto_refresh_in_progress():
    """Whether a launched auth.session helper is still alive and holding
    AUTO_REFRESH_LOCK — used by the app module's login screen to tell "still waiting
    on you to scan" apart from "Chrome was closed/crashed before you scanned,
    give up waiting and let the user retry" (see wait_for_cookies's docstring
    in auth.session: that process releases the lock and exits the
    moment its own Chrome disappears, whether from a scan, a close, or a
    crash — so the lock's liveness is exactly the signal we need here).
    """
    if not AUTO_REFRESH_LOCK.exists():
        return False
    pid = relogin_control.lock_pid(AUTO_REFRESH_LOCK)
    return pid is not None and relogin_control.process_alive(pid)

