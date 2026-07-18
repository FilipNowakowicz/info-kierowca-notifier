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
DEFAULT_URL = "https://info-kierowca.pl/login"
# No default timeout: you'll log back in eventually regardless, and the lock
# file already stops this from being relaunched while one is in flight — so
# just wait for the QR to be scanned, however long that takes. Pass --timeout
# to bound it (e.g. for testing).
DEFAULT_TIMEOUT = None

# Edge is Chromium-based and supports the same --remote-debugging-port CDP
# flag, so it's included as a fallback — it's preinstalled on all Windows
# machines, unlike Chrome, which matters for a "no setup needed" install.
CHROME_CANDIDATES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "msedge", "microsoft-edge", "microsoft-edge-stable",
]
CHROME_MAC_PATH = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
EDGE_WIN_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

# The login click-path: info-kierowca.pl -> (maybe) "Zaloguj się" -> a PWPW
# identity-provider chooser with a "gov.pl" tile -> a login.gov.pl chooser
# with an "Aplikacja mObywatel" tile -> QR code. Checked in this order (most
# specific/downstream first) so whichever screen is showing gets exactly one
# click; harmless no-op once the QR page itself is showing, since nothing
# there matches. Site markup could change and break this — if it stops
# matching, you can still click through by hand while the script waits.
AUTO_CLICK_TARGETS = ["Aplikacja mObywatel", "gov.pl", "Zaloguj się"]

# Shared by both scripts below: find the smallest element anywhere on the
# page whose text contains one of `targets` (checked in that order) and
# click the nearest real clickable ancestor — login-page rows are often a
# plain <div> wrapping an icon + label, not a bare <button>/<a>.
CLICK_LOGIC_JS = """
function __ikw_isClickable(el) {
  if (!el) return false;
  var style = window.getComputedStyle(el);
  return el.tagName === 'BUTTON' || el.tagName === 'A' ||
    el.getAttribute('role') === 'button' || style.cursor === 'pointer';
}
function __ikw_clickableAncestor(el) {
  var cur = el;
  for (var i = 0; i < 6 && cur; i++) {
    if (__ikw_isClickable(cur)) return cur;
    cur = cur.parentElement;
  }
  return el;
}
function __ikw_findAndClick(targets) {
  var all = document.querySelectorAll('button, a, [role="button"], li, div, span');
  for (var ti = 0; ti < targets.length; ti++) {
    var text = targets[ti];
    var best = null;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var t = (el.innerText || el.textContent || '').trim();
      if (t && t.length < 200 && t.toLowerCase().indexOf(text.toLowerCase()) !== -1) {
        if (!best || t.length < best[1].length) best = [el, t];
      }
    }
    if (best) {
      __ikw_clickableAncestor(best[0]).click();
      return text;
    }
  }
  return null;
}
"""

# One-shot version: used as a slow Python-polled fallback (see try_auto_click).
AUTO_CLICK_JS = CLICK_LOGIC_JS + (
    "(function(targets) { return __ikw_findAndClick(targets); })(%s)"
    % json.dumps(AUTO_CLICK_TARGETS)
)

# Persistent version: registered via Page.addScriptToEvaluateOnNewDocument
# (see cdp_client.inject_and_navigate) so it's already watching the DOM
# before the first paint of *every* document in this tab — including
# cross-origin OAuth redirects — and clicks the instant a target appears,
# instead of waiting on our next poll tick. This is what makes the
# click-through effectively instant rather than bounded by a sleep interval.
AUTO_CLICK_OBSERVER_JS = CLICK_LOGIC_JS + (
    """
(function(targets) {
  var scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function() { scheduled = false; __ikw_findAndClick(targets); });
  }
  __ikw_findAndClick(targets);
  new MutationObserver(schedule).observe(
    document, {childList: true, subtree: true, characterData: true}
  );
})(%s)
"""
    % json.dumps(AUTO_CLICK_TARGETS)
)


def try_auto_click(host, port):
    try:
        return cdp_client.evaluate_in_page(host, port, AUTO_CLICK_JS)
    except Exception:
        return None


def find_chrome():
    for name in CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    if CHROME_MAC_PATH.exists():
        return str(CHROME_MAC_PATH)
    for path in EDGE_WIN_PATHS:
        if path.exists():
            return str(path)
    raise SystemExit("Couldn't find a Chrome/Chromium/Edge binary on PATH.")


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
    if not topic or not config.get("phone_alerts_relogin", True):
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


def wait_for_cookies(host, port, timeout, chrome_proc):
    """timeout=None waits indefinitely — but always bails out the moment
    chrome_proc has exited. Without this, a crashed/killed Chrome left this
    looping forever: fetch_cookies() against a dead debug port just raises,
    and that's caught by the same `except Exception: pass` that's meant to
    tolerate Chrome being mid-navigation, so a permanent failure looked
    identical to a transient one. The process never returned, never hit
    the `finally` in main() that releases the lock, and never got reaped
    by the OS (visible as a `<defunct>` zombie in `ps`) — so a Chrome that
    crashed hours ago could still be silently blocking every future
    auto-relogin attempt, with no window on screen for anyone to notice.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while deadline is None or time.monotonic() < deadline:
        if chrome_proc.poll() is not None:
            return None
        try:
            raw = cdp_client.fetch_cookies(host, port)
            cookies = cdp_client.extract_info_kierowca_cookies(raw)
            if cdp_client.COOKIE_NAMES <= cookies.keys():
                return cookies
        except Exception:
            pass  # Chrome may be mid-navigation; just retry
        try_auto_click(host, port)
        time.sleep(3)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help="Page to open Chrome to (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help="Seconds to wait for the QR to be scanned before giving up (default: wait indefinitely)",
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
                "--app=about:blank",
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
            priority="default",
        )

        cdp_client.wait_for_debug_port("127.0.0.1", args.port, timeout=20)
        # Register the click-observer before the real page ever loads, then
        # navigate — so it's already watching from the first paint instead
        # of racing our own next poll tick.
        cdp_client.inject_and_navigate("127.0.0.1", args.port, args.url, AUTO_CLICK_OBSERVER_JS)
        cookies = wait_for_cookies("127.0.0.1", args.port, args.timeout, chrome_proc)

        if cookies is None:
            if chrome_proc.poll() is not None:
                print("Chrome exited before logging in (crashed or was closed).")
                notify_desktop(
                    "info-kierowca: relogin failed",
                    "Chrome closed before logging in — run auto_refresh_session.py again",
                    "critical",
                )
            else:
                print(f"No login detected within {args.timeout}s.")
                notify_desktop(
                    "info-kierowca: relogin timed out",
                    f"No login detected within {args.timeout}s — run auto_refresh_session.py again when ready",
                    "critical",
                )
            sys.exit(1)

        cdp_client.write_session_file(cookies)
        print(f"Wrote {len(cookies)} cookie(s) to {cdp_client.SESSION_FILE}")
        notify_desktop(
            "info-kierowca: session refreshed",
            "Logged back in — the notifier will pick it up on the next check",
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
