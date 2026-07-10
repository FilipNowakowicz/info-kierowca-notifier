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
import subprocess
import time
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
MAX_HISTORY = 200

BASE = "https://info-kierowca.pl"
REFRESH_URL = f"{BASE}/bknd/auth/api/v1/jwt/refresh"
SEARCH_URL = f"{BASE}/bknd/exam/api/v1/Schedules/user/MultipleCentersExams"

NTFY_URL = "https://ntfy.sh"

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


def fastest_of(hits):
    return min(hits, key=lambda h: h["datetime"]) if hits else None


def short_word(name):
    prefix = "WORD Warszawa M/E "
    return name[len(prefix):] if name.startswith(prefix) else name


def update_status(status, outcome, message="", current_hits=None):
    status["last_check"] = datetime.now().isoformat()
    status["outcome"] = outcome
    status["message"] = message
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
        return
    session = load_json(SESSION_FILE)

    # 1. Keep the session alive.
    status, body, headers = do_request(REFRESH_URL, session, method="GET")
    if status == 204:
        save_json(SESSION_FILE, session)
        logger.info("outcome=refresh_ok status=%s", status)
    elif status in (401, 403, 404):
        logger.info("outcome=auth_expired status=%s stage=refresh", status)
        notify(
            "info-kierowca: session expired",
            "Log back in via browser and update session.json",
            "critical",
        )
        update_status(dash_status, "auth_expired", "Session expired during refresh")
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
        "organizationId": config["organization_ids"],
        "category": config["category"],
        "profileNumber": config["profile_number"],
        "profileType": "Pkk",
    }
    status, body, headers = do_request(SEARCH_URL, session, method="POST", json_body=payload)

    if status in (401, 403):
        logger.info("outcome=auth_expired status=%s stage=search", status)
        notify(
            "info-kierowca: session expired",
            "Log back in via browser and update session.json",
            "critical",
        )
        update_status(dash_status, "auth_expired", "Session expired during search")
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

    max_date = datetime.now() + timedelta(days=config["max_days_ahead"])
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
        days_until = (datetime.fromisoformat(fastest["datetime"]) - datetime.now()).total_seconds() / 86400
        push_threshold = config.get("push_below_days", 10)
        if days_until <= push_threshold:
            if fastest != dash_status.get("last_push_signature"):
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
                    tags=["rotating_light"],
                )
                logger.info("outcome=push_sent detail=%r", fastest)
                dash_status["last_push_signature"] = fastest
        else:
            dash_status["last_push_signature"] = None

        update_status(dash_status, "slot_found", "", hit_dicts)
    else:
        logger.info("outcome=no_slot status=%s", status)
        dash_status["last_push_signature"] = None
        update_status(dash_status, "no_slot", "", hit_dicts)


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
        logger.info("outcome=loop_start interval=%s", args.interval)
        while True:
            try:
                run_check(logger, dash_status)
            except Exception:
                logger.exception("outcome=crash stage=run_check")
            time.sleep(args.interval)
    else:
        run_check(logger, dash_status)


if __name__ == "__main__":
    main()
