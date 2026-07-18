#!/usr/bin/env python3
"""Launches a fresh Chrome window pre-authenticated to info-kierowca.pl by
injecting the cookies already saved in session.json — skips the login/QR
step entirely.

Run by hand:

    python open_logged_in_browser.py

Uses a dedicated throwaway profile (separate from your regular browsing and
from auto_refresh_session.py's own profile) so it never fights over a
profile lock. Reads only from session.json and writes nothing; nothing but
the two info-kierowca.pl session cookies is sent anywhere, and only to
info-kierowca.pl itself — see cdp_client.py's docstring for the
debug-port security note.
"""
import argparse
import json
import subprocess
import time
import uuid

import auto_refresh_session
import cdp_client
from auto_refresh_session import find_chrome

from paths import STATE_DIR  # noqa: E402

PROFILE_DIR = STATE_DIR / "chrome-reschedule-profile"

# Distinct from pull_session_cookies.py's manual default (9222) and
# auto_refresh_session.py's (9333) so none of the three ever fight over a
# port if run at the same time.
DEFAULT_PORT = 9555
DEFAULT_URL = "https://info-kierowca.pl/cases"


def consent_cookie():
    """A pre-accepted CookieScriptConsent value, shaped like what the site's
    own consent banner (a CookieScript.com widget) writes when you click
    through it — setting this ourselves means the banner never renders,
    instead of you having to dismiss it on every fresh profile.

    Opts in to "necessary" only, matching this project's existing
    minimal-footprint stance — flip action/categories below to "accept" +
    the full category list if you'd rather auto-accept everything.
    """
    payload = {
        "bannershown": 1,
        "action": "reject",
        "consenttime": int(time.time()),
        "categories": "[]",
        "key": str(uuid.uuid4()),
    }
    return json.dumps(payload)


# The two buttons auto-clicked in sequence: the list button that opens the
# "are you sure" modal, then that modal's own confirm button. Both matches
# are deliberately narrow — exact-ish text against just button/link/
# role=button elements, not the login flow's fuzzy multi-target chooser (see
# AUTO_CLICK_TARGETS in auto_refresh_session.py) — because the list page
# also has an "Anuluj" (cancel the booking outright) button close by, and
# CONFIRM_CHANGE_DATE_TEXT is intentionally the longer, more specific phrase
# so it can't also match CHANGE_DATE_TEXT's own button. This still stops
# right after the second click; nothing past it is automated, so picking
# the actual new date and any final confirm past that stays a real click
# from you.
CHANGE_DATE_TEXT = "Zmień termin"
CONFIRM_CHANGE_DATE_TEXT = "Zmień termin rezerwacji"


def click_text_js(text):
    # Deliberately stricter than auto_refresh_session.py's chooser matching
    # (buttons/links only, shorter text cap) — see the module docstring — but
    # the clickability heuristic itself is the shared one.
    return auto_refresh_session.CLICKABLE_HELPERS_JS + """
(function(text) {
  var all = document.querySelectorAll('button, a, [role="button"]');
  var best = null;
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var t = (el.innerText || el.textContent || '').trim();
    if (t && t.length < 60 && t.toLowerCase().indexOf(text.toLowerCase()) !== -1) {
      if (!best || t.length < best[1].length) best = [el, t];
    }
  }
  if (best) {
    __ikw_clickableAncestor(best[0]).click();
    return true;
  }
  return false;
})(%s)
""" % json.dumps(text)


def wait_and_click(host, port, text, timeout=20):
    """Poll for an element containing `text` and click it once it renders —
    content on this SPA loads asynchronously after navigation/a previous
    click, so it isn't there on the very first frame. Gives up quietly
    after `timeout`s if it never shows (site copy changed, modal didn't
    open, etc.) — you can still click it yourself, same fallback as the
    login auto-click.
    """
    js = click_text_js(text)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if cdp_client.evaluate_in_page(host, port, js):
                return True
        except Exception:
            pass  # page may be mid-navigation/render — just retry
        time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url", default=DEFAULT_URL, help="Page to open Chrome to (default: %(default)s)"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-auto-click", action="store_true",
        help="Just open a logged-in tab — skip the Zmień termin/confirm auto-click-through",
    )
    args = parser.parse_args()

    if not cdp_client.SESSION_FILE.exists():
        raise SystemExit(
            f"No session found at {cdp_client.SESSION_FILE} — log in first "
            "(see auto_refresh_session.py or pull_session_cookies.py)."
        )
    session = json.loads(cdp_client.SESSION_FILE.read_text())
    cookies = session.get("cookies", {})
    missing = cdp_client.COOKIE_NAMES - cookies.keys()
    if missing:
        raise SystemExit(
            f"session.json is missing {sorted(missing)} — run auto_refresh_session.py "
            "to log in again."
        )

    chrome = find_chrome()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    cdp_client.wait_for_debug_port("127.0.0.1", args.port, timeout=20)
    cdp_client.set_cookies(
        "127.0.0.1", args.port, {**cookies, "CookieScriptConsent": consent_cookie()}
    )
    cdp_client.navigate("127.0.0.1", args.port, args.url)

    print(f"Chrome opened at {args.url}, logged in using {cdp_client.SESSION_FILE}.")
    if args.no_auto_click:
        print("Skipping the Zmień termin auto-click-through (--no-auto-click).")
    elif wait_and_click("127.0.0.1", args.port, CHANGE_DATE_TEXT):
        print(f"Clicked '{CHANGE_DATE_TEXT}'.")
        if wait_and_click("127.0.0.1", args.port, CONFIRM_CHANGE_DATE_TEXT):
            print(f"Clicked '{CONFIRM_CHANGE_DATE_TEXT}' — pick the new date and confirm yourself from here.")
        else:
            print(f"Couldn't find '{CONFIRM_CHANGE_DATE_TEXT}' automatically — click it yourself.")
    else:
        print(f"Couldn't find '{CHANGE_DATE_TEXT}' automatically — click it yourself.")
    print("Close the window whenever you're done — this script doesn't manage it further.")


if __name__ == "__main__":
    main()
