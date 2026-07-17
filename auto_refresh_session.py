#!/usr/bin/env python3
"""Auto-heals an expired info-kierowca.pl session.

Launches Chrome (in its own throwaway profile, separate from your regular
browsing) pointed at the login page, waits for you to scan the mObywatel QR
code in the app, then captures the resulting session cookies the moment
they appear and writes session.json — no manual "launch Chrome, log in, run
a script" dance required.

Run by hand:

    python auto_refresh_session.py

or invoked automatically by notifier.py (see trigger_auto_refresh() in
notifier.py) whenever a check comes back auth_expired. A lock file at
~/.local/state/info-kierowca-notifier/auto-refresh.lock stops it firing
more than once concurrently — delete that file if a previous run crashed
without cleaning up.

Nothing but the two info-kierowca.pl session cookies is read or sent
anywhere; see cdp_client.py's docstring for the debug-port security note.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import cdp_client

STATE_DIR = Path.home() / ".local" / "state" / "info-kierowca-notifier"
PROFILE_DIR = STATE_DIR / "chrome-relogin-profile"
LOCK_FILE = STATE_DIR / "auto-refresh.lock"
CONFIG_FILE = Path.home() / ".config" / "info-kierowca-notifier" / "config.json"

# Deliberately distinct from pull_session_cookies.py's manual default (9222)
# so this never fights over the port with a Chrome you started by hand.
DEFAULT_PORT = 9333
DEFAULT_URL = "https://login.mobywatel.gov.pl/#/"
DEFAULT_TIMEOUT = 600  # seconds to wait for you to scan the QR

CHROME_CANDIDATES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
CHROME_MAC_PATH = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def find_chrome():
    for name in CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    if CHROME_MAC_PATH.exists():
        return str(CHROME_MAC_PATH)
    raise SystemExit("Couldn't find a Chrome/Chromium binary on PATH.")


def notify_desktop(summary, body, urgency="normal"):
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-a", "info-kierowca-notifier", summary, body],
            check=False,
        )
    except FileNotFoundError:
        pass


def push_ntfy(title, message, priority="default", tags=None):
    try:
        config = json.loads(CONFIG_FILE.read_text())
    except Exception:
        return
    topic = config.get("ntfy_topic")
    if not topic:
        return
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception:
        pass


def acquire_lock():
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
        except ValueError:
            pid = None
        if pid is not None:
            try:
                os.kill(pid, 0)
                return False  # a refresh is already in progress
            except OSError:
                pass  # stale lock — the owning process is gone
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def wait_for_cookies(host, port, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = cdp_client.fetch_cookies(host, port)
            cookies = cdp_client.extract_info_kierowca_cookies(raw)
            if cdp_client.COOKIE_NAMES <= cookies.keys():
                return cookies
        except Exception:
            pass  # Chrome may be mid-navigation; just retry
        time.sleep(3)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help="Page to open Chrome to (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help="Seconds to wait for the QR to be scanned before giving up (default: %(default)s)",
    )
    parser.add_argument(
        "--keep-open", action="store_true", help="Leave Chrome open after capturing cookies"
    )
    args = parser.parse_args()

    if not acquire_lock():
        print("A refresh is already in progress (lock file present) — exiting.")
        return

    chrome_proc = None
    try:
        chrome = find_chrome()
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        chrome_proc = subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={args.port}",
                f"--user-data-dir={PROFILE_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=460,760",
                f"--app={args.url}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        notify_desktop(
            "info-kierowca: relogin needed",
            "Chrome opened — scan the QR in the app to log back in",
            "critical",
        )
        push_ntfy(
            "info-kierowca: relogin needed",
            "Chrome opened on your desktop — scan the QR in the app to log back in",
            priority="urgent",
            tags=["warning"],
        )

        cdp_client.wait_for_debug_port("127.0.0.1", args.port, timeout=20)
        cookies = wait_for_cookies("127.0.0.1", args.port, args.timeout)

        if cookies is None:
            print(f"No login detected within {args.timeout}s.")
            notify_desktop(
                "info-kierowca: relogin timed out",
                f"No login detected within {args.timeout}s — run auto_refresh_session.py again when ready",
                "critical",
            )
            push_ntfy(
                "info-kierowca: relogin timed out",
                "QR wasn't scanned in time — it'll retry on the next auth error",
            )
            sys.exit(1)

        cdp_client.write_session_file(cookies)
        print(f"Wrote {len(cookies)} cookie(s) to {cdp_client.SESSION_FILE}")
        notify_desktop(
            "info-kierowca: session refreshed",
            "Logged back in — the notifier will pick it up on the next check",
        )
        push_ntfy(
            "info-kierowca: session refreshed",
            "Logged back in automatically — notifier resumes on the next check",
        )
    finally:
        if chrome_proc and not args.keep_open:
            chrome_proc.terminate()
            try:
                chrome_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome_proc.kill()
        release_lock()


if __name__ == "__main__":
    main()
