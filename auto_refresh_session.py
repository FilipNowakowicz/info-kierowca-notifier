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
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import cdp_client

from paths import AUTO_REFRESH_LOCK as LOCK_FILE  # noqa: E402
from paths import CONFIG_FILE, STATE_DIR  # noqa: E402,F401

PROFILE_DIR = STATE_DIR / "chrome-relogin-profile"

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
# CHROME_CANDIDATES' PATH-based names ("google-chrome" etc.) are a Linux/Mac
# convention — a Windows Chrome install never puts chrome.exe on PATH under
# any of those names, so without these explicit paths find_chrome() always
# fell through to EDGE_WIN_PATHS below even on a machine with Chrome
# installed (confirmed live: Edge opened instead of the user's own Chrome).
# %LOCALAPPDATA% covers the common non-admin/per-user install; the two
# Program Files paths cover a machine-wide install (matching EDGE_WIN_PATHS'
# own x86/64 pair).
CHROME_WIN_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]
EDGE_WIN_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def _chrome_from_windows_registry():
    """Look up Chrome's install path via the "App Paths" registry key —
    the same mechanism Windows itself uses to resolve a bare "chrome.exe"
    (e.g. from the Run dialog or `start chrome`). Every normal Chrome
    installer (per-user or per-machine) writes this key regardless of which
    drive/folder it installed to, so it's more robust than guessing fixed
    paths like CHROME_WIN_PATHS above — those only cover the default
    locations and silently miss anything installed elsewhere. winreg only
    exists on Windows; the ImportError there makes this a clean no-op on
    Linux/Mac.
    """
    try:
        import winreg
    except ImportError:
        return None
    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                path, _ = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        if path and Path(path).exists():
            return path
    return None

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
# plain <div> wrapping an icon + label, not a bare <button>/<a>. Once
# targets[0] (the most specific/downstream one, "Aplikacja mObywatel" — the
# tile that lands on the QR page itself) gets clicked, a sessionStorage flag
# is set so neither this function nor its callers try again: sessionStorage
# survives same-origin navigations (including the browser back button), so
# if you back out of the QR page to pick a different login method, this
# won't force you straight back to it. Origin-scoped only — it resets on a
# genuine cross-origin hop, which matches the one place that's actually
# wanted: a fresh run of this script (new profile) should auto-click again.
# The "is this thing clickable" heuristic, shared verbatim with
# open_logged_in_browser.py's own click-by-text helper. This is the most
# site-fragile code in the project — when info-kierowca.pl reshuffles its
# markup it gets edited under pressure, so it lives in exactly one place
# rather than in two copies that can silently drift apart.
CLICKABLE_HELPERS_JS = """
function __ikw_isVisible(el) {
  var style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  return el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0;
}
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
"""

CLICK_LOGIC_JS = """
var __IKW_STOP_KEY = '__ikw_auto_click_stopped';
function __ikw_stopped() {
  try { return !!sessionStorage.getItem(__IKW_STOP_KEY); } catch (e) { return false; }
}
""" + CLICKABLE_HELPERS_JS + """
function __ikw_findAndClick(targets) {
  if (__ikw_stopped()) return null;
  var all = document.querySelectorAll('button, a, [role="button"], li, div, span');
  for (var ti = 0; ti < targets.length; ti++) {
    var text = targets[ti];
    var best = null;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      // textContent (unlike innerText) includes text from display:none
      // elements, so a not-yet-revealed tile that's already in the DOM
      // (common in SPA choosers that toggle visibility via a class rather
      // than mounting/unmounting) must not be matched via that fallback.
      if (!__ikw_isVisible(el)) continue;
      var t = (el.innerText || el.textContent || '').trim();
      if (t && t.length < 200 && t.toLowerCase().indexOf(text.toLowerCase()) !== -1) {
        // <=, not <: querySelectorAll returns document order, so an outer
        // wrapper div is always seen before the inner button/span it wraps.
        // When their trimmed text is the same length (the wrapper contains
        // nothing but that one label), a strict < would keep the first
        // (outer, usually non-clickable) match instead of the more
        // specific inner one -- and __ikw_clickableAncestor only walks
        // *up* from whatever's picked, so it would never reach the real
        // clickable element in that case.
        if (!best || t.length <= best[1].length) best = [el, t];
      }
    }
    if (best) {
      __ikw_clickableAncestor(best[0]).click();
      if (text === targets[0]) {
        try { sessionStorage.setItem(__IKW_STOP_KEY, '1'); } catch (e) {}
      }
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
#
# Watches `attributes` as well as `childList`/`characterData`: some chooser
# screens reveal the next tile by toggling a class/hidden attribute on an
# already-present element rather than inserting a new node, which this
# observer used to miss entirely — the click then only happened on the next
# Python-side fallback poll (try_auto_click, see wait_for_cookies), which is
# exactly the ~1s-ish hang reported right before the QR page. Disconnects
# itself once targets[0] is clicked (see __ikw_findAndClick's sessionStorage
# flag above) so a same-document re-render that brings the chooser back
# (e.g. picking a different login method) doesn't get auto-clicked forward
# again.
#
# Confirmed live 2026-07-18: on the podmiotyzewnetrzne.login.gov.pl tile
# chooser specifically, this observer's callback (and a setInterval placed
# alongside it, tried and discarded) never fires at all even though the
# tiles are fully rendered and clickable within ~1s — the MutationObserver
# and any in-page timers registered via this
# Page.addScriptToEvaluateOnNewDocument-injected script go silently inert on
# that one page. A *fresh* Runtime.evaluate call from Python-side (i.e.
# try_auto_click, called on its own separate CDP connection) always finds
# and clicks the tile instantly regardless. So the reliable fix isn't a
# better in-page watcher — it's not leaving that fallback 3s idle; see
# wait_for_cookies's poll interval.
AUTO_CLICK_OBSERVER_JS = CLICK_LOGIC_JS + (
    """
(function(targets) {
  if (__ikw_stopped()) return;
  var scheduled = false;
  var observer = new MutationObserver(schedule);
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function() {
      scheduled = false;
      var clicked = __ikw_findAndClick(targets);
      if (clicked === targets[0]) observer.disconnect();
    });
  }
  var clicked = __ikw_findAndClick(targets);
  if (clicked === targets[0]) return;
  observer.observe(
    document, {childList: true, subtree: true, characterData: true, attributes: true}
  );
})(%s)
"""
    % json.dumps(AUTO_CLICK_TARGETS)
)


def try_auto_click(host, port):
    try:
        return cdp_client.evaluate_in_page(host, port, AUTO_CLICK_JS)
    except Exception as e:
        # Swallowed by design (Chrome may be mid-navigation) but logged: a
        # click failing here silently every 0.5s for the whole wait looks
        # identical, from the outside, to the tile chooser just never
        # matching — this print is what tells the two apart in
        # AUTO_REFRESH_LOG_FILE after the fact.
        print(f"try_auto_click error: {e!r}")
        return None


def find_chrome():
    for name in CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    if CHROME_MAC_PATH.exists():
        return str(CHROME_MAC_PATH)
    registry_path = _chrome_from_windows_registry()
    if registry_path:
        return registry_path
    for path in CHROME_WIN_PATHS:
        if path.exists():
            return str(path)
    for path in EDGE_WIN_PATHS:
        if path.exists():
            return str(path)
    raise SystemExit("Couldn't find a Chrome/Chromium/Edge binary on PATH.")


def chrome_available():
    """Whether find_chrome() would succeed, without raising. Used by
    notifier.trigger_auto_refresh()/trigger_open_browser() to detect a
    missing Chromium browser (e.g. a Mac with only Safari installed)
    synchronously, before spawning the detached subprocess whose own
    find_chrome() failure would otherwise be invisible — its stdout/stderr
    go to DEVNULL since the launch is fire-and-forget.
    """
    try:
        find_chrome()
        return True
    except SystemExit:
        return False


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

    The 0.5s poll interval (not the 3s it used to be) matters more than it
    looks: try_auto_click() is a *fresh* Runtime.evaluate call on its own
    CDP connection, which — confirmed live 2026-07-18 — is the only thing
    that reliably clicks through the podmiotyzewnetrzne.login.gov.pl tile
    chooser. AUTO_CLICK_OBSERVER_JS's in-page MutationObserver (and a
    setInterval placed alongside it, tried and discarded) never fires at
    all on that specific page, even though the tiles are clickable within
    ~1s of landing — so this Python-side retry is the actual click-through
    mechanism there, not just a backstop, and the old 3s cadence was a
    real, visible stall directly on top of it. try_auto_click() is a cheap
    no-op once __ikw_stopped() is true, so polling this often for however
    long a human takes to scan the QR costs nothing but some idle loopback
    chatter.
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
        clicked = try_auto_click(host, port)
        if clicked:
            print(f"auto-clicked: {clicked!r}")
        time.sleep(0.5)
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
        "--no-phone-push", action="store_true",
        help="Skip the ntfy push notification — used when a person just clicked a "
        "button and is already watching Chrome, so a 'scan the QR' push to their "
        "phone would be redundant. The desktop notification still fires.",
    )
    parser.add_argument(
        "--keep-open", action="store_true", help="Leave Chrome open after capturing cookies"
    )
    args = parser.parse_args()

    # notifier.trigger_auto_refresh(force=True) — the "Open browser" button's
    # path for clearing a forgotten QR window — SIGTERMs whoever holds the
    # lock. Without a handler Python dies immediately, skipping the finally
    # below: the lock got cleared but our Chrome child survived as an orphan
    # still holding PROFILE_DIR, so the *replacement* Chrome launched against
    # the same --user-data-dir would delegate to it and exit instantly,
    # tripping the "Chrome closed before logging in" bail-out on every retry.
    # Translating the signal into SystemExit lets the finally run normally.
    def _terminate(signum, _frame):
        raise SystemExit(f"terminated by signal {signum}")

    signal.signal(signal.SIGTERM, _terminate)

    # stdout/stderr are redirected to a plain file (AUTO_REFRESH_LOG_FILE) when
    # launched via notifier.trigger_auto_refresh(), which fully-buffers by
    # default for a non-tty — without this, prints below (including
    # try_auto_click's failure logging) wouldn't actually land in the file
    # until the process exits, which could be hours into an unattended wait.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass  # older Python without reconfigure(); harmless to skip

    if not acquire_lock():
        print("A refresh is already in progress (lock file present) — exiting.")
        return

    chrome_proc = None
    try:
        chrome = find_chrome()
        print(f"using browser: {chrome}")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        chrome_proc = subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={args.port}",
                f"--user-data-dir={PROFILE_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                # Both 460px and 600px wide clipped the login page below
                # whatever breakpoint swaps its QR image for a plain numeric
                # backup code (confirmed live 2026-07-22, twice). There's no
                # real reason to keep this narrow/phone-ish — it's a login
                # page in a desktop browser, not a phone screen — so this
                # goes well past any plausible responsive breakpoint
                # (common ones sit around 480/600/768px) instead of tuning
                # the width by trial and error again.
                "--window-size=900,850",
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
        if not args.no_phone_push:
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
