#!/usr/bin/env python3
"""Notification-only slot checker for info-kierowca.pl.

Never books or reserves anything. Reads two endpoints only:
  - GET  /bknd/auth/api/v1/jwt/refresh                       (keep session alive)
  - POST /bknd/exam/api/v1/Schedules/user/MultipleCentersExams (read slot data)
"""
import argparse
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
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

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
MAX_HISTORY = 200

WORD_CENTERS_FILE = Path(__file__).parent / "word_centers.json"

# The search endpoint rejects anything but exactly 5 organizationIds
# ("Exactly 5 exam centers must be provided when searching for the fastest
# terms"), even though the user may only want to watch 1-2. The extra slots
# are padded with other real center ids the user doesn't care about — their
# results are discarded below by the watch_organization_ids filter, so which
# ones they are doesn't matter.
SEARCH_ORG_ID_COUNT = 5


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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)
    path.chmod(0o600)


def load_status():
    if STATUS_FILE.exists():
        try:
            return load_json(STATUS_FILE)
        except Exception:
            pass
    return {
        "last_check": None,
        "outcome": None,
        "message": "",
        "current_hits": [],
        "history": [],
    }


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
    """Whether fastest_dt is on or before the date of the user's current slot.

    Inclusive: a slot on the same day still counts, since that's a same-day
    time change rather than an earlier date.
    """
    current_slot_date = config["current_slot_date"]
    cutoff = datetime.fromisoformat(current_slot_date).replace(
        hour=23, minute=59, second=59
    )
    return fastest_dt <= cutoff


def update_status(status, outcome, message="", current_hits=None, urgent=False):
    status["last_check"] = datetime.now().isoformat()
    status["outcome"] = outcome
    status["message"] = message
    status["urgent"] = urgent
    if current_hits is not None:
        status["current_hits"] = current_hits
        signature = fastest_of(current_hits)
        if signature != status.get("last_signature"):
            status.setdefault("history", []).append(
                {"seen_at": status["last_check"], "hits": current_hits}
            )
            status["history"] = status["history"][-MAX_HISTORY:]
            status["last_signature"] = signature
    save_status(status)


def notify(summary, body, urgency="normal"):
    """Desktop notification via notify-send. Linux only — no-op if it's missing."""
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
AUTO_REFRESH_LOCK = STATE_DIR / "auto-refresh.lock"


def trigger_auto_refresh(logger, config, force=False):
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

    force=True (the manual "Log in" button) kills whatever's holding the
    lock and relaunches anyway. This exists because the lock has no timeout
    (auto_refresh_session.py waits indefinitely for a QR scan) and survives
    app.py restarts, since the Chrome+QR process it guards is detached —
    the most common way this bites someone is a QR window left open and
    forgotten from a previous session (confirmed live: a lock stayed held
    for ~10 hours), which silently no-ops every later auto-trigger,
    including the very next app launch, with no visible sign why. The
    automatic path stays conservative (never force); force is opt-in so a
    background retry never kills a window the user is mid-scan on.

    Returns a short status string: "disabled", "already_running",
    "launched", or "launch_failed".
    """
    if not config.get("auto_refresh_chrome", True):
        return "disabled"
    if AUTO_REFRESH_LOCK.exists():
        try:
            pid = int(AUTO_REFRESH_LOCK.read_text().strip())
            os.kill(pid, 0)
            if not force:
                logger.info("outcome=auto_refresh_skipped detail=already_running pid=%s", pid)
                return "already_running"
            logger.info("outcome=auto_refresh_force_restart detail=killing_stale pid=%s", pid)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        except ValueError:
            pass  # stale lock — let auto_refresh_session.py sort it out
        except OSError:
            pass  # pid is gone — stale lock, safe to relaunch
        AUTO_REFRESH_LOCK.unlink(missing_ok=True)
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--internal-auto-refresh"]
    else:
        if not AUTO_REFRESH_SCRIPT.exists():
            return "launch_failed"
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
    try:
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        logger.info("outcome=auto_refresh_launched")
        return "launched"
    except Exception as e:
        logger.info("outcome=auto_refresh_launch_failed detail=%r", str(e))
        return "launch_failed"


OPEN_BROWSER_SCRIPT = Path(__file__).parent / "open_logged_in_browser.py"
# Must match open_logged_in_browser.py's DEFAULT_PORT.
OPEN_BROWSER_PORT = 9555


def trigger_open_browser(logger, config):
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

    Returns a short status string: "disabled", "already_running",
    "launched", or "launch_failed". No force option here (unlike
    trigger_auto_refresh) — forcing would mean launching a second Chrome
    on the same fixed debug port an already-open one is using, which is
    fragile rather than useful; if one's already open that's already the
    outcome a caller wants.
    """
    if not config.get("auto_open_browser", True):
        return "disabled"
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{OPEN_BROWSER_PORT}/json/version", timeout=1)
        logger.info("outcome=open_browser_skipped detail=already_running")
        return "already_running"
    except Exception:
        pass  # nothing listening on that port -> safe to launch
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--internal-open-browser"]
    else:
        if not OPEN_BROWSER_SCRIPT.exists():
            return "launch_failed"
        cmd = [sys.executable, str(OPEN_BROWSER_SCRIPT)]
    try:
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        logger.info("outcome=open_browser_launched")
        return "launched"
    except Exception as e:
        logger.info("outcome=open_browser_launch_failed detail=%r", str(e))
        return "launch_failed"


def cookie_header(session):
    return "; ".join(f"{k}={v}" for k, v in session.get("cookies", {}).items())


def parse_set_cookies(headers, session):
    if headers is None:
        return
    for raw in headers.get_all("Set-Cookie") or []:
        name, _, rest = raw.partition("=")
        value = rest.split(";", 1)[0]
        session.setdefault("cookies", {})[name.strip()] = value


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


def run_check(logger, dash_status):
    if is_paused():
        dash_status["paused"] = True
        update_status(dash_status, "paused", "Paused — click Resume to keep checking")
        return
    dash_status["paused"] = False

    if not CONFIG_FILE.exists():
        logger.info("outcome=fatal detail=missing_config")
        notify("info-kierowca: setup incomplete", f"Missing {CONFIG_FILE}", "critical")
        update_status(dash_status, "unexpected", f"Missing {CONFIG_FILE}")
        return
    config = load_json(CONFIG_FILE)

    if not SESSION_FILE.exists():
        logger.info("outcome=auth_missing")
        notify(
            "info-kierowca: no session",
            "session.json missing — log in via browser and populate cookies",
            "critical",
        )
        update_status(dash_status, "auth_expired", "session.json missing")
        trigger_auto_refresh(logger, config)
        return
    session = load_json(SESSION_FILE)

    # 1. Keep the session alive.
    status, body, headers = do_request(REFRESH_URL, session, method="GET")
    if status == 204:
        save_json(SESSION_FILE, session)
        logger.info("outcome=refresh_ok status=%s", status)
    elif status in (401, 403, 404, 500):
        logger.info("outcome=auth_expired status=%s stage=refresh", status)
        notify(
            "info-kierowca: session expired",
            "Log back in via browser and update session.json",
            "critical",
        )
        update_status(dash_status, "auth_expired", "Session expired during refresh")
        trigger_auto_refresh(logger, config)
        return
    else:
        detail = body[:200].decode(errors="replace") if body else ""
        logger.info("outcome=unexpected status=%s stage=refresh detail=%r", status, detail)
        notify(
            "info-kierowca: unexpected response",
            f"Refresh call returned {status} — check manually",
            "critical",
        )
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
    status, body, headers = do_request(SEARCH_URL, session, method="POST", json_body=payload)

    if status in (401, 403, 500):
        logger.info("outcome=auth_expired status=%s stage=search", status)
        notify(
            "info-kierowca: session expired",
            "Log back in via browser and update session.json",
            "critical",
        )
        update_status(dash_status, "auth_expired", "Session expired during search")
        trigger_auto_refresh(logger, config)
        return
    if status != 200:
        detail = body[:200].decode(errors="replace") if body else ""
        logger.info("outcome=unexpected status=%s stage=search detail=%r", status, detail)
        notify(
            "info-kierowca: unexpected response",
            f"Search call returned {status} — check manually",
            "critical",
        )
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
    watch_ids = set(config.get("watch_organization_ids", config["organization_ids"]))
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
            if dt <= max_date:
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
                trigger_open_browser(logger, config)
        else:
            dash_status["last_push_signature"] = None

        update_status(dash_status, "slot_found", "", hit_dicts, urgent=urgent)
    else:
        logger.info("outcome=no_slot status=%s", status)
        dash_status["last_push_signature"] = None
        update_status(dash_status, "no_slot", "", hit_dicts)


def loop(logger, dash_status, interval, stop_event=None):
    """Check on a timer until stop_event is set (or forever, if none given).

    Factored out of main()'s --loop branch so app.py can run this in a
    background thread instead of shelling out to `python notifier.py --loop`
    as a subprocess — stop_event lets that thread be told to exit cleanly.
    """
    if stop_event is None:
        stop_event = threading.Event()
    logger.info("outcome=loop_start interval=%s", interval)
    while not stop_event.is_set():
        try:
            run_check(logger, dash_status)
        except Exception:
            logger.exception("outcome=crash stage=run_check")
        stop_event.wait(interval)


def main():
    parser = argparse.ArgumentParser(description="info-kierowca.pl slot checker")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously instead of once — no systemd/cron/Task Scheduler needed",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between checks in --loop mode (default: 60)",
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
