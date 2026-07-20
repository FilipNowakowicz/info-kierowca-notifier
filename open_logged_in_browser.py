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
from datetime import datetime

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
# so it can't also match CHANGE_DATE_TEXT's own button. By default nothing
# past the second click is automated, so picking the actual new date and
# any final confirm past that stays a real click from you. The only
# optional exception is --target-slot (see try_select_target_slot()),
# itself off unless config's experimental auto_select_slot flag is set —
# it selects the matching slot and clicks "Przejdź do podsumowania", landing
# on the "Potwierdź wybrany egzamin" summary modal. A second, separate flag
# (--confirm-reschedule / config's auto_confirm_reschedule, by explicit user
# request as of 2026-07-20, screenshot-confirmed to show the exam type,
# category, date/time, and price with no separate payment step) goes one
# click further and clicks CONFIRM_SUMMARY_TEXT — the one action in this
# whole file that actually submits the reservation change. It requires
# auto_select_slot to also be on, and try_select_target_slot() verifies the
# summary modal's own text matches the intended slot before ever clicking
# it. This is the single highest-stakes click in this project: unlike
# everything before it, it can't be undone by just closing the tab.
CHANGE_DATE_TEXT = "Zmień termin"
CONFIRM_CHANGE_DATE_TEXT = "Zmień termin rezerwacji"
SUMMARY_BUTTON_TEXT = "Przejdź do podsumowania"
CONFIRM_SUMMARY_TEXT = "Potwierdź i przejdź dalej"


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


# notifier.py's hit_dicts use the search API's own exam_type values
# ("Theoretical"/"Practice"); these are the labels the reschedule modal
# renders in each slot row (confirmed from screenshots, not live DOM).
EXAM_TYPE_LABELS_PL = {
    "Theoretical": "Egzamin teoretyczny",
    "Practice": "Egzamin praktyczny",
}


def select_slot_js(exam_label, time_str):
    # EXPERIMENTAL / UNVERIFIED against the live site as of 2026-07-20 —
    # written from screenshots of the slot-picker modal, not a live DOM
    # inspection like click_text_js's button/role="button" selector was.
    # The modal renders one radio input per (date, time) slot row inside an
    # expanded date group; this walks up from each radio looking for an
    # ancestor whose text contains both the Polish exam-type label and the
    # HH:MM time, then clicks that radio directly (not a clickable-ancestor
    # heuristic like click_text_js's, since a radio input is always
    # clickable regardless of how the row around it is styled). Capped at 6
    # ancestor levels, same as __ikw_clickableAncestor, to avoid walking
    # high enough to span into a sibling row's text.
    return auto_refresh_session.CLICKABLE_HELPERS_JS + """
(function(examLabel, timeStr) {
  var radios = document.querySelectorAll('input[type="radio"]');
  for (var i = 0; i < radios.length; i++) {
    var radio = radios[i];
    var cur = radio;
    var t = '';
    for (var depth = 0; depth < 6 && cur; depth++) {
      t = (cur.innerText || cur.textContent || '').trim();
      if (t.indexOf(examLabel) !== -1 && t.indexOf(timeStr) !== -1) break;
      cur = cur.parentElement;
    }
    if (t.indexOf(examLabel) !== -1 && t.indexOf(timeStr) !== -1) {
      radio.click();
      return true;
    }
  }
  return false;
})(%s, %s)
""" % (json.dumps(exam_label), json.dumps(time_str))


def wait_and_select_slot(host, port, exam_label, time_str, timeout=20):
    """Same polling shape as wait_and_click(), for select_slot_js() instead —
    the matching slot row may not be in the DOM yet right after the date
    group is expanded."""
    js = select_slot_js(exam_label, time_str)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if cdp_client.evaluate_in_page(host, port, js):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def click_enabled_button_js(text):
    # Deliberately its own function rather than reusing click_text_js(), and
    # shared between SUMMARY_BUTTON_TEXT and CONFIRM_SUMMARY_TEXT: both
    # start out present but disabled until the step before them completes
    # (see screenshots — greyed out), and a plain el.click() on a disabled
    # button is a silent no-op in most browsers rather than an error.
    # click_text_js() has no notion of "disabled", so a caller couldn't tell
    # a real click from one that did nothing; this checks
    # el.disabled/aria-disabled explicitly and only reports success (and
    # only clicks) once the button is actually enabled, so the polling wait
    # loop below keeps retrying meanwhile instead of falsely reporting done.
    # Exact text match rather than click_text_js()'s substring match — both
    # button labels are short, specific, and known exactly from screenshots,
    # and an exact match is the safer choice given what CONFIRM_SUMMARY_TEXT
    # actually does.
    return auto_refresh_session.CLICKABLE_HELPERS_JS + """
(function(text) {
  var all = document.querySelectorAll('button, [role="button"]');
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var t = (el.innerText || el.textContent || '').trim();
    if (t === text) {
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
      el.click();
      return true;
    }
  }
  return false;
})(%s)
""" % json.dumps(text)


def wait_and_click_enabled(host, port, text, timeout=20):
    """Same polling shape as wait_and_click(), for click_enabled_button_js()
    instead — the target button needs a moment to go from disabled to
    enabled after whatever step precedes it completes."""
    js = click_enabled_button_js(text)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if cdp_client.evaluate_in_page(host, port, js):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def wait_and_verify_summary(host, port, date_str, time_str, exam_label, timeout=10):
    """Safety check run before CONFIRM_SUMMARY_TEXT is ever clicked: does the
    summary modal's own visible text actually contain the date, time, and
    exam type we intended to select? This is the one guard against
    select_slot_js() having matched the wrong radio row before a real
    reservation change gets submitted — unlike every earlier step in this
    flow, that click can't be undone by just closing the tab.

    Checks document.body's whole visible text rather than a specific
    "Data i godzina" row element, since no live-verified selector for the
    modal exists (screenshot-only, like the rest of this feature) — a false
    positive from unrelated matching text elsewhere on the page is possible
    in theory, but a false negative (missing a real mismatch) is not, which
    is the direction that matters here: this errs toward not confirming
    rather than confirming wrongly.
    """
    expected_datetime = f"{date_str}, {time_str}"
    js = """
(function(expectedDateTime, examLabel) {
  var text = document.body.innerText || document.body.textContent || '';
  return text.indexOf(expectedDateTime) !== -1 && text.indexOf(examLabel) !== -1;
})(%s, %s)
""" % (json.dumps(expected_datetime), json.dumps(exam_label))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if cdp_client.evaluate_in_page(host, port, js):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


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


def try_select_target_slot(host, port, target_slot_json, confirm=False):
    """Best-effort continuation of the auto-click-through, gated behind
    --target-slot (itself only ever passed when config's experimental
    auto_select_slot flag is on — see notifier.trigger_open_browser()).

    Slots within a ~31-day window show up in the "Najbliższe dostępne
    terminy" list on the date-picker screen without needing to touch the
    "Data rozpoczęcia" field at all — every slot notifier.py finds is
    already inside that window (MAX_DAYS_AHEAD), so this deliberately
    doesn't attempt to drive that date input. Confirmed live 2026-07-20:
    the field only matters for pushing the window further out than that.

    Expands the date group matching the target's date, selects the radio
    button matching its exam type + time, then clicks "Przejdź do
    podsumowania" (go to summary) to land on the "Potwierdź wybrany
    egzamin" summary modal. If any of that fails (slot already taken by
    someone else, or a DOM this hasn't been verified against), it stops
    immediately and leaves you to pick it by hand — this never guesses.

    confirm=False (the default) stops there, same as before 2026-07-20.

    confirm=True — only ever set when config's separate, also-experimental
    auto_confirm_reschedule flag is on, by explicit user request as of
    2026-07-20 — goes one click further: verifies the summary modal's own
    text actually shows the intended date/time/exam type
    (wait_and_verify_summary()), and only if that matches, clicks
    CONFIRM_SUMMARY_TEXT ("Potwierdź i przejdź dalej"). That submits the
    actual reservation change — the one action in this file that can't be
    undone by closing the tab. A verification mismatch, or the button never
    becoming clickable, stops short of that click every time.
    """
    try:
        target = json.loads(target_slot_json)
        dt = datetime.fromisoformat(target["datetime"])
        exam_label = EXAM_TYPE_LABELS_PL.get(target["exam_type"], target["exam_type"])
    except Exception as e:
        print(f"Couldn't parse --target-slot ({e!r}) — pick the slot yourself.")
        return
    date_str = dt.strftime("%d/%m/%Y")
    time_str = dt.strftime("%H:%M")
    print(f"Looking for {exam_label} at {time_str} on {date_str}...")
    if not wait_and_click(host, port, date_str):
        print(f"Couldn't find the '{date_str}' date group automatically — pick the slot yourself.")
        return
    print(f"Expanded '{date_str}'.")
    if not wait_and_select_slot(host, port, exam_label, time_str):
        print(
            f"Couldn't find a matching {exam_label} row at {time_str} "
            "(may already be taken) — pick the slot yourself."
        )
        return
    print(f"Selected {exam_label} at {time_str}.")
    if not wait_and_click_enabled(host, port, SUMMARY_BUTTON_TEXT):
        print(
            f"Selected the slot but couldn't click '{SUMMARY_BUTTON_TEXT}' automatically "
            "— click it yourself."
        )
        return
    print(f"Clicked '{SUMMARY_BUTTON_TEXT}'.")
    if not confirm:
        print(
            "Review the summary screen and confirm yourself from here. Nothing past this "
            "has been automated or verified."
        )
        return
    if not wait_and_verify_summary(host, port, date_str, time_str, exam_label):
        print(
            "Summary screen didn't show the expected date/time/exam type — NOT clicking "
            f"'{CONFIRM_SUMMARY_TEXT}' automatically. Review it yourself before confirming."
        )
        return
    print("Summary screen matches the intended slot.")
    if wait_and_click_enabled(host, port, CONFIRM_SUMMARY_TEXT):
        print(
            f"Clicked '{CONFIRM_SUMMARY_TEXT}' — the reservation change has been submitted. "
            "Nothing past this screen is automated or known — check the site for whatever "
            "comes next."
        )
    else:
        print(
            f"Couldn't click '{CONFIRM_SUMMARY_TEXT}' automatically — confirm it yourself "
            "if the summary looks right."
        )


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
    parser.add_argument(
        "--target-slot", default=None,
        help=(
            "JSON hit dict (word/exam_type/datetime/places, matching notifier.py's hit_dicts) "
            "to also select on the date-range picker and carry through to the summary screen "
            "after the Zmień termin click-through. Experimental/unverified — see "
            "try_select_target_slot()'s docstring. By default stops on landing on that summary "
            "screen; add --confirm-reschedule to go one click further."
        ),
    )
    parser.add_argument(
        "--confirm-reschedule", action="store_true",
        help=(
            "Only takes effect together with --target-slot: after landing on the summary "
            "screen, verify it matches the intended slot and click "
            f"'{CONFIRM_SUMMARY_TEXT}' — submitting the actual reservation change. "
            "Experimental/unverified. This is the one click in this entire project that "
            "can't be undone by closing the tab."
        ),
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
            print(f"Clicked '{CONFIRM_CHANGE_DATE_TEXT}'.")
            if args.target_slot:
                try_select_target_slot(
                    "127.0.0.1", args.port, args.target_slot, confirm=args.confirm_reschedule
                )
            else:
                print("Pick the new date and confirm yourself from here.")
        else:
            print(f"Couldn't find '{CONFIRM_CHANGE_DATE_TEXT}' automatically — click it yourself.")
    else:
        print(f"Couldn't find '{CHANGE_DATE_TEXT}' automatically — click it yourself.")
    print("Close the window whenever you're done — this script doesn't manage it further.")


if __name__ == "__main__":
    main()
