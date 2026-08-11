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

from info_kierowca_notifier.browser import cdp as cdp_client
from info_kierowca_notifier.browser.clicking import (
    CLICKABLE_HELPERS_JS,
    sanitize_click_diagnostics,
)
from info_kierowca_notifier.browser.chrome import (
    chrome_debugging_args,
    ensure_private_profile_dir,
    find_chrome,
)
from info_kierowca_notifier import ntfy_transport
from info_kierowca_notifier.booking import transaction as rt

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
    return CLICKABLE_HELPERS_JS + """
(function(text) { return __ikw_clickByText(text, 'button, a, [role="button"]', false, false); })(%s)
""" % json.dumps(text)


# notifier.py's hit_dicts use the search API's own exam_type values
# ("Theoretical"/"Practice"); these are the labels the reschedule modal
# renders in each slot row (confirmed from screenshots, not live DOM).
EXAM_TYPE_LABELS_PL = {
    "Theoretical": "Egzamin teoretyczny",
    "Practice": "Egzamin praktyczny",
}


SLOT_CANDIDATE_SELECTOR = (
    'button, a, [role="button"], [role="radio"], [role="row"], '
    'input[type="radio"], label, li, tr, section, article, div, span'
)
# How much text a container may hold and still be treated as "the date group".
# Generous (a day can list many slots) but far short of a whole page, so a
# body-sized wrapper can't pose as the group.
MAX_DATE_GROUP_TEXT = 4000
MAX_SLOT_ROW_TEXT = 300


def select_slot_js(exam_label, time_str, date_str, center=""):
    # Confirmed live 2026-07-28 that the first version of this (querying
    # `input[type="radio"]` and walking up 6 ancestor levels for matching
    # text) failed to select a slot that was visibly present and correctly
    # labeled — the selection circle in this modal isn't necessarily a real
    # `<input type="radio">`, and by user confirmation the whole slot row
    # is clickable, not just the circle. So this no longer looks for a
    # radio element at all: it reuses the same find-the-most-specific-
    # matching-element-then-walk-up-to-a-clickable-ancestor pattern as
    # click_text_js/__ikw_findAndClick (already proven live for the
    # login/Zmień termin click-throughs elsewhere in this project).
    #
    # Rewritten again 2026-08-11 after a security review and a code review
    # independently found the same shape of bug: the match was
    # exam-label + time *anywhere in the document*, so
    #   * a row under a *different* date group could win — the target date was
    #     only ever used to expand a group, never as part of the row match, and
    #     the `t.length <= best[1].length` tie-break preferred the *last* equal
    #     length match in document order, i.e. the later date; and
    #   * the exam centre was never compared at all, even though the hit dict
    #     carries it, so a same date/time/type slot at the wrong centre (in
    #     practice the existing booking's own) selected silently.
    # Both are now structural rather than heuristic: the query is scoped to the
    # subtree of the *smallest visible element containing the target date, the
    # exam label and the time together*, the row must be unambiguous inside
    # that subtree, and any exam centre named within the group must be
    # compatible with the target's. Anything ambiguous returns a diagnostic
    # instead of clicking — selecting nothing is always recoverable (the user
    # picks by hand), selecting the wrong row is not once --confirm-reschedule
    # is on.
    return CLICKABLE_HELPERS_JS + rt.CENTER_HELPERS_JS + """
(function(dateStr, examLabel, timeStr, wantedCenter, maxGroupText, maxRowText, selector) {
  function diag(reason, el, text) {
    var out = __ikw_diagnostics('%s', text || '', el || null, reason);
    out.slot_date = dateStr; out.slot_time = timeStr;
    return out;
  }
  function disjoint(a, b) { return !a.contains(b) && !b.contains(a); }
  function smallest(list) {
    var best = null;
    for (var i = 0; i < list.length; i++) {
      if (!best || __ikw_text(list[i]).length < __ikw_text(best).length) best = list[i];
    }
    return best;
  }
  var all = document.querySelectorAll(selector);
  var groups = [];
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (!__ikw_isVisible(el)) continue;
    var t = __ikw_text(el);
    if (!t || t.length > maxGroupText) continue;
    if (t.indexOf(dateStr) === -1 || t.indexOf(examLabel) === -1 || t.indexOf(timeStr) === -1) continue;
    groups.push(el);
  }
  if (!groups.length) return diag('date_group_not_found');
  var group = smallest(groups);
  for (var g = 0; g < groups.length; g++) {
    if (disjoint(groups[g], group)) return diag('ambiguous_date_group', group, __ikw_text(group));
  }
  // The group itself counts as a candidate row: a date group holding exactly
  // one slot can legitimately be the same element as its row.
  var scoped = [group].concat(Array.prototype.slice.call(group.querySelectorAll(selector)));
  var rows = [];
  for (var j = 0; j < scoped.length; j++) {
    var row = scoped[j];
    if (!__ikw_isVisible(row)) continue;
    var rt_ = __ikw_text(row);
    if (!rt_ || rt_.length > maxRowText) continue;
    if (rt_.indexOf(examLabel) === -1 || rt_.indexOf(timeStr) === -1) continue;
    rows.push(row);
  }
  if (!rows.length) return diag('slot_row_not_found', group, '');
  var chosen = smallest(rows);
  for (var r = 0; r < rows.length; r++) {
    if (disjoint(rows[r], chosen)) return diag('ambiguous_slot_row', chosen, __ikw_text(chosen));
  }
  var verdict = __ikw_centerVerdict(__ikw_text(group), wantedCenter);
  if (verdict === 'conflict') return diag('center_conflict', chosen, __ikw_text(chosen));
  var clickable = __ikw_clickableAncestor(chosen);
  if (__ikw_isExcludedControl(chosen, clickable)) return diag('excluded_control', clickable, __ikw_text(chosen));
  var result = diag('clicked', clickable, __ikw_text(chosen));
  result.center_verdict = verdict;
  clickable.click();
  result.clicked = true;
  return result;
})(%s, %s, %s, %s, %s, %s, %s)
""" % (
        "select-slot",
        json.dumps(date_str), json.dumps(exam_label), json.dumps(time_str),
        json.dumps(center or ""), MAX_DATE_GROUP_TEXT, MAX_SLOT_ROW_TEXT,
        json.dumps(SLOT_CANDIDATE_SELECTOR),
    )


def _print_click_result(result):
    print(
        "browser click result "
        f"label={result.get('requested_label')!r} "
        f"matched={result.get('matched_text')!r} "
        f"url={result.get('page_url')!r} host={result.get('page_host')!r} "
        f"tag={result.get('tag')!r} "
        f"id={result.get('element_id')!r} class={result.get('element_class')!r} "
        f"href={result.get('href')!r}"
    )


def _print_abstention(result, seen):
    """Log *why* a fail-closed check refused, once per distinct reason.

    Every check in this file abstains rather than guesses, so without this a
    refusal is indistinguishable from "the page never rendered" in
    RESCHEDULE_LOG_FILE — and telling `ambiguous_date_group` (our matching is
    too loose for this DOM) apart from `center_conflict` (the picker is showing
    a different exam centre than the hit claimed) is the whole point of having
    separate reasons.
    """
    if not isinstance(result, dict):
        return
    reason = result.get("reason")
    if not reason or reason in seen:
        return
    seen.add(reason)
    safe = sanitize_click_diagnostics(result)
    print(
        f"browser check abstained reason={reason!r} "
        f"label={safe.get('requested_label')!r} matched={safe.get('matched_text')!r} "
        f"tag={safe.get('tag')!r} class={safe.get('element_class')!r}"
    )


def _poll_until_truthy(host, port, js, timeout=20, target=None):
    """Evaluate `js` in the page every 0.5s until it returns truthy or
    `timeout`s elapse. Shared body of every wait_*/wait_and_click below: this
    SPA renders content asynchronously after navigation or a prior click, so a
    selector often isn't in the DOM on the first frame. Exceptions are
    swallowed and retried (the page may be mid-navigation/render); returns True
    on the first truthy result, False if it never comes.

    Safe to retry precisely because everything routed through here is either
    read-only or idempotent-by-matching. The one click that is neither — the
    final confirm — deliberately does NOT use this function; see
    click_confirm_once().
    """
    deadline = time.monotonic() + timeout
    reasons_seen = set()
    while time.monotonic() < deadline:
        try:
            result = cdp_client.evaluate_in_page(host, port, js, target=target)
            if result is True or (isinstance(result, dict) and result.get("clicked")):
                if isinstance(result, dict):
                    _print_click_result(sanitize_click_diagnostics(result))
                return True
            _print_abstention(result, reasons_seen)
        except Exception:
            pass
        time.sleep(0.5)
    return False


def wait_and_select_slot(host, port, exam_label, time_str, date_str, center="",
                         timeout=20, target=None):
    """Same polling shape as wait_and_click(), for select_slot_js() instead —
    the matching slot row may not be in the DOM yet right after the date
    group is expanded."""
    return _poll_until_truthy(
        host, port, select_slot_js(exam_label, time_str, date_str, center), timeout, target
    )


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
    return CLICKABLE_HELPERS_JS + """
(function(text) { return __ikw_clickByText(text, 'button, [role="button"]', true, true); })(%s)
""" % json.dumps(text)


def wait_and_click_enabled(host, port, text, timeout=20, target=None):
    """Same polling shape as wait_and_click(), for click_enabled_button_js()
    instead — the target button needs a moment to go from disabled to
    enabled after whatever step precedes it completes."""
    return _poll_until_truthy(host, port, click_enabled_button_js(text), timeout, target)


# --- the one non-idempotent click in this project ---------------------------
# CONFIRM_SUMMARY_TEXT submits the reservation change. Every other wait_* here
# polls happily because re-running its JS either finds nothing or clicks the
# same still-present control again harmlessly; this one cannot, and the retry
# loop had a real double-submit hole in it: cdp.cdp_call()'s socket carries a
# 5s timeout, _poll_until_truthy() swallowed *every* exception including
# socket.timeout, and a lost *response* says nothing about whether the JS ran.
# Runtime.evaluate executes synchronously in the page regardless of whether we
# ever read the reply, so a timed-out confirm may well have clicked — and the
# next poll iteration would have clicked it again.
#
# The fix is to make the page itself the record of what happened: the marker is
# set immediately *before* .click() and cleared again only if the click
# demonstrably didn't fire. Because the whole expression runs to completion in
# the page no matter what happens to our socket, "marker present" means exactly
# "a click fired", and it survives the navigation the confirm click is expected
# to cause (sessionStorage is same-origin and outlives a document; the window
# flag is a fallback for contexts where storage throws). A retry therefore only
# ever happens on positive evidence that nothing was clicked yet.
CONFIRM_MARKER_KEY = "__ikw_confirm_click_fired"
CONFIRM_CLICKED = "clicked"
CONFIRM_ALREADY_CLICKED = "already_clicked"

CONFIRM_MARKER_JS = """
function __ikw_confirmMarker(action) {
  var KEY = %s, stored = null;
  try {
    if (action === 'set') window.sessionStorage.setItem(KEY, String(Date.now()));
    else if (action === 'clear') window.sessionStorage.removeItem(KEY);
    stored = window.sessionStorage.getItem(KEY);
  } catch (e) { stored = null; }
  if (action === 'set') window[KEY] = true;
  else if (action === 'clear') window[KEY] = false;
  return !!stored || !!window[KEY];
}
""" % json.dumps(CONFIRM_MARKER_KEY)


def confirm_marker_js(action):
    """`get` reads the marker, `clear` resets it — both side-effect-free for
    the booking itself, so they are safe to retry however often."""
    return CONFIRM_MARKER_JS + "\n__ikw_confirmMarker(%s)" % json.dumps(action)


def confirm_click_js(text):
    return CLICKABLE_HELPERS_JS + CONFIRM_MARKER_JS + """
(function(text) {
  if (__ikw_confirmMarker('get')) {
    return __ikw_diagnostics(text, '', null, 'already_clicked');
  }
  __ikw_confirmMarker('set');
  var result = __ikw_clickByText(text, 'button, [role="button"]', true, true);
  if (!result || !result.clicked) __ikw_confirmMarker('clear');
  return result;
})(%s)
""" % json.dumps(text)


def reset_confirm_marker(host, port, target=None, evaluate=None):
    """Clear the marker before the confirm phase starts.

    A fresh CDP page target starts with empty sessionStorage, so this is
    belt-and-braces — but a marker left over from anything else would be read
    as "a confirm click already fired" and would make this run skip its own
    submit and report UNKNOWN. Best-effort: if the clear itself fails we keep
    going, and the worst case is that same fail-closed skip.
    """
    evaluate = evaluate or cdp_client.evaluate_in_page
    try:
        evaluate(host, port, confirm_marker_js("clear"), target=target)
        return True
    except Exception as e:
        print(f"Couldn't reset the confirm-click marker ({e!r}); continuing.")
        return False


def click_confirm_once(host, port, text, timeout=20, target=None,
                       evaluate=None, sleep=None, monotonic=None):
    """Click CONFIRM_SUMMARY_TEXT at most once, ever.

    Returns CONFIRM_CLICKED (we saw the click land), CONFIRM_ALREADY_CLICKED (a
    click fired but its response was lost — treat the submit as attempted and
    verify rather than retry), or None (never clicked; nothing was submitted).
    The clock/evaluate/sleep hooks exist so the retry logic can be tested
    without a browser.
    """
    evaluate = evaluate or cdp_client.evaluate_in_page
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    deadline = monotonic() + timeout
    reasons_seen = set()
    while monotonic() < deadline:
        try:
            already = evaluate(host, port, confirm_marker_js("get"), target=target)
        except Exception:
            # Read-only probe: a failure here tells us nothing, and retrying it
            # cannot submit anything. Never fall through to a click on it.
            sleep(0.5)
            continue
        if already:
            return CONFIRM_ALREADY_CLICKED
        try:
            result = evaluate(host, port, confirm_click_js(text), target=target)
        except Exception:
            # The click JS may or may not have run. Only the marker knows, and
            # the next iteration's probe is what reads it.
            sleep(0.5)
            continue
        if isinstance(result, dict) and result.get("clicked"):
            _print_click_result(sanitize_click_diagnostics(result))
            return CONFIRM_CLICKED
        _print_abstention(result, reasons_seen)
        sleep(0.5)
    return None


MODAL_SELECTOR = '[role="dialog"], [aria-modal="true"], dialog'


def verify_summary_js(date_str, time_str, exam_label, center=""):
    """The summary-modal check, scoped to the modal itself.

    Until 2026-08-11 this read `document.body`'s entire visible text, which
    made it far weaker than its own docstring claimed: the date-picker page is
    still mounted *behind* the modal, already showing the date group that
    select_slot_js() expanded, so the expected "date, time" and exam label
    could all be satisfied by the page behind the modal even when the modal
    itself showed a different slot — precisely the case this check exists to
    catch. It now finds the visible modal container (the same selector
    transaction.PAGE_SNAPSHOT_JS already records as `dialogs`) and reads only
    that subtree, and it additionally requires the exam centre to check out:

      * conflict (a different centre named in the modal, or on the page when
        the modal names none) -> refuse;
      * unknown (no centre named anywhere) -> also refuse, because this is the
        last gate before an irreversible submit and "the page never told us
        which centre this is" is not evidence that it is the right one.

    No modal, or more than one plausible modal, likewise refuses. Every one of
    those refusals is recoverable — the user confirms by hand — while a false
    positive here submits a real reservation change.
    """
    expected_datetime = f"{date_str}, {time_str}"
    return CLICKABLE_HELPERS_JS + rt.CENTER_HELPERS_JS + """
(function(expectedDateTime, examLabel, wantedCenter, modalSelector) {
  function diag(reason, el, text) { return __ikw_diagnostics('%s', text || '', el || null, reason); }
  var nodes = document.querySelectorAll(modalSelector), modals = [];
  for (var i = 0; i < nodes.length; i++) {
    if (__ikw_isVisible(nodes[i]) && __ikw_text(nodes[i])) modals.push(nodes[i]);
  }
  if (!modals.length) return diag('summary_modal_not_found');
  var matching = [];
  for (var m = 0; m < modals.length; m++) {
    var text = __ikw_text(modals[m]);
    if (text.indexOf(expectedDateTime) !== -1 && text.indexOf(examLabel) !== -1) matching.push(modals[m]);
  }
  if (!matching.length) return diag('summary_mismatch', modals[0], __ikw_text(modals[0]));
  var outermost = matching[0];
  for (var n = 1; n < matching.length; n++) {
    if (matching[n].contains(outermost)) outermost = matching[n];
    else if (!outermost.contains(matching[n])) return diag('ambiguous_summary_modal', outermost, '');
  }
  var modalText = __ikw_text(outermost);
  var verdict = __ikw_centerVerdict(modalText, wantedCenter);
  // The modal may legitimately not repeat the centre; the page it sits on
  // belongs to one specific booking, so it is the next best evidence.
  if (verdict === 'unknown') verdict = __ikw_centerVerdict(__ikw_text(document.body), wantedCenter);
  if (verdict !== 'match') return diag('center_' + verdict, outermost, '');
  return true;
})(%s, %s, %s, %s)
""" % (
        "verify-summary",
        json.dumps(expected_datetime), json.dumps(exam_label),
        json.dumps(center or ""), json.dumps(MODAL_SELECTOR),
    )


def wait_and_verify_summary(host, port, date_str, time_str, exam_label, center="",
                            timeout=10, target=None):
    """Safety check run before CONFIRM_SUMMARY_TEXT is ever clicked: does the
    summary modal's own visible text actually show the date, time, exam type
    and exam centre we intended to select? This is the one guard against
    select_slot_js() having matched the wrong row before a real reservation
    change gets submitted — unlike every earlier step in this flow, that click
    can't be undone by just closing the tab. See verify_summary_js() for what
    each refusal means.
    """
    return _poll_until_truthy(
        host, port, verify_summary_js(date_str, time_str, exam_label, center), timeout, target
    )


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
        write_private_json(CONFIG_FILE, config)
        print(f"Updated current_slot_date to {new_date_iso} in config.json.")
    except Exception as e:
        print(
            f"Booking confirmed, but couldn't update current_slot_date automatically ({e!r}) "
            "— update 'Date of your current booked slot' in Settings yourself."
        )


def write_private_json(path, payload):
    """Atomic replace via a temp file that is owner-only from the moment it
    exists.

    The previous shape here (`tmp.write_text(...)` then `chmod` on the final
    path) left two windows open: the temp file was created with whatever the
    process umask allowed — 0644 on a typical desktop — and it held the entire
    contents of config.json (PKK number, centres, ntfy topic) for as long as
    the write took, world-readable on a multi-user box. os.open() with an
    explicit mode closes both, the same way transaction.DiagnosticRecorder.save()
    already does for its trace files. The trailing chmod stays for the case
    where `path` already existed with looser permissions.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if os.name == "posix":
        path.chmod(0o600)
    return path


def record_confirm_cooldown(outcome="PENDING"):
    """Arm/refresh the 15-minute confirm gate, and say whether it is armed.

    Returns True only when the file is actually on disk with the new
    timestamp. It used to swallow every failure (`except Exception: pass`),
    which meant a read-only or full state dir silently removed the one gate
    standing between an uncertain confirm attempt and the next poll cycle
    attempting another one — with nothing anywhere to notice. Callers now
    decide: booking.launch.trigger_open_browser() withholds --confirm-reschedule
    entirely if it can't arm the gate, and try_select_target_slot() refuses to
    click submit for the same reason.
    """
    try:
        payload = {"attempted_at": datetime.now().isoformat(), "outcome": outcome}
        write_private_json(RESCHEDULE_CONFIRM_COOLDOWN_FILE, payload)
        return True
    except Exception as e:
        print(
            f"Couldn't write the confirm-reschedule cooldown file ({e!r}) — "
            "the 15-minute gate on repeat confirm attempts is NOT armed."
        )
        return False


def release_confirm_cooldown():
    """Drop the gate booking.launch armed at launch time.

    trigger_open_browser() arms the cooldown *before* spawning this process (a
    ~90s window used to sit between launch and the child's own write, guarded
    only by a port probe that fails open while Chrome is still starting), so a
    run that gives up before ever reaching the submit click would otherwise
    cost 15 minutes of confirm attempts for nothing. Only ever called from
    paths where nothing was submitted; once the marker for the real click goes
    down, the gate stays up regardless of outcome.
    """
    try:
        RESCHEDULE_CONFIRM_COOLDOWN_FILE.unlink(missing_ok=True)
        return True
    except Exception as e:
        print(f"Couldn't clear the confirm-reschedule cooldown file ({e!r}).")
        return False


def target_beats_current_slot(target_dt, config=None):
    """Re-check, right before the submit click, that the slot is still an
    improvement on the booking we hold.

    notifier.run_check() decided this slot was urgent, but that was in another
    process, possibly a minute or more earlier, and this process only ever
    re-read config.json for the ntfy topic. In between: a previous confirm may
    have succeeded while its verification timed out (leaving current_slot_date
    stale then updated late), or the user may have changed the date in Settings
    themselves. Mirrors notifier.is_urgent()'s strict comparison — same
    midnight cutoff, same "strictly earlier date only" rule — deliberately
    rather than importing it, since notifier imports this module.

    Fails closed: an unreadable or absent current_slot_date returns False. We
    cannot show the slot is better, so we do not submit.
    """
    config = read_config() if config is None else config
    raw = config.get("current_slot_date")
    if not raw:
        return False
    try:
        cutoff = datetime.fromisoformat(str(raw)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    except Exception:
        return False
    return target_dt < cutoff


def diagnostic_reference(path):
    """How a diagnostic file is named in an ntfy push.

    Only the file name, never the path: the full path starts with the user's
    home directory, so interpolating it (as this did until 2026-08-11) shipped
    the local OS username to ntfy.sh — a third-party server this project
    otherwise tells only the times of exam slots. Local stdout keeps the full
    path; it goes to RESCHEDULE_LOG_FILE on the user's own machine.
    """
    if not path:
        return "no diagnostic file"
    return f"{os.path.basename(str(path))} (in the reschedule-diagnostics folder)"


def save_diagnostic(recorder):
    """Best-effort recorder.save(); never let it break the outcome handling.

    save() opens with O_EXCL and writes into the state dir, so it can raise on
    a name collision, a full disk, or an unwritable directory. It used to be
    called bare, right after the irreversible submit click — so an exception
    propagated out of try_select_target_slot(), skipping the current_slot_date
    update *and* every push below it, at the exact moment the user most needs
    to be told what just happened to their booking.
    """
    try:
        return recorder.save()
    except Exception as e:
        print(f"Couldn't save the reschedule diagnostic trace ({e!r}).")
        return None


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
    auto_select_slot flag is on — see booking.launch.trigger_open_browser()).

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
    2026-07-20 — goes one click further, behind three gates added 2026-08-11:
    the target must still beat config.json's current_slot_date
    (target_beats_current_slot(), re-read here rather than trusted from the
    launch that happened minutes ago), the summary *modal* must show the
    intended date/time/exam type/centre (wait_and_verify_summary(), scoped to
    the modal rather than the whole page), and the repeat-confirm cooldown must
    be armed. Only then does it click CONFIRM_SUMMARY_TEXT ("Potwierdź i
    przejdź dalej") — via click_confirm_once(), never the retrying poller, so a
    lost CDP response can't turn into a second submission. That click submits
    the actual reservation change, the one action in this file that can't be
    undone by closing the tab. Any gate failing, or the button never becoming
    clickable, stops short of it every time, and every stop before the click
    releases the launch-time cooldown so the next cycle may try again.

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
    unknown: (1) RESCHEDULE_CONFIRM_COOLDOWN_FILE, which
    booking.launch.confirm_reschedule_cooldown_active() checks before ever
    passing --confirm-reschedule again — armed by trigger_open_browser() at
    launch time (see record_confirm_cooldown()/release_confirm_cooldown() for
    why it moved there) and refreshed here right before the submit click, with
    the outcome, once one is known; (2) a push notification
    (push_ntfy(), reusing config's existing ntfy_topic) fires for every
    outcome from that point on — summary mismatch, confirm button
    unclickable, confirmed-but-unverified, or confirmed-and-verified —
    since none of those are things that should only be discoverable by
    someone happening to be watching the Chrome window.
    """
    def abandon():
        """Give the launch-time confirm cooldown back — nothing was submitted."""
        if confirm:
            release_confirm_cooldown()

    try:
        target = json.loads(target_slot_json)
        dt = datetime.fromisoformat(target["datetime"])
        exam_label = EXAM_TYPE_LABELS_PL.get(target["exam_type"], target["exam_type"])
    except Exception as e:
        print(f"Couldn't parse --target-slot ({e!r}) — pick the slot yourself.")
        abandon()
        return
    date_str = dt.strftime("%d/%m/%Y")
    time_str = dt.strftime("%H:%M")
    center = target.get("center") or target.get("word_id") or target.get("word") or ""
    transaction_target = {"exam_type": exam_label, "date": date_str, "time": time_str,
                          "center": center, "word_id": center}
    recorder = rt.DiagnosticRecorder(
        transaction_target, baseline_booking or [],
        baseline_booking_candidates=baseline_booking_candidates or [],
    ) if confirm else None
    if recorder:
        recorder.state("CAPTURE_BASELINE_BOOKING")
        print(f"transaction={recorder.transaction_id} state=baseline_captured cards={len(recorder.baseline_booking)}")
    print(f"Looking for {exam_label} at {time_str} on {date_str}"
          + (f" ({center})" if center else "") + "...")
    if not wait_and_click(host, port, date_str, target=page_target):
        print(f"Couldn't find the '{date_str}' date group automatically — pick the slot yourself.")
        abandon()
        return
    print(f"Expanded '{date_str}'.")
    if not wait_and_select_slot(host, port, exam_label, time_str, date_str, center,
                                target=page_target):
        print(
            f"Couldn't unambiguously find a {exam_label} row at {time_str} inside the "
            f"'{date_str}' group at {center or 'the expected centre'} (already taken, or the "
            "page didn't match closely enough to be safe) — pick the slot yourself."
        )
        abandon()
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
        abandon()
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
    # Re-check the premise before doing anything irreversible. run_check()
    # judged this slot urgent in a different process, possibly minutes ago; the
    # only thing this process had re-read from config.json since was the ntfy
    # topic. See target_beats_current_slot() for the failure modes this covers.
    if not target_beats_current_slot(dt):
        print(
            f"{date_str} is no longer earlier than the booking recorded in config.json "
            f"(current_slot_date) — NOT clicking '{CONFIRM_SUMMARY_TEXT}'. Confirm by hand if "
            "you still want this slot."
        )
        recorder.state("stale_target_slot")
        push_ntfy(
            topic,
            "info-kierowca: reschedule skipped",
            f"Reached the summary screen for {exam_label} at {date_str}, {time_str}, but it no "
            "longer beats the booking date on file — did NOT auto-confirm.",
            priority="urgent",
        )
        abandon()
        return
    if not wait_and_verify_summary(host, port, date_str, time_str, exam_label, center,
                                   target=page_target):
        print(
            "Summary modal didn't show the expected date/time/exam type/centre (or no modal "
            f"was found at all) — NOT clicking '{CONFIRM_SUMMARY_TEXT}' automatically. Review "
            "it yourself before confirming; the reason is logged just above."
        )
        push_ntfy(
            topic,
            "info-kierowca: reschedule needs review",
            f"Reached the summary screen but it didn't match the intended {exam_label} at "
            f"{date_str}, {time_str} — did NOT auto-confirm. Check the browser window.",
            priority="urgent",
        )
        abandon()
        return
    print("Summary screen matches the intended slot.")
    recorder.state("VERIFY_SUMMARY")
    print(f"transaction={recorder.transaction_id} state=summary_verified")
    # Refreshed right before attempting the real submit click, regardless of its
    # outcome — see booking.launch.confirm_reschedule_cooldown_active(), which this
    # gates: a confirm attempt whose own result is uncertain (verification
    # below can time out even on a real success) must not let the very next
    # poll cycle immediately attempt another one on some other nearby slot.
    # trigger_open_browser() already armed it at launch; if the file can't be
    # written at all we stop here rather than submit with no gate behind us.
    if not record_confirm_cooldown():
        recorder.state("cooldown_unarmed")
        push_ntfy(
            topic,
            "info-kierowca: reschedule aborted",
            f"Couldn't arm the repeat-confirm safety gate, so {exam_label} at {date_str}, "
            f"{time_str} was NOT auto-confirmed. Confirm by hand if you want it.",
            priority="urgent",
        )
        return
    try:
        recorder.data["post_submit_pages"].append(rt.capture_page(host, port, page_target, 0))
    except Exception as exc:
        recorder.state("pre_submit_snapshot_unavailable", error=type(exc).__name__)
    observer = None
    try:
        observer = cdp_client.NetworkObserver(host, port, page_target).start()
    except Exception as exc:
        recorder.state("network_observer_unavailable", error=type(exc).__name__)
    reset_confirm_marker(host, port, page_target)
    click_result = click_confirm_once(host, port, CONFIRM_SUMMARY_TEXT, target=page_target)
    if click_result is None:
        if observer: recorder.data["network_events"] = observer.stop()
        recorder.data["final_outcome"] = rt.ERROR
        diagnostic_path = save_diagnostic(recorder)
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
    recorder.state("SUBMIT_CURRENT_FINAL_BUTTON", click_result=click_result)
    print(f"transaction={recorder.transaction_id} state=submit_clicked detail={click_result}")
    if click_result == CONFIRM_ALREADY_CLICKED:
        print(
            "The confirm click had already fired (its CDP response was lost) — not clicking "
            "again; verifying what the site actually did instead."
        )
    print(f"Clicked '{CONFIRM_SUMMARY_TEXT}'. Waiting for booking outcome.")
    # Everything from here on is best-effort reporting about a change that has
    # already been submitted. Nothing in it may be allowed to raise past this
    # point: an exception used to skip the current_slot_date update *and* every
    # push below, leaving the user with an altered booking and no notification.
    outcome = rt.UNKNOWN
    try:
        outcome = rt.run_post_submit(host, port, page_target, recorder)
    except Exception as exc:
        outcome = rt.UNKNOWN
        try:
            recorder.state("observation_error", error=type(exc).__name__)
            recorder.data["final_outcome"] = outcome
        except Exception:
            pass
    finally:
        try:
            if observer:
                recorder.data["network_events"] = observer.stop()
        except Exception:
            pass
    try:
        record_confirm_cooldown(outcome)
    except Exception:
        pass
    diagnostic_path = save_diagnostic(recorder)
    diagnostic_note = diagnostic_reference(diagnostic_path)
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
            f"left open and a diagnostic trace was saved ({diagnostic_note}).", priority="urgent")
    elif outcome == rt.VERIFIED_UNCHANGED:
        push_ntfy(
            topic, "info-kierowca: booking unchanged",
            f"The previous booking remained active after attempting {exam_label} at {date_str}, "
            f"{time_str}. The browser remains open; diagnostic: {diagnostic_note}.", priority="urgent")
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
            f"the booking outcome is unverified. Browser left open; diagnostic: {diagnostic_note}. "
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
    # ensure_private_profile_dir() rather than a bare mkdir(): this profile is
    # about to have live info-kierowca.pl session cookies injected into it, and
    # a plain mkdir() takes whatever the umask allows (0755 on a typical
    # desktop), leaving another local account able to read the cookie jar of a
    # logged-in session. auth/session.py already used the private helper for
    # its own profile; this one had been left on the default. Launch flags come
    # from chrome_debugging_args() for the same reason — one place to pin
    # --remote-debugging-address=127.0.0.1 rather than two copies that drift.
    ensure_private_profile_dir(PROFILE_DIR)
    subprocess.Popen(
        [
            chrome,
            *chrome_debugging_args(args.port, PROFILE_DIR),
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
