#!/usr/bin/env python3
"""Launches a fresh Chrome window pre-authenticated to info-kierowca.pl by
injecting the cookies already saved in session.json — skips the login/QR
step entirely.

Run by hand:

    python -m info_kierowca_notifier.booking.reschedule

Uses a dedicated throwaway profile (separate from your regular browsing and
from auth.session's own profile) so it never fights over a
profile lock. Reads only from session.json; nothing but the two
info-kierowca.pl session cookies is sent anywhere, and only to
info-kierowca.pl itself — see browser.cdp's docstring for the
debug-port security note. The one write this file can make is conditional:
with --confirm-reschedule and a confirmed booking change verified on
/cases afterward, it updates config.json's current_slot_date to match (see
update_current_slot_date()) — otherwise it writes nothing except its own
progress to RESCHEDULE_LOG_FILE (see push_ntfy()'s docstring for why this
file also duplicates a slice of notifier.py's own notification logic
instead of importing it).
"""
import argparse
import json
import os
import subprocess
import time
import uuid
from datetime import datetime

from info_kierowca_notifier.auth import session as auto_refresh_session
from info_kierowca_notifier.browser import cdp as cdp_client
from info_kierowca_notifier import ntfy_transport
from info_kierowca_notifier.booking import transaction as rt
from info_kierowca_notifier.auth.session import find_chrome

from info_kierowca_notifier.paths import CONFIG_FILE, RESCHEDULE_CONFIRM_COOLDOWN_FILE, STATE_DIR  # noqa: E402

PROFILE_DIR = STATE_DIR / "chrome-reschedule-profile"

# Distinct from pull_session_cookies.py's manual default (9222) and
# auth.session's (9333) so none of the three ever fight over a
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
# AUTO_CLICK_TARGETS in auth.session) — because the list page
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
    # Deliberately stricter than auth.session's chooser matching
    # (buttons/links only, shorter text cap) — see the module docstring — but
    # the clickability heuristic itself is the shared one.
    return auto_refresh_session.CLICKABLE_HELPERS_JS + """
(function(text) { return __ikw_clickByText(text, 'button, a, [role="button"]', false, false); })(%s)
""" % json.dumps(text)


# notifier.py's hit_dicts use the search API's own exam_type values
# ("Theoretical"/"Practice"); these are the labels the reschedule modal
# renders in each slot row (confirmed from screenshots, not live DOM).
EXAM_TYPE_LABELS_PL = {
    "Theoretical": "Egzamin teoretyczny",
    "Practice": "Egzamin praktyczny",
}


def select_slot_js(exam_label, time_str):
    # Confirmed live 2026-07-28 that the first version of this (querying
    # `input[type="radio"]` and walking up 6 ancestor levels for matching
    # text) failed to select a slot that was visibly present and correctly
    # labeled — the selection circle in this modal isn't necessarily a real
    # `<input type="radio">`, and by user confirmation the whole slot row
    # is clickable, not just the circle. So this no longer looks for a
    # radio element at all: it reuses the same find-the-most-specific-
    # matching-element-then-walk-up-to-a-clickable-ancestor pattern as
    # click_text_js/__ikw_findAndClick (already proven live for the
    # login/Zmień termin click-throughs elsewhere in this project), except
    # matching requires *both* the Polish exam-type label and the HH:MM
    # time substring together, since a single row must contain both — a
    # single-substring search like __ikw_findAndClick's could match the
    # wrong row when multiple slots share an exam type or a time.
    # "Most specific" (shortest matching text) is preferred so a wrapping
    # container that happens to contain the whole date group's text isn't
    # picked over the actual row.
    return auto_refresh_session.CLICKABLE_HELPERS_JS + """
(function(examLabel, timeStr) {
  var all = document.querySelectorAll('button, a, [role="button"], [role="radio"], input[type="radio"], li, tr, div, span');
  var best = null;
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (!__ikw_isVisible(el)) continue;
    var t = (el.innerText || el.textContent || '').trim();
    if (t && t.length < 300 && t.indexOf(examLabel) !== -1 && t.indexOf(timeStr) !== -1) {
      if (!best || t.length <= best[1].length) best = [el, t];
    }
  }
  if (best) {
    __ikw_clickableAncestor(best[0]).click();
    return true;
  }
  return false;
})(%s, %s)
""" % (json.dumps(exam_label), json.dumps(time_str))


def _poll_until_truthy(host, port, js, timeout=20, target=None):
    """Evaluate `js` in the page every 0.5s until it returns truthy or
    `timeout`s elapse. Shared body of every wait_*/wait_and_click below: this
    SPA renders content asynchronously after navigation or a prior click, so a
    selector often isn't in the DOM on the first frame. Exceptions are
    swallowed and retried (the page may be mid-navigation/render); returns True
    on the first truthy result, False if it never comes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = cdp_client.evaluate_in_page(host, port, js, target=target)
            if result is True or (isinstance(result, dict) and result.get("clicked")):
                if isinstance(result, dict):
                    result = auto_refresh_session.sanitize_click_diagnostics(result)
                    print(
                        "browser click result "
                        f"label={result.get('requested_label')!r} "
                        f"matched={result.get('matched_text')!r} "
                        f"url={result.get('page_url')!r} host={result.get('page_host')!r} "
                        f"tag={result.get('tag')!r} "
                        f"id={result.get('element_id')!r} class={result.get('element_class')!r} "
                        f"href={result.get('href')!r}"
                    )
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def wait_and_select_slot(host, port, exam_label, time_str, timeout=20, target=None):
    """Same polling shape as wait_and_click(), for select_slot_js() instead —
    the matching slot row may not be in the DOM yet right after the date
    group is expanded."""
    return _poll_until_truthy(host, port, select_slot_js(exam_label, time_str), timeout, target)


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
(function(text) { return __ikw_clickByText(text, 'button, [role="button"]', true, true); })(%s)
""" % json.dumps(text)


def wait_and_click_enabled(host, port, text, timeout=20, target=None):
    """Same polling shape as wait_and_click(), for click_enabled_button_js()
    instead — the target button needs a moment to go from disabled to
    enabled after whatever step precedes it completes."""
    return _poll_until_truthy(host, port, click_enabled_button_js(text), timeout, target)


def wait_and_verify_summary(host, port, date_str, time_str, exam_label, timeout=10, target=None):
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
    return _poll_until_truthy(host, port, js, timeout, target)


def wait_and_verify_booking(host, port, date_str, time_str, exam_label, timeout=20, target=None):
    """Compatibility wrapper using card-scoped, unambiguous verification."""
    deadline = time.monotonic() + timeout
    wanted = {"date": date_str, "time": time_str, "exam_type": exam_label}
    while time.monotonic() < deadline:
        try:
            cards = rt.capture_booking_cards(host, port, target)
            if rt.classify_cards(cards, wanted, []) == rt.VERIFIED_SUCCESS:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def read_config():
    """Best-effort read of config.json — {} on any failure (missing file,
    bad JSON), so callers needing just one optional value (e.g. ntfy_topic)
    can treat a missing/unreadable config the same as an empty one, rather
    than every caller needing its own try/except. NOT used by
    update_current_slot_date() below — that read must raise on failure so
    its own except block can skip the write instead of silently clobbering
    config.json with a near-empty dict.
    """
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def push_ntfy(topic, title, message, priority="default"):
    """Send a plain ntfy notification through the shared safe transport.

    Used only for the handful of outcomes in try_select_target_slot() tied
    to auto_confirm_reschedule actually attempting or completing the final
    submit click — not for the earlier, lower-stakes auto_select_slot
    steps, which already got their own "slot found" push before the
    browser ever opened and whose own failures are logged but not worth a
    second, separate alert.
    """
    outcome = ntfy_transport.push_ntfy(topic, title, message, priority=priority)
    if not outcome.ok:
        print("Couldn't send push notification (reason={}).".format(outcome.kind))
    return outcome


def update_current_slot_date(new_date_iso):
    """Best-effort: after wait_and_verify_booking() confirms the reschedule
    actually went through, update config.json's current_slot_date to the
    newly-booked date — so notifier.is_urgent()'s very next comparison
    reflects the change immediately. Without this, current_slot_date would
    stay on the old (later) date until the user updated Settings by hand,
    and every check in between could treat the slot we just booked into —
    or anything else on the same stale side of the old cutoff — as still
    urgent, potentially re-triggering auto_confirm_reschedule on a booking
    that's already been moved.

    Reimplements notifier.save_json()'s atomic-write/chmod pattern rather
    than importing notifier: notifier.py imports this module at module
    level (OPEN_BROWSER_PORT = open_logged_in_browser.DEFAULT_PORT), so
    importing notifier back here would be circular.

    Deliberately only ever overwrites this one key, not the whole config —
    this runs in a detached subprocess launched well after notifier.py read
    its own config for this cycle, so the file on disk may have picked up
    unrelated Settings changes (e.g. a poll-interval edit) since; a
    read-modify-write of just current_slot_date preserves those instead of
    clobbering them with whatever this process's own inputs were built
    from.
    """
    try:
        config = json.loads(CONFIG_FILE.read_text())
        config["current_slot_date"] = new_date_iso
        tmp = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(config, indent=2))
        tmp.replace(CONFIG_FILE)
        CONFIG_FILE.chmod(0o600)
        print(f"Updated current_slot_date to {new_date_iso} in config.json.")
    except Exception as e:
        print(
            f"Booking confirmed, but couldn't update current_slot_date automatically ({e!r}) "
            "— update 'Date of your current booked slot' in Settings yourself."
        )


def record_confirm_cooldown(outcome="PENDING"):
    """Persist attempt time and latest outcome without weakening the 15-minute gate."""
    try:
        RESCHEDULE_CONFIRM_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"attempted_at": datetime.now().isoformat(), "outcome": outcome}
        RESCHEDULE_CONFIRM_COOLDOWN_FILE.write_text(json.dumps(payload))
        if os.name == "posix":
            RESCHEDULE_CONFIRM_COOLDOWN_FILE.chmod(0o600)
    except Exception:
        pass


def update_slot_date_for_outcome(outcome, new_date_iso):
    """The sole outcome gate for mutating current_slot_date."""
    if outcome == rt.VERIFIED_SUCCESS:
        update_current_slot_date(new_date_iso)
        return True
    return False


def wait_and_click(host, port, text, timeout=20, target=None):
    """Poll for an element containing `text` and click it once it renders —
    content on this SPA loads asynchronously after navigation/a previous
    click, so it isn't there on the very first frame. Gives up quietly
    after `timeout`s if it never shows (site copy changed, modal didn't
    open, etc.) — you can still click it yourself, same fallback as the
    login auto-click.
    """
    return _poll_until_truthy(host, port, click_text_js(text), timeout, target)


def try_select_target_slot(host, port, target_slot_json, confirm=False, page_target=None,
                           baseline_booking=None, baseline_booking_candidates=None):
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

    After that click, a separate explicit CDP target navigates to /cases while
    the transaction target remains untouched. If structured card verification
    confirms the booking now actually shows our slot as "Potwierdzona", this updates
    config.json's current_slot_date to match
    (update_current_slot_date()) — so notifier.is_urgent()'s next
    comparison reflects the change immediately instead of possibly
    re-triggering auto_confirm_reschedule on a slot we already booked
    into. Skipped (config left untouched) if that verification doesn't
    succeed within its timeout.

    Two more things happen once confirm=True, both added 2026-07-20 as a
    direct follow-up to a code review that flagged this whole flow's
    outcomes as otherwise invisible when auto-triggered (stdout used to go
    to DEVNULL — see trigger_open_browser()'s own docstring, now fixed at
    that end) and re-triggerable while a prior attempt's outcome was still
    unknown: (1) right before the real submit click is attempted,
    RESCHEDULE_CONFIRM_COOLDOWN_FILE is written, which
    notifier.confirm_reschedule_cooldown_active() checks before ever
    passing --confirm-reschedule again; (2) a push notification
    (push_ntfy(), reusing config's existing ntfy_topic) fires for every
    outcome from that point on — summary mismatch, confirm button
    unclickable, confirmed-but-unverified, or confirmed-and-verified —
    since none of those are things that should only be discoverable by
    someone happening to be watching the Chrome window.
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
    transaction_target = {"exam_type": exam_label, "date": date_str, "time": time_str,
                          "word_id": target.get("word_id", target.get("word", ""))}
    recorder = rt.DiagnosticRecorder(
        transaction_target, baseline_booking or [],
        baseline_booking_candidates=baseline_booking_candidates or [],
    ) if confirm else None
    if recorder:
        recorder.state("CAPTURE_BASELINE_BOOKING")
        print(f"transaction={recorder.transaction_id} state=baseline_captured cards={len(recorder.baseline_booking)}")
    print(f"Looking for {exam_label} at {time_str} on {date_str}...")
    if not wait_and_click(host, port, date_str, target=page_target):
        print(f"Couldn't find the '{date_str}' date group automatically — pick the slot yourself.")
        return
    print(f"Expanded '{date_str}'.")
    if not wait_and_select_slot(host, port, exam_label, time_str, target=page_target):
        print(
            f"Couldn't find a matching {exam_label} row at {time_str} "
            "(may already be taken) — pick the slot yourself."
        )
        return
    print(f"Selected {exam_label} at {time_str}.")
    if recorder:
        recorder.state("SELECT_TARGET_SLOT")
        print(f"transaction={recorder.transaction_id} state=target_slot_selected")
    if not wait_and_click_enabled(host, port, SUMMARY_BUTTON_TEXT, target=page_target):
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
    # Only fetched once we're actually in the auto-confirm path: pushes below are
    # reserved for outcomes tied to attempting/completing the real submit click,
    # not the earlier, lower-stakes auto_select_slot-only steps above, which
    # already got their own "slot found" push before the browser ever opened.
    topic = read_config().get("ntfy_topic")
    if not wait_and_verify_summary(host, port, date_str, time_str, exam_label, target=page_target):
        print(
            "Summary screen didn't show the expected date/time/exam type — NOT clicking "
            f"'{CONFIRM_SUMMARY_TEXT}' automatically. Review it yourself before confirming."
        )
        push_ntfy(
            topic,
            "info-kierowca: reschedule needs review",
            f"Reached the summary screen but it didn't match the intended {exam_label} at "
            f"{date_str}, {time_str} — did NOT auto-confirm. Check the browser window.",
            priority="urgent",
        )
        return
    print("Summary screen matches the intended slot.")
    recorder.state("VERIFY_SUMMARY")
    print(f"transaction={recorder.transaction_id} state=summary_verified")
    # Written right before attempting the real submit click, regardless of its
    # outcome — see notifier.confirm_reschedule_cooldown_active(), which this
    # gates: a confirm attempt whose own result is uncertain (verification
    # below can time out even on a real success) must not let the very next
    # poll cycle immediately attempt another one on some other nearby slot.
    record_confirm_cooldown()
    try:
        recorder.data["post_submit_pages"].append(rt.capture_page(host, port, page_target, 0))
    except Exception as exc:
        recorder.state("pre_submit_snapshot_unavailable", error=type(exc).__name__)
    observer = None
    try:
        observer = cdp_client.NetworkObserver(host, port, page_target).start()
    except Exception as exc:
        recorder.state("network_observer_unavailable", error=type(exc).__name__)
    if not wait_and_click_enabled(host, port, CONFIRM_SUMMARY_TEXT, target=page_target):
        if observer: recorder.data["network_events"] = observer.stop()
        recorder.data["final_outcome"] = rt.ERROR
        diagnostic_path = recorder.save()
        print(f"Reschedule diagnostic saved to: {diagnostic_path}")
        print(
            f"Couldn't click '{CONFIRM_SUMMARY_TEXT}' automatically — confirm it yourself "
            "if the summary looks right."
        )
        push_ntfy(
            topic,
            "info-kierowca: couldn't auto-confirm",
            f"On the summary screen for {exam_label} at {date_str}, {time_str} but couldn't click "
            "the final confirm button automatically. Check the browser window.",
            priority="urgent",
        )
        return
    recorder.state("SUBMIT_CURRENT_FINAL_BUTTON")
    print(f"transaction={recorder.transaction_id} state=submit_clicked")
    print(f"Clicked '{CONFIRM_SUMMARY_TEXT}'. Waiting for booking outcome.")
    try:
        outcome = rt.run_post_submit(host, port, page_target, recorder)
    except Exception as exc:
        outcome = rt.UNKNOWN
        recorder.state("observation_error", error=type(exc).__name__)
        recorder.data["final_outcome"] = outcome
    finally:
        if observer:
            recorder.data["network_events"] = observer.stop()
    record_confirm_cooldown(outcome)
    diagnostic_path = recorder.save()
    print(f"transaction={recorder.transaction_id} outcome={outcome.lower()}")
    print(f"Reschedule diagnostic saved to: {diagnostic_path}")
    if outcome == rt.VERIFIED_SUCCESS:
        print(f"Verified on /cases: {exam_label} at {date_str}, {time_str} is active and confirmed.")
        update_slot_date_for_outcome(outcome, dt.date().isoformat())
        push_ntfy(
            topic,
            "info-kierowca: reschedule confirmed",
            f"Booked {exam_label} at {date_str}, {time_str}. current_slot_date updated.",
            priority="default",
        )
    elif outcome == rt.NEEDS_FURTHER_CONFIRMATION:
        push_ntfy(
            topic, "info-kierowca: additional reschedule step",
            "Reschedule reached an additional step and needs review. The browser has been "
            f"left open and a diagnostic trace was saved to {diagnostic_path}.", priority="urgent")
    elif outcome == rt.VERIFIED_UNCHANGED:
        push_ntfy(
            topic, "info-kierowca: booking unchanged",
            f"The previous booking remained active after attempting {exam_label} at {date_str}, "
            f"{time_str}. The browser remains open; diagnostic: {diagnostic_path}.", priority="urgent")
    else:
        print(
            "Couldn't confirm the new booking on /cases automatically — check the site and, "
            "if it did go through, update 'Date of your current booked slot' in Settings "
            "yourself."
        )
        push_ntfy(
            topic,
            "info-kierowca: reschedule outcome unknown",
            f"Clicked the current final control for {exam_label} at {date_str}, {time_str}, but "
            f"the booking outcome is unverified. Browser left open; diagnostic: {diagnostic_path}. "
            "current_slot_date was NOT updated.",
            priority="urgent",
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
            "(see the login flow or tools/pull_session_cookies.py)."
        )
    session = json.loads(cdp_client.SESSION_FILE.read_text())
    cookies = session.get("cookies", {})
    missing = cdp_client.COOKIE_NAMES - cookies.keys()
    if missing:
        raise SystemExit(
            f"session.json is missing {sorted(missing)} — run the login flow "
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
    page_target = cdp_client.create_page_target("127.0.0.1", args.port)
    cdp_client.navigate_target("127.0.0.1", args.port, page_target, args.url)
    cdp_client.bring_target_to_front("127.0.0.1", args.port, page_target)

    baseline_booking = []
    baseline_booking_candidates = []
    if args.target_slot and args.confirm_reschedule:
        try:
            baseline_booking, baseline_booking_candidates = rt.capture_stable_booking_baseline(
                "127.0.0.1", args.port, page_target
            )
            print(f"Captured {len(baseline_booking)} structural baseline booking card(s).")
        except Exception as exc:
            print(f"Couldn't capture baseline booking cards ({type(exc).__name__}); verification will fail closed.")

    print(f"Chrome opened at {args.url}, logged in using {cdp_client.SESSION_FILE}.")
    if args.no_auto_click:
        print("Skipping the Zmień termin auto-click-through (--no-auto-click).")
    elif wait_and_click("127.0.0.1", args.port, CHANGE_DATE_TEXT, target=page_target):
        print(f"Clicked '{CHANGE_DATE_TEXT}'.")
        if wait_and_click("127.0.0.1", args.port, CONFIRM_CHANGE_DATE_TEXT, target=page_target):
            print(f"Clicked '{CONFIRM_CHANGE_DATE_TEXT}'.")
            if args.target_slot:
                try_select_target_slot(
                    "127.0.0.1", args.port, args.target_slot, confirm=args.confirm_reschedule,
                    page_target=page_target, baseline_booking=baseline_booking,
                    baseline_booking_candidates=baseline_booking_candidates,
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
