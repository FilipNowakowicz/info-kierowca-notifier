#!/usr/bin/env python3
"""Launches a fresh Chrome window pre-authenticated to info-kierowca.pl by
injecting the cookies already saved in session.json — skips the login/QR
step entirely.

Run by hand:

    python -m info_kierowca_notifier.booking.reservation

Uses a dedicated throwaway profile (separate from your regular browsing and
from auth.session's own profile) so it never fights over a
profile lock. Reads only from session.json; nothing but the two
info-kierowca.pl session cookies is sent anywhere, and only to
info-kierowca.pl itself — see browser.cdp's docstring for the
debug-port security note. The one write this file can make is conditional:
with --confirm-reschedule it can submit the verified preliminary reservation
through the portal's own final button.  The resulting Reservations/create
response is observed through CDP without logging its request payload (which
contains PKK/profile data).  The browser is left open for any subsequent
confirmation or payment required by the portal.
"""
import argparse
import json
import re
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

from info_kierowca_notifier.paths import CATEGORIES_FILE, CONFIG_FILE, RESCHEDULE_CONFIRM_COOLDOWN_FILE, STATE_DIR  # noqa: E402

PROFILE_DIR = STATE_DIR / "chrome-reservation-profile"

# Distinct from pull_session_cookies.py's manual default (9222) and
# auth.session's (9333) so none of the three ever fight over a
# port if run at the same time.
DEFAULT_PORT = 9555
DEFAULT_URL = "https://info-kierowca.pl/reservation"


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


def click_date_expansion_header_js(date_str):
    """Click the Angular Material day accordion header for an exact date.

    Live portal DOM (2026-10-11) uses mat-expansion-panel-header[role=button]
    with the date in a child span and aria-expanded as the authoritative state.
    Generated ids such as mat-expansion-panel-header-0 are intentionally ignored.
    """
    return CLICKABLE_HELPERS_JS + r"""
(function(dateStr) {
  var headers = document.querySelectorAll('mat-expansion-panel-header[role="button"]');
  var matches = [];
  for (var i = 0; i < headers.length; i++) {
    var h = headers[i];
    if (__ikw_text(h) !== dateStr) continue;
    if (h.getAttribute('aria-disabled') === 'true') continue;
    var r = h.getBoundingClientRect();
    // The stepper keeps inactive steps in the DOM off-screen horizontally.
    // Accept a day below the fold, but only from the active horizontal panel.
    if (!(r.width > 0 && r.height > 0 && r.right > 0 && r.left < window.innerWidth)) continue;
    matches.push(h);
  }
  if (matches.length !== 1) {
    var out = __ikw_diagnostics(dateStr, '', matches[0] || null,
      matches.length ? 'ambiguous_date_header' : 'date_header_not_found');
    out.matchCount = matches.length;
    return out;
  }
  var h = matches[0];
  var out = __ikw_diagnostics(dateStr, __ikw_text(h), h, 'date_header_found');
  out.ariaExpandedBefore = h.getAttribute('aria-expanded');
  out.ariaControls = h.getAttribute('aria-controls');
  if (out.ariaExpandedBefore === 'true') { out.clicked = true; out.alreadyExpanded = true; return out; }
  h.scrollIntoView({block:'center', inline:'nearest'});
  h.click();
  out.clicked = true;
  return out;
})(%s)
""" % json.dumps(date_str)


def date_expansion_open_js(date_str):
    return r"""
(function(dateStr) {
  var hs = document.querySelectorAll('mat-expansion-panel-header[role="button"]');
  for (var i = 0; i < hs.length; i++) {
    var h = hs[i], r = h.getBoundingClientRect();
    if ((h.textContent || '').trim() === dateStr &&
        r.width > 0 && r.height > 0 && r.right > 0 && r.left < window.innerWidth &&
        h.getAttribute('aria-expanded') === 'true') return true;
  }
  return false;
})(%s)
""" % json.dumps(date_str)


# notifier.py's hit_dicts use the search API's own exam_type values
# ("Theoretical"/"Practice"); these are the labels the reschedule modal
# renders in each slot row (confirmed from screenshots, not live DOM).
EXAM_TYPE_LABELS_PL = {
    "Theoretical": "Egzamin teoretyczny",
    "Practice": "Egzamin praktyczny",
    "TheoreticalWithPractice": "Egzamin łączony (Teoria + Praktyka)",
}

EXAM_TYPE_VALUES = {
    "Theoretical": "Theoretical",
    "Practice": "Practice",
    "TheoreticalWithPractice": "TheoreticalWithPractice",
    # Accept common notifier spellings for a combined search/result.
    "Combined": "TheoreticalWithPractice",
    "Both": "TheoreticalWithPractice",
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

  // Several rows can legitimately have the same date, exam type and time
  // (for example the WORD may offer different examination vehicles at 07:30).
  // auto-select is intentionally defined to choose the FIRST matching slot in
  // the order shown by the portal.  First reduce nested matches to the most
  // specific row candidates, then keep their DOM order and take candidate 0.
  // This does not use the volatile number of free places as a discriminator.
  var leafRows = [];
  for (var r = 0; r < rows.length; r++) {
    var hasMatchingDescendant = false;
    for (var q = 0; q < rows.length; q++) {
      if (q !== r && rows[r].contains(rows[q])) {
        hasMatchingDescendant = true;
        break;
      }
    }
    if (!hasMatchingDescendant) leafRows.push(rows[r]);
  }
  var ordered = leafRows.length ? leafRows : rows;
  ordered.sort(function(a, b) {
    if (a === b) return 0;
    var pos = a.compareDocumentPosition(b);
    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
  var chosen = ordered[0];
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


def _poll_until_truthy(host, port, js, timeout=20, target=None, strict=False):
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

    strict=True is for the wizard clicks leading up to that final one. Every
    step's "next" button and the final create button share the label
    "Zapisz i przejdź dalej", so retrying a step-3 click whose CDP response was
    lost (the click may well have run) could land on step 4 and create a
    reservation even with auto-confirm off. In strict mode an exception means
    "outcome unknown": stop and return False instead of retrying. Only a
    returned not-clicked result is retried.
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
        except Exception as exc:
            if strict:
                print(f"Click result unknown ({type(exc).__name__}) — not retrying; "
                      "finish this step yourself.")
                return False
        time.sleep(0.5)
    return False


def wait_and_select_slot(host, port, exam_label, time_str, date_str, center="",
                         timeout=20, target=None):
    """Same polling shape as wait_and_click(), for select_slot_js() instead —
    the matching slot row may not be in the DOM yet right after the date
    group is expanded."""
    return _poll_until_truthy(
        host, port, select_slot_js(exam_label, time_str, date_str, center), timeout, target,
        strict=True,
    )


def click_enabled_button_js(text):
    # Angular Material renders the desktop and mobile labels inside the same
    # <button>.  Consequently button.innerText can be e.g.
    # "Zapisz i przejdź dalej Następny arrow_forward", so an exact match
    # against the whole button can never find the visible desktop label.
    # Match the exact text on a descendant label/span first and click its
    # nearest button.  Keep the conservative enabled/visibility checks.
    return CLICKABLE_HELPERS_JS + r"""
(function(text) {
  if (__ikw_pageIsKnownError()) return __ikw_diagnostics(text, '', null, 'known_error_page');
  var nodes = document.querySelectorAll(
    'button .mdc-button__label span, button span.desktop, button span, [role="button"] span'
  );
  var matches = [];
  for (var i = 0; i < nodes.length; i++) {
    var label = nodes[i];
    if (!__ikw_isVisible(label) || __ikw_text(label) !== text) continue;
    var button = label.closest('button, [role="button"]');
    if (!button || !__ikw_isVisible(button)) continue;
    if (button.disabled || button.getAttribute('aria-disabled') === 'true') continue;
    if (__ikw_isExcludedControl(label, button)) continue;
    if (matches.indexOf(button) === -1) matches.push(button);
  }
  if (!matches.length) return __ikw_diagnostics(text, '', null, 'not_found');
  if (matches.length !== 1) return __ikw_diagnostics(text, '', null, 'ambiguous_match');
  var result = __ikw_diagnostics(text, text, matches[0], 'clicked');
  matches[0].click();
  result.clicked = true;
  return result;
})(%s)
""" % json.dumps(text)


def wait_and_click_enabled(host, port, text, timeout=20, target=None, strict=False):
    """Same polling shape as wait_and_click(), for click_enabled_button_js()
    instead — the target button needs a moment to go from disabled to
    enabled after whatever step precedes it completes. See
    _poll_until_truthy() for strict."""
    return _poll_until_truthy(
        host, port, click_enabled_button_js(text), timeout, target, strict=strict
    )


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
    # The stepper keeps every step mounted side-by-side.  On the live summary
    # page the real step-4 submit can be below the viewport while submit
    # buttons belonging to inactive steps sit far off-screen horizontally.
    # __ikw_clickByText(..., requireVisible=True) therefore rejected the real
    # final button.  Use the same proven active-column rule as Krok 1: exact
    # desktop label, rendered size, horizontal intersection; then scroll and
    # single-fire click under the sessionStorage marker.
    return CLICKABLE_HELPERS_JS + CONFIRM_MARKER_JS + r"""
(function(text) {
  if (__ikw_confirmMarker('get')) {
    return __ikw_diagnostics(text, '', null, 'already_clicked');
  }
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const inActiveColumn = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) !== 0 &&
           r.width > 0 && r.height > 0 && r.right > 0 && r.left < window.innerWidth;
  };
  const buttons = Array.from(document.querySelectorAll('button[type="submit"]'));
  const matches = buttons.filter(button => {
    if (!inActiveColumn(button)) return false;
    if (button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
    const label = button.querySelector('.mdc-button__label .desktop, span.desktop');
    return label && norm(label.textContent) === text;
  });
  if (matches.length !== 1) {
    const out = __ikw_diagnostics(text, '', matches[0] || null,
      matches.length ? 'ambiguous_final_submit' : 'final_submit_not_found');
    out.matchCount = matches.length;
    return out;
  }
  const button = matches[0];
  const out = __ikw_diagnostics(text, text, button, 'clicked');
  __ikw_confirmMarker('set');
  try {
    button.scrollIntoView({block:'center', inline:'nearest'});
    button.focus({preventScroll:true});
    button.click();
    out.clicked = true;
    return out;
  } catch (e) {
    __ikw_confirmMarker('clear');
    out.clicked = false;
    out.reason = 'final_click_exception';
    return out;
  }
})(%s)
""" % json.dumps(text)




CREATE_ENDPOINT_PATH = "/bknd/exam/api/v1/Reservations/create"


class ReservationCreateObserver:
    """Observe exactly one Reservations/create response on the retained page.

    Starts before the non-idempotent final click.  It records only response
    metadata and the bounded JSON response body; it never records the request
    payload (which contains the PKK/profile number).
    """
    def __init__(self, host, port, target):
        self.host, self.port, self.target = host, port, target
        self._manager = None
        self._sock = None
        self._thread = None
        self._stop = __import__("threading").Event()
        self._done = __import__("threading").Event()
        self.result = None

    def start(self):
        page = cdp_client.get_page_target(self.host, self.port, self.target.id)
        self._manager = cdp_client.cdp_socket(page.websocket_url)
        self._sock = self._manager.__enter__()
        cdp_client.cdp_call(self._sock, 1, "Network.enable", {
            "maxTotalBufferSize": 1024 * 1024,
            "maxResourceBufferSize": 512 * 1024,
        })
        import threading
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    @staticmethod
    def _is_create_url(url):
        try:
            from urllib.parse import urlparse
            parsed = urlparse(str(url or ""))
            return (parsed.hostname or "").lower() == "info-kierowca.pl" and parsed.path == CREATE_ENDPOINT_PATH
        except Exception:
            return False

    def _run(self):
        response = None
        request_id = None
        try:
            while not self._stop.is_set():
                msg = json.loads(cdp_client.ws_recv_message(self._sock))
                method = msg.get("method", "")
                params = msg.get("params", {})
                if method == "Network.responseReceived":
                    candidate = params.get("response", {})
                    if self._is_create_url(candidate.get("url")):
                        response = candidate
                        request_id = params.get("requestId")
                elif request_id and params.get("requestId") == request_id and method == "Network.loadingFinished":
                    body = ""
                    try:
                        body_result = cdp_client.cdp_call(self._sock, 9001, "Network.getResponseBody", {"requestId": request_id})
                        body = str(body_result.get("body", ""))[:16384]
                    except Exception:
                        body = ""
                    self.result = self._make_result(response or {}, body)
                    self._done.set()
                    return
                elif request_id and params.get("requestId") == request_id and method == "Network.loadingFailed":
                    self.result = {
                        "status": None, "ok": False, "network_error": str(params.get("errorText", ""))[:200],
                        "error_id": "", "error_type": None, "message": "",
                    }
                    self._done.set()
                    return
        except Exception as exc:
            if not self._stop.is_set():
                self.result = {"observer_error": repr(exc)[:300]}
                self._done.set()

    @staticmethod
    def _make_result(response, body):
        status = response.get("status")
        try:
            status = int(status)
        except Exception:
            pass
        out = {"status": status, "ok": isinstance(status, int) and 200 <= status < 300,
               "error_id": "", "error_type": None, "message": ""}
        try:
            payload = json.loads(body) if body else {}
            if isinstance(payload, dict):
                out["error_id"] = str(payload.get("id") or "")[:200]
                out["error_type"] = payload.get("type")
                out["message"] = _safe_portal_message(payload.get("message"))
        except Exception:
            pass
        return out

    def wait(self, timeout=10):
        self._done.wait(timeout)
        return self.result

    def stop(self):
        self._stop.set()
        if self._sock:
            try:
                import socket
                self._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=1)
        if self._manager:
            try:
                self._manager.__exit__(None, None, None)
            except Exception:
                pass
        return self.result


def post_create_status_js():
    """Read-only probe after the one final create click.

    The portal can either advance to step 5 or briefly render a Material
    dialog/snackbar with a server-side refusal.  Keep this probe read-only: it
    must never dismiss a dialog or click anything after the non-idempotent
    create action.
    """
    return r"""
(function() {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) !== 0 &&
           r.width > 0 && r.height > 0;
  };
  const body = norm(document.body && (document.body.innerText || document.body.textContent));
  const step5 = Array.from(document.querySelectorAll('mat-step-header, [role="tab"], .mat-step-header'))
    .some(el => visible(el) && /Potwierdzenie/i.test(norm(el.textContent)) &&
      (el.getAttribute('aria-selected') === 'true' || /mat-step-header-selected|mat-mdc-tab-active/.test(el.className || '')));
  if (step5 || (/Potwierdzenie/i.test(body) && !/Podsumowanie wstępnej rezerwacji/i.test(body)))
    return {status:'step5_reached', message:''};

  // Prefer transaction-result alerts/banners over persistent site chrome such
  // as the cookie-settings dialog.  The live portal reports reservation-create
  // refusals in a visible alert banner (for example "Rezerwacja nie powiodła
  // się ..."), while the cookie widget may remain aria-modal for the whole
  // session and must not be mistaken for the create result.
  const resultSelectors = [
    '[role="alert"]', '.alert', '.mat-mdc-snack-bar-container',
    'mat-snack-bar-container', '.mat-mdc-simple-snack-bar',
    '.mat-snack-bar-container'
  ];
  const resultNodes = [];
  resultSelectors.forEach(sel => document.querySelectorAll(sel).forEach(el => {
    if (visible(el) && !resultNodes.includes(el)) resultNodes.push(el);
  }));
  const looksLikeCreateResult = text =>
    /rezerwacj|nie powiod|nie uda|b(?:łą|l)d|error|odrzu|PKK|WORD|płatno|platno/i.test(text);
  const resultMessages = resultNodes
    .map(el => norm(el.innerText || el.textContent))
    .filter(text => text && looksLikeCreateResult(text));
  if (resultMessages.length)
    return {status:'portal_message', message:resultMessages.join(' | ')};

  // Some portal banners have no stable ARIA role.  Search small visible
  // elements for unmistakable create-result wording, but never return the
  // entire document body (which could contain unrelated/profile data).
  const candidates = Array.from(document.querySelectorAll('div, section, aside, p, span'))
    .filter(visible)
    .map(el => norm(el.innerText || el.textContent))
    .filter(text => text && text.length <= 1200 && looksLikeCreateResult(text))
    .filter(text => !/USTAWIENIA PLIKÓW COOKIE/i.test(text));
  candidates.sort((a, b) => a.length - b.length);
  if (candidates.length)
    return {status:'portal_message', message:candidates[0]};

  // Dialogs are a fallback only.  Explicitly ignore the persistent cookie
  // settings/consent UI so it cannot mask the actual reservation response.
  const dialogSelectors = [
    '[role="alertdialog"]', '[role="dialog"]', '[aria-modal="true"]',
    'mat-dialog-container', '.mat-mdc-dialog-container'
  ];
  const dialogMessages = [];
  dialogSelectors.forEach(sel => document.querySelectorAll(sel).forEach(el => {
    if (!visible(el)) return;
    const text = norm(el.innerText || el.textContent);
    if (text && !/USTAWIENIA PLIKÓW COOKIE/i.test(text) && looksLikeCreateResult(text))
      dialogMessages.push(text);
  }));
  if (dialogMessages.length)
    return {status:'portal_message', message:dialogMessages.join(' | ')};
  return {status:'pending', message:''};
})()
"""


def _safe_portal_message(text):
    # A portal error normally contains no secrets, but fail closed if it ever
    # echoes a PKK/profile-like long number.
    return re.sub(r"\b\d{10,}\b", "<redacted-number>", str(text or ""))[:1200]


def observe_post_create(host, port, timeout=10, target=None):
    """Observe, but never interact with, the portal after create was clicked."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = cdp_client.evaluate_in_page(host, port, post_create_status_js(), target=target)
            if isinstance(result, dict):
                last = result
                status = result.get("status")
                if status in ("step5_reached", "portal_message"):
                    return result
        except Exception:
            pass
        time.sleep(0.25)
    return last or {"status": "unknown", "message": ""}

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
    last_result = None
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
        # A summary-step button can briefly be absent while Angular settles.
        # Keep transient abstentions quiet; if the whole timeout expires, emit
        # one final diagnostic instead of a misleading stream of not-found
        # messages immediately before a successful click.
        last_result = result
        sleep(0.5)
    _print_abstention(last_result, reasons_seen)
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


def wait_and_expand_date(host, port, date_str, timeout=20, target=None):
    """Expand one exact live Angular Material date header and verify aria-expanded."""
    deadline = time.monotonic() + timeout
    reasons_seen = set()
    clicked = False
    while time.monotonic() < deadline:
        try:
            if cdp_client.evaluate_in_page(host, port, date_expansion_open_js(date_str), target=target) is True:
                print(f"RESERVATION_DIAG step2 date_expanded={date_str}")
                return True
            if not clicked:
                result = cdp_client.evaluate_in_page(host, port, click_date_expansion_header_js(date_str), target=target)
                if isinstance(result, dict) and result.get("clicked"):
                    safe = sanitize_click_diagnostics(result)
                    print(
                        "RESERVATION_DIAG step2 date_header_click "
                        f"date={date_str!r} matched={safe.get('matched_text')!r} "
                        f"tag={safe.get('tag')!r} class={safe.get('element_class')!r} "
                        f"ariaExpandedBefore={result.get('ariaExpandedBefore')!r} "
                        f"ariaControls={result.get('ariaControls')!r}"
                    )
                    clicked = True
                else:
                    _print_abstention(result, reasons_seen)
        except Exception as exc:
            if 'exception' not in reasons_seen:
                print(f"RESERVATION_DIAG step2 date_header_exception={type(exc).__name__}")
                reasons_seen.add('exception')
        time.sleep(0.35)
    print(f"RESERVATION_DIAG step2 date_expand_timeout={date_str}")
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


def click_form_choice_js(text):
    """Click a labelled radio/form choice on the reservation wizard.

    The live portal renders step-1 PKK and WORD-mode choices as labelled
    radio cards, not buttons.  The generic click_text_js() is intentionally
    button-only for high-risk actions, so use this broader helper only for
    harmless step-1 form choices.
    """
    return CLICKABLE_HELPERS_JS + r"""
(function(text) {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('pl-PL');
  const wanted = norm(text);
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const candidates = Array.from(document.querySelectorAll(
    'label, [role="radio"], input[type="radio"], button, [role="button"], div'
  )).filter(visible);

  // Prefer the smallest visible element/container whose text contains the
  // requested label.  This avoids clicking a large section containing both
  // radio choices.
  let matches = candidates.filter(el => norm(el.innerText || el.textContent).includes(wanted));
  matches.sort((a, b) => norm(a.innerText || a.textContent).length - norm(b.innerText || b.textContent).length);
  for (const el of matches) {
    let node = el;
    for (let i = 0; node && i < 4; i++, node = node.parentElement) {
      if (!visible(node)) continue;
      if (node.matches('label, [role="radio"], button, [role="button"]')) { node.click(); return {clicked:true}; }
      const radio = node.matches('input[type="radio"]') ? node : node.querySelector('input[type="radio"]');
      if (radio && visible(radio)) { radio.click(); radio.dispatchEvent(new Event('change', {bubbles:true})); return {clicked:true}; }
    }
    el.click();
    return {clicked:true};
  }
  return {clicked:false};
})(%s)
""" % json.dumps(text, ensure_ascii=False)


def wait_and_click_form_choice(host, port, text, timeout=20, target=None):
    return _poll_until_truthy(host, port, click_form_choice_js(text), timeout, target)


def select_radio_by_value_or_text_js(value, text):
    """Prefer a radio carrying the stable backend ID; fall back to visible text."""
    return r"""
(function(value, text) {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('pl-PL');
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
  const exact = radios.find(r => String(r.value) === String(value));
  function clickRadio(r) {
    if (!r) return false;
    const id = r.id;
    const label = id ? document.querySelector('label[for="' + CSS.escape(id) + '"]') : null;
    (label || r).click();
    return true;
  }
  if (clickRadio(exact)) return {clicked:true, reason:'radio_value'};
  const wanted = norm(text);
  for (const r of radios) {
    let node = r;
    for (let i=0; node && i<6; i++, node=node.parentElement) {
      if (wanted && norm(node.innerText || node.textContent).includes(wanted)) {
        clickRadio(r); return {clicked:true, reason:'radio_text'};
      }
    }
  }
  return {clicked:false, reason:'radio_not_found'};
})(%s, %s)
""" % (json.dumps(value), json.dumps(text, ensure_ascii=False))


def wait_and_select_radio(host, port, value, text, timeout=15, target=None):
    return _poll_until_truthy(host, port, select_radio_by_value_or_text_js(value, text), timeout, target)



def click_radio_selector_js(name, value):
    """Click one exact native radio by its stable portal name/value pair.

    Angular Material keeps the backend value on the native input.  Dispatch
    input/change after click so Angular sees the same events as a user click.
    """
    return r"""
(function(name, value) {
  const esc = (window.CSS && CSS.escape) ? CSS.escape : (s => String(s).replace(/["\\]/g, '\\$&'));
  const selector = 'input[type="radio"][name="' + esc(String(name)) + '"][value="' + esc(String(value)) + '"]';
  const radio = document.querySelector(selector);
  if (!radio) return {clicked:false, reason:'radio_not_found', selector:selector};
  if (radio.checked) return {clicked:true, reason:'already_checked', selector:selector};
  radio.click();
  radio.dispatchEvent(new Event('input', {bubbles:true}));
  radio.dispatchEvent(new Event('change', {bubbles:true}));
  return {clicked:true, reason:'native_radio', selector:selector, checked:radio.checked};
})(%s, %s)
""" % (json.dumps(str(name)), json.dumps(str(value)))


def wait_and_click_radio_selector(host, port, name, value, timeout=15, target=None):
    return _poll_until_truthy(
        host, port, click_radio_selector_js(name, value), timeout, target
    )


def exam_type_value(exam_type):
    return EXAM_TYPE_VALUES.get(str(exam_type or "").strip(), str(exam_type or "").strip())


def wait_and_select_exam_type(host, port, exam_type, timeout=15, target=None):
    """Select the live step-2 exam type by its stable Angular radio value.

    The group name is generated, so only the backend value is treated as
    stable: Theoretical, Practice, or TheoreticalWithPractice.
    """
    value = exam_type_value(exam_type)
    js = r"""
(function(value) {
  const esc = (window.CSS && CSS.escape) ? CSS.escape : (s => String(s).replace(/["\\]/g, '\\$&'));
  const radio = document.querySelector('input[type="radio"][value="' + esc(String(value)) + '"]');
  if (!radio) return {clicked:false, reason:'exam_type_not_found', value:value};
  if (radio.checked) return {clicked:true, reason:'already_checked', value:value};
  radio.click();
  radio.dispatchEvent(new Event('input', {bubbles:true}));
  radio.dispatchEvent(new Event('change', {bubbles:true}));
  return {clicked:true, reason:'exam_type_radio', value:value, checked:radio.checked};
})(%s)
""" % json.dumps(value)
    return _poll_until_truthy(host, port, js, timeout, target)



def select_word_search_js(word_name):
    """Use the live WORD search combobox to expose a centre not present in the
    initial five nearby cards.

    The Angular control has stable id ``word-input``.  Choose the dropdown
    option matching the target WORD name; afterwards the normal WORD radio
    carrying organizationId can be selected exactly.
    """
    return r"""
(function(wordName) {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('pl-PL');
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const wanted = norm(wordName);

  // If the dropdown is open, prefer an exact name; otherwise allow the portal
  // option to contain the configured name (for minor presentation suffixes).
  const options = Array.from(document.querySelectorAll(
    '[role="option"], mat-option, li, [class*="option"], [class*="Option"]'
  )).filter(visible);
  let option = options.find(el => norm(el.innerText || el.textContent) === wanted);
  if (!option) {
    option = options.find(el => {
      const txt = norm(el.innerText || el.textContent);
      return wanted && (txt.includes(wanted) || wanted.includes(txt));
    });
  }
  if (option) {
    option.click();
    return {clicked:true, reason:'word_option', text:option.innerText || option.textContent};
  }

  const input = document.querySelector('#word-input');
  if (!input || !visible(input)) return {clicked:false, reason:'word_input_not_found'};

  // Open the Angular autocomplete.  Typing the full WORD name narrows the
  // list and avoids relying on the initial five centres.
  input.focus();
  input.click();
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, String(wordName || ''));
  input.dispatchEvent(new Event('input', {bubbles:true}));
  input.dispatchEvent(new Event('change', {bubbles:true}));
  return {clicked:false, reason:'word_search_typed'};
})(%s)
""" % json.dumps(str(word_name), ensure_ascii=False)


def wait_and_select_word_search(host, port, word_name, timeout=15, target=None):
    return _poll_until_truthy(
        host, port, select_word_search_js(word_name), timeout, target
    )



def verify_selected_word_js(word_name):
    """Confirm that Angular accepted the WORD autocomplete selection.

    Do not use focus as a signal.  The live portal may leave the caret in
    #word-input after a successful selection.  Acceptance is instead confirmed
    by the input value and, when present, the selected-WORD summary card.
    """
    return r"""
(function(wordName) {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('pl-PL');
  const wanted = norm(wordName);
  const input = document.querySelector('#word-input');
  const inputMatches = !!input && norm(input.value) === wanted;

  // After a successful autocomplete choice the portal renders a selected WORD
  // card above the map.  Match text outside the autocomplete panel as a second
  // independent confirmation of Angular state.
  const candidates = Array.from(document.querySelectorAll('div, section, article, mat-card'));
  const cardMatches = candidates.some(el => {
    const txt = norm(el.innerText || el.textContent);
    if (!txt || !txt.includes(wanted)) return false;
    if (input && el.contains(input)) return false;
    return txt.length < 500;
  });
  return inputMatches && cardMatches;
})(%s)
""" % json.dumps(str(word_name), ensure_ascii=False)
def wait_for_selected_word(host, port, word_name, timeout=8, target=None):
    return _poll_until_truthy(
        host, port, verify_selected_word_js(word_name), timeout, target
    )



def step2_loaded_js():
    """Return true when the wizard has actually advanced to Krok 2.

    Angular Material keeps all wizard panels mounted and its native radio
    inputs are visually hidden inside the rendered radio controls. Therefore
    checking whether the native exam-type input itself is onscreen produces
    false negatives. Detect the active second step plus the onscreen start
    date instead; keep an onscreen Krok-2 heading as an independent fallback.
    """
    return r"""
(function() {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('pl-PL');
  const onscreen = el => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 &&
           r.left < window.innerWidth && r.top < window.innerHeight;
  };
  const start = document.querySelector('#startDate');
  if (!onscreen(start)) return false;
  const step2Header = document.querySelector('#cdk-stepper-0-label-1');
  const headerActive = !!step2Header && (
    document.activeElement === step2Header ||
    step2Header.getAttribute('aria-selected') === 'true' ||
    step2Header.getAttribute('aria-current') === 'step' ||
    step2Header.classList.contains('mat-step-header-selected')
  );
  if (headerActive) return true;
  const heading = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6,div,span,p'))
    .find(el => onscreen(el) && norm(el.innerText || el.textContent).includes('wybór terminu egzaminu dla'));
  return !!heading;
})()
"""


def step1_diagnostics_js():
    """Return non-sensitive diagnostics for the live Krok 1 form."""
    return r"""
(function() {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { if (!el) return false; const r=el.getBoundingClientRect(); const cs=getComputedStyle(el); return cs.display!=='none' && cs.visibility!=='hidden' && Number(cs.opacity)!==0 && r.width>0 && r.height>0 && r.right>0 && r.bottom>0 && r.left<window.innerWidth && r.top<window.innerHeight; };
  const radios = (name) => Array.from(document.querySelectorAll(`input[type="radio"][name="${name}"]`))
    .map(x => ({value:x.value, checked:!!x.checked, visible:visible(x), disabled:!!x.disabled}));
  const p = document.querySelector('#profileNumber-input');
  const cat = Array.from(document.querySelectorAll('input')).find(x =>
    String(x.getAttribute('aria-label') || '').toLocaleLowerCase('pl-PL').includes('kategoria'));
  const buttons = Array.from(document.querySelectorAll('button[type="submit"]')).map((b, i) => ({
    i, text:norm(b.innerText || b.textContent), visible:visible(b), disabled:!!b.disabled,
    ariaDisabled:b.getAttribute('aria-disabled'), formNoValidate:!!b.formNoValidate,
    formPresent:!!b.form, formValid:b.form ? b.form.checkValidity() : null,
    rect:(() => { const r=b.getBoundingClientRect(); return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]; })()
  }));
  return {
    activeTag:document.activeElement ? document.activeElement.tagName : null,
    activeId:document.activeElement ? document.activeElement.id : null,
    documentType:radios('documentType'), requestType:radios('requestType'),
    profilePresent:!!p, profileHasValue:!!(p && norm(p.value)), profileFocused:document.activeElement===p,
    categoryHasValue:!!(cat && norm(cat.value)),
    submitButtons:buttons,
    startDatePresent:!!document.querySelector('#startDate'), startDateOnscreen:visible(document.querySelector('#startDate')), viewport:[window.innerWidth,window.innerHeight]
  };
})()
"""


def _diag_step1(host, port, label, target=None):
    try:
        data = cdp_client.evaluate_in_page(host, port, step1_diagnostics_js(), target=target)
        print(f"RESERVATION_DIAG step1 {label}: {json.dumps(data, ensure_ascii=False, sort_keys=True)}")
    except Exception as exc:
        print(f"RESERVATION_DIAG step1 {label}: diagnostic_error={type(exc).__name__}")


def step1_submit_js():
    """Submit Krok 1 through the form that owns the exact live submit button.

    Use the exact live submit button and move focus to it before clicking.
    This mirrors the important part of the manual interaction: leaving the
    Angular autocomplete before its submit handler runs.
    """
    return r"""
(function() {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  // The active Krok-1 panel can be taller than the viewport.  Its submit
  // button is therefore legitimately below the fold (y > innerHeight).
  // Inactive Angular step panels, however, are translated horizontally far
  // outside the viewport.  Distinguish the active panel by horizontal
  // intersection and rendered size, then scroll its button into view.
  const inActiveColumn = el => {
    if (!el) return false;
    const r=el.getBoundingClientRect();
    const cs=getComputedStyle(el);
    return cs.display!=='none' && cs.visibility!=='hidden' && Number(cs.opacity)!==0 &&
           r.width>0 && r.height>0 && r.right>0 && r.left<window.innerWidth;
  };
  const buttons = Array.from(document.querySelectorAll('button[type="submit"]'));
  const matches = buttons.filter(button => {
    if (!inActiveColumn(button)) return false;
    const label = button.querySelector('.mdc-button__label .desktop, span.desktop');
    return label && norm(label.textContent) === 'Zapisz i przejdź dalej';
  });
  if (matches.length !== 1) {
    return {submitted:false, reason:matches.length ? 'ambiguous_submit' : 'submit_not_found',
            matchCount:matches.length};
  }

  const button = matches[0];
  if (button.disabled || button.getAttribute('aria-disabled') === 'true') {
    return {submitted:false, reason:'submit_disabled'};
  }

  // Do not call form.requestSubmit() here.  The live Angular autocomplete can
  // still own focus after its option has been selected.  A real user advances
  // by clicking this button, which first moves focus away from the autocomplete
  // and then runs the button/Angular handlers.  requestSubmit() skips that
  // interaction and can leave Krok 1 on screen even though all values are set.
  button.scrollIntoView({block:'center', inline:'nearest'});
  button.focus({preventScroll:true});
  button.click();
  return {submitted:true, reason:'focused_button_click'};
})()
"""


def wait_and_click_step1_next(host, port, timeout=20, target=None):
    """Submit Krok 1 and require Krok 2 to appear, with diagnostics."""
    deadline = time.monotonic() + timeout
    attempts = 0
    _diag_step1(host, port, "before_submit_loop", target)
    while time.monotonic() < deadline:
        try:
            if cdp_client.evaluate_in_page(host, port, step2_loaded_js(), target=target) is True:
                print("RESERVATION_DIAG step1 step2_detected_before_click")
                return True

            result = cdp_client.evaluate_in_page(host, port, step1_submit_js(), target=target)
            print(f"RESERVATION_DIAG step1 submit_result: {json.dumps(result, ensure_ascii=False, sort_keys=True)}")
            if isinstance(result, dict) and result.get("submitted"):
                attempts += 1
                _diag_step1(host, port, f"after_click_{attempts}", target)
                verify_until = min(deadline, time.monotonic() + 4.0)
                while time.monotonic() < verify_until:
                    try:
                        if cdp_client.evaluate_in_page(host, port, step2_loaded_js(), target=target) is True:
                            print(f"RESERVATION_DIAG step1 step2_detected_after_click={attempts}")
                            return True
                    except Exception as exc:
                        print(f"RESERVATION_DIAG step1 verify_exception={type(exc).__name__}")
                    time.sleep(0.25)
                _diag_step1(host, port, f"verify_timeout_{attempts}", target)
                if attempts >= 3:
                    print("Krok 1 submit was issued but Krok 2 did not render.")
                    return False
            else:
                _diag_step1(host, port, "submit_abstained", target)
                time.sleep(0.5)
        except Exception as exc:
            print(f"RESERVATION_DIAG step1 submit_exception={type(exc).__name__}: {exc}")
            time.sleep(0.5)
    _diag_step1(host, port, "overall_timeout", target)
    return False

def category_code(category_id):
    """Return the portal's visible category code (for example 5 -> ``B``)."""
    try:
        wanted = int(category_id)
        rows = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
        for row in rows:
            if int(row.get("id")) == wanted:
                return str(row.get("code") or "").strip()
    except Exception as exc:
        print(f"Couldn't map category id {category_id!r} ({exc!r}).")
    return ""


def select_category_js(code):
    """Select a category in the live step-1 control.

    The portal currently renders this as a dropdown/combobox after PKK is
    selected.  Support both a native <select> and the custom combobox build.
    Exact visible text is required so category B cannot accidentally match B1
    or B+E.
    """
    return r"""
(function(code) {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('pl-PL');
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const wanted = norm(code);

  for (const sel of Array.from(document.querySelectorAll('select')).filter(visible)) {
    const opt = Array.from(sel.options || []).find(o => {
      const t = norm(o.textContent);
      return t === wanted || t.startsWith(wanted + ' ') || t.startsWith(wanted + '—') || t.startsWith(wanted + ' -');
    });
    if (opt) {
      sel.value = opt.value;
      sel.dispatchEvent(new Event('input', {bubbles:true}));
      sel.dispatchEvent(new Event('change', {bubbles:true}));
      return {clicked:true, reason:'native_select'};
    }
  }

  const options = Array.from(document.querySelectorAll('[role="option"], li, [data-value]')).filter(visible);
  const exact = options.find(el => norm(el.innerText || el.textContent) === wanted);
  if (exact) { exact.click(); return {clicked:true, reason:'custom_option'}; }

  const controls = Array.from(document.querySelectorAll('[role="combobox"], button, [aria-haspopup="listbox"], input')).filter(visible);
  for (const el of controls) {
    let node = el;
    for (let i=0; node && i<5; i++, node=node.parentElement) {
      const t = norm(node.innerText || node.textContent);
      if (t.includes('kategoria')) {
        el.click();
        return {clicked:false, reason:'category_dropdown_opened'};
      }
    }
  }
  return {clicked:false, reason:'category_control_not_found'};
})(%s)
""" % json.dumps(code, ensure_ascii=False)


def wait_and_select_category(host, port, code, timeout=15, target=None):
    return _poll_until_truthy(host, port, select_category_js(code), timeout, target)


def select_profile_number_js(profile_number):
    """Select the logged-in PKK profile from the live custom dropdown.

    After choosing the PKK profile type the portal exposes a ``Numer profilu``
    combobox.  Its option text is currently ``<profile_number> — <category>``;
    selecting it also fills the disabled category field, so category must not
    be clicked separately.
    """
    return r"""
(function(profileNumber) {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const wanted = String(profileNumber || '').trim();

  // Angular can destroy/recreate the autocomplete overlay while an option
  // click is being processed.  In that case the CDP evaluation that performed
  // the click can lose its execution context even though the selection itself
  // succeeded.  Therefore treat the stable live control value as authoritative
  // completion state on the next poll instead of reopening the combobox forever.
  const selectedInput = document.querySelector('#profileNumber-input');
  if (selectedInput && visible(selectedInput)) {
    const current = norm(selectedInput.value);
    if (current === wanted || current.startsWith(wanted + ' ') || current.startsWith(wanted + '—')) {
      return {clicked:true, reason:'profile_already_selected', value:current};
    }
  }

  // If the list is already open, choose the option whose visible text starts
  // with the exact PKK number.  The suffix is the category (for example — B).
  const options = Array.from(document.querySelectorAll(
    '[role="option"], li, [data-value], [class*="option"], [class*="Option"]'
  )).filter(visible);
  for (const opt of options) {
    const t = norm(opt.innerText || opt.textContent);
    if (t === wanted || t.startsWith(wanted + ' ') || t.startsWith(wanted + '—')) {
      opt.click();
      return {clicked:true, reason:'profile_option'};
    }
  }

  // The live portal exposes a stable input id for this Angular combobox.
  const profileInput = document.querySelector('#profileNumber-input');
  if (profileInput && visible(profileInput)) {
    profileInput.click();
    return {clicked:false, reason:'profile_dropdown_opened_by_id'};
  }

  // Fallback for older builds: open specifically the control belonging to
  // "Numer profilu".  Do not use the category combobox: it is populated
  // automatically after the profile option is chosen.
  const controls = Array.from(document.querySelectorAll(
    '[role="combobox"], [aria-haspopup="listbox"], input, button'
  )).filter(visible);
  for (const el of controls) {
    let node = el;
    for (let i = 0; node && i < 6; i++, node = node.parentElement) {
      const t = norm(node.innerText || node.textContent).toLocaleLowerCase('pl-PL');
      if (t.includes('numer profilu') && !t.includes('wybór numeru profilu i kategorii')) {
        el.click();
        return {clicked:false, reason:'profile_dropdown_opened'};
      }
    }
  }

  // Material UI often keeps the label as a sibling rather than an ancestor.
  // Find the smallest visible container containing "Numer profilu" and click
  // its combobox/input.
  const containers = Array.from(document.querySelectorAll('div, fieldset')).filter(visible)
    .filter(el => norm(el.innerText || el.textContent).toLocaleLowerCase('pl-PL').includes('numer profilu'))
    .sort((a,b) => norm(a.innerText || a.textContent).length - norm(b.innerText || b.textContent).length);
  for (const box of containers) {
    const control = box.querySelector('[role="combobox"], [aria-haspopup="listbox"], input');
    if (control && visible(control)) {
      control.click();
      return {clicked:false, reason:'profile_dropdown_opened'};
    }
  }
  return {clicked:false, reason:'profile_control_not_found'};
})(%s)
""" % json.dumps(str(profile_number), ensure_ascii=False)


def wait_and_select_profile_number(host, port, profile_number, timeout=15, target=None):
    return _poll_until_truthy(
        host, port, select_profile_number_js(profile_number), timeout, target
    )


def page_contains_js(*parts):
    return """
(function(parts) {
  const norm = s => String(s || '').replace(/\\s+/g, ' ').trim().toLocaleLowerCase('pl-PL');
  const text = norm(document.body && (document.body.innerText || document.body.textContent));
  return parts.every(p => text.includes(norm(p)));
})(%s)
""" % json.dumps(list(parts), ensure_ascii=False)


def wait_for_page_text(host, port, *parts, timeout=20, target=None):
    return _poll_until_truthy(host, port, page_contains_js(*parts), timeout, target)


def summary_matches_target(host, port, target, page_target=None, timeout=20):
    dt = datetime.fromisoformat(target["datetime"])
    exam_label = EXAM_TYPE_LABELS_PL.get(target["exam_type"], target["exam_type"])
    # Step 4 is a normal page, not the old change-date modal.  Require the
    # page heading plus every identifying field before an irreversible click.
    return wait_for_page_text(
        host, port,
        "Podsumowanie wstępnej rezerwacji",
        dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M"), exam_label,
        target.get("word", ""),
        timeout=timeout, target=page_target,
    )


def _read_target(target_slot_json):
    target = json.loads(target_slot_json)
    for key in ("word", "exam_type", "datetime", "organization_id"):
        if target.get(key) in (None, ""):
            raise ValueError(f"target slot is missing {key}")
    datetime.fromisoformat(target["datetime"])
    return target


def open_reservation_results(host, port, target, page_target=None):
    """Drive the live /reservation wizard to step 2 for one concrete WORD.

    Search mode is intentionally irrelevant here: MultipleCentersExams is a
    discovery mechanism.  Once notifier has a winning hit, reservation always
    uses that hit's concrete WORD and lets the portal issue OneCenterExam.
    """
    config = read_config()
    category = category_code(config.get("category"))
    if not category:
        print("Missing/unknown category in config — leaving /reservation open.")
        return False

    if not wait_for_page_text(host, port, "Rezerwacja egzaminu", timeout=25, target=page_target):
        print("Reservation wizard did not load.")
        return False

    # Krok 1 — use the live portal's dependency order:
    # 1) SingleWord request type, 2) concrete WORD autocomplete,
    # 3) PKK document type, 4) concrete profile number/category.
    print("RESERVATION_DIAG step1 stage=requestType_start")
    if not wait_and_click_radio_selector(
        host, port, "requestType", "SingleWord", timeout=12, target=page_target
    ):
        print("Couldn't switch to the single-WORD reservation mode (requestType=SingleWord).")
        return False
    print("RESERVATION_DIAG step1 stage=requestType_ok")

    print("RESERVATION_DIAG step1 stage=word_start")
    if not wait_and_select_word_search(
        host, port, target["word"], timeout=18, target=page_target
    ):
        print(f"Couldn't find/select target WORD {target['word']!r} in the WORD search.")
        return False
    print("RESERVATION_DIAG step1 stage=word_ok")
    # A successful autocomplete option click is the completion signal for the
    # WORD field.  Do not wait for #word-input to lose focus or for a second
    # DOM confirmation: the live Angular form legitimately leaves the chosen
    # WORD in the combobox and may keep focus there.  Proceed immediately to
    # the next independent control (PKK).
    print("RESERVATION_DIAG step1 stage=pkk_start")
    if not wait_and_click_radio_selector(
        host, port, "documentType", "Pkk", timeout=12, target=page_target
    ):
        print("Couldn't select the PKK profile type (documentType=Pkk).")
        return False
    print("RESERVATION_DIAG step1 stage=pkk_ok")

    profile_number = str(config.get("profile_number") or "").strip()
    if not profile_number:
        print("Missing profile_number in config — leaving /reservation open.")
        return False
    print("RESERVATION_DIAG step1 stage=profile_start")
    if not wait_and_select_profile_number(host, port, profile_number, timeout=18, target=page_target):
        print("Couldn't select the configured PKK profile number.")
        return False
    print("RESERVATION_DIAG step1 stage=profile_ok")
    _diag_step1(host, port, "after_profile_ok", page_target)

    # Selecting the PKK autocomplete option is the completion signal for the
    # profile field.  The live Angular control legitimately keeps focus in
    # #profileNumber-input after the value and derived category are already
    # populated.  Do not wait for focus loss, an extra category text check, or
    # another DOM transition; proceed directly to the Krok 1 submit.
    print("RESERVATION_DIAG step1 stage=submit_start")
    if not wait_and_click_step1_next(host, port, timeout=20, target=page_target):
        print("Couldn't continue from Krok 1 — Profil i WORD.")
        return False
    print("RESERVATION_DIAG step1 stage=step2_ok")

    exam_value = exam_type_value(target["exam_type"])
    exam_label = EXAM_TYPE_LABELS_PL.get(exam_value, target["exam_type"])
    if not wait_and_select_exam_type(host, port, exam_value, timeout=20, target=page_target):
        print(f"Couldn't select {exam_label!r} ({exam_value}) on the term step.")
        return False
    # Do not overwrite the portal's start date here.  This helper is no longer
    # reproducing the notifier's search; it is opening the concrete WORD that
    # already produced the winning hit.  OneCenterExam shows that centre's
    # available dates and auto-select can expand the target date directly.
    print(f"Reservation results opened for {target['word']} ({exam_label}).")
    return True


def try_select_target_slot(host, port, target_slot_json, confirm=False, page_target=None,
                           baseline_booking=None, baseline_booking_candidates=None):
    """Select the exact live reservation slot and stop at preliminary summary.

    With confirm=True, the only irreversible action is the final click on
    step 4.  The portal itself then issues POST /Reservations/create; this
    helper never constructs or submits that API request directly.
    """
    def abandon():
        if confirm:
            release_confirm_cooldown()

    try:
        target = _read_target(target_slot_json)
        dt = datetime.fromisoformat(target["datetime"])
    except Exception as exc:
        print(f"Couldn't parse --target-slot ({exc!r}) — pick the slot yourself.")
        abandon(); return

    exam_label = EXAM_TYPE_LABELS_PL.get(target["exam_type"], target["exam_type"])
    date_str, time_str = dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")
    center = target["word"]
    print(f"Looking for {exam_label} at {time_str} on {date_str} ({center})...")

    if not wait_and_expand_date(host, port, date_str, timeout=20, target=page_target):
        print(f"Couldn't expand {date_str}; pick the slot manually."); abandon(); return
    if not wait_and_select_slot(host, port, exam_label, time_str, date_str, center, target=page_target):
        print("Couldn't select the first matching slot; pick it manually."); abandon(); return

    # Live step 2 renders concrete times with Angular Material checkbox inputs
    # whose generated IDs are unstable. select_slot_js deliberately identifies
    # the exact row structurally by date + exam type + time and clicks that row;
    # do not depend on mat-mdc-checkbox-NNN IDs.
    # Selecting a slot opens "Potwierdź wybrany egzamin".
    if not wait_and_click_enabled(host, port, "Potwierdź i przejdź dalej", timeout=15, target=page_target, strict=True):
        print("Couldn't confirm the selected slot modal."); abandon(); return
    # Back on step 2, the chosen slot is checked; this advances to language.
    if not wait_and_click_enabled(host, port, "Zapisz i przejdź dalej", timeout=15, target=page_target, strict=True):
        print("Couldn't advance from the term step."); abandon(); return
    if not wait_for_page_text(host, port, "Wybór języka egzaminu", timeout=15, target=page_target):
        print("Language step did not appear."); abandon(); return
    # Use the live stable language radio instead of matching visible text.
    if not wait_and_click_radio_selector(
        host, port, "language", "Polish", timeout=8, target=page_target
    ):
        print("Couldn't select Polish on the language step."); abandon(); return
    if not wait_and_click_enabled(host, port, "Zapisz i przejdź dalej", timeout=15, target=page_target, strict=True):
        print("Couldn't advance from the language step."); abandon(); return
    if not summary_matches_target(host, port, target, page_target=page_target, timeout=20):
        print("Preliminary reservation summary did not match the intended slot — stopping.")
        abandon(); return

    print("Reached 'Podsumowanie wstępnej rezerwacji' and verified the intended slot.")
    if not confirm:
        print("Auto-select stops here. Nothing has been submitted.")
        return

    if not target_beats_current_slot(dt):
        print("Target no longer beats current_slot_date — NOT creating a reservation.")
        abandon(); return
    if not record_confirm_cooldown():
        print("Couldn't arm repeat-confirm safety gate — NOT creating a reservation.")
        return

    # This is the sole non-idempotent action.  On the live site this causes
    # POST /bknd/exam/api/v1/Reservations/create and then proceeds to the
    # confirmation/payment flow.  Use the single-fire marker, never a poller.
    reset_confirm_marker(host, port, page_target)
    api_observer = None
    try:
        api_observer = ReservationCreateObserver(host, port, page_target).start()
        print("RESERVATION_DIAG create_observer=armed")
    except Exception as exc:
        print(f"RESERVATION_DIAG create_observer=unavailable error={repr(exc)[:240]}")

    result = click_confirm_once(host, port, "Zapisz i przejdź dalej", target=page_target)
    if result is None:
        if api_observer:
            api_observer.stop()
        print("Couldn't click the final reservation-create button automatically.")
        return
    record_confirm_cooldown("CREATE_CLICKED")
    print("Reservation create was clicked once. Observing the API response and portal without further interaction...")

    api_result = api_observer.wait(timeout=10) if api_observer else None
    if api_observer:
        api_observer.stop()

    # The create API is authoritative.  Never log its request payload: it
    # contains the PKK/profile number.  Log only status and bounded response
    # fields.  DOM observation remains a fallback/secondary confirmation.
    if isinstance(api_result, dict) and api_result.get("status") is not None:
        print("RESERVATION_DIAG create_request=sent")
        print(f"RESERVATION_DIAG create_http_status={api_result.get('status')}")
        if api_result.get("ok"):
            print("RESERVATION_DIAG create_result=api_success")
        else:
            print("RESERVATION_DIAG create_result=api_error")
            if api_result.get("error_id"):
                print(f"RESERVATION_DIAG create_error_id={api_result.get('error_id')!r}")
            if api_result.get("error_type") is not None:
                print(f"RESERVATION_DIAG create_error_type={api_result.get('error_type')!r}")
            if api_result.get("message"):
                print(f"RESERVATION_DIAG create_message={api_result.get('message')!r}")
    elif isinstance(api_result, dict) and api_result.get("network_error"):
        print("RESERVATION_DIAG create_result=network_error")
        print(f"RESERVATION_DIAG create_network_error={api_result.get('network_error')!r}")
    elif isinstance(api_result, dict) and api_result.get("observer_error"):
        print("RESERVATION_DIAG create_observer=error")
        print(f"RESERVATION_DIAG create_observer_error={api_result.get('observer_error')!r}")
    else:
        print("RESERVATION_DIAG create_api_response=not_observed")

    outcome = observe_post_create(host, port, timeout=10, target=page_target)
    status = outcome.get("status") if isinstance(outcome, dict) else "unknown"
    message = _safe_portal_message(outcome.get("message")) if isinstance(outcome, dict) else ""
    api_error = isinstance(api_result, dict) and api_result.get("status") is not None and not api_result.get("ok")
    api_success = isinstance(api_result, dict) and api_result.get("ok") is True
    if api_error:
        api_message = _safe_portal_message(api_result.get("message"))
        print("Portal API rejected the reservation-create request; no further action was taken.")
        push_title = "info-kierowca: reservation create rejected"
        push_message = f"Portal rejected the create request: {api_message or ('HTTP ' + str(api_result.get('status')))}"
    elif status == "step5_reached":
        print("RESERVATION_DIAG create_result=step5_reached")
        print("Portal advanced to step 5 (Potwierdzenie). The browser is left open for confirmation/payment.")
        push_title = "info-kierowca: reservation created — payment/review required"
        push_message = (
            f"Portal advanced to confirmation after submitting {exam_label} at {date_str}, {time_str}. "
            "Check the open browser and complete any required confirmation/payment."
        )
    elif api_success:
        print("RESERVATION_DIAG create_result=api_success_ui_unverified")
        print("Portal API accepted the reservation-create request, but step 5 was not observed within 10 seconds.")
        push_title = "info-kierowca: reservation accepted — verify browser"
        push_message = (
            f"Portal API accepted the create request for {exam_label} at {date_str}, {time_str}, "
            "but the confirmation screen was not observed. Check the open browser before taking further action."
        )
    elif status == "portal_message":
        print("RESERVATION_DIAG create_result=portal_message")
        print(f"RESERVATION_DIAG portal_message={message!r}")
        print("Portal displayed a message after the create attempt; no further action was taken.")
        push_title = "info-kierowca: reservation create was not confirmed"
        push_message = f"Portal message after create attempt: {message}"
    else:
        print("RESERVATION_DIAG create_result=unknown")
        print("The final create click fired, but no step-5 transition or portal message was observed within 10 seconds.")
        push_title = "info-kierowca: reservation create attempted — verify browser"
        push_message = (
            f"Clicked create for {exam_label} at {date_str}, {time_str}, but the portal result could not be verified. "
            "Check the open browser before taking any further action."
        )
    topic = read_config().get("ntfy_topic")
    push_ntfy(topic, push_title, push_message, priority="urgent")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help="Page to open Chrome to (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-auto-click", action="store_true",
                        help="Just open a logged-in /reservation tab; do not drive the wizard")
    parser.add_argument("--target-slot", default=None,
                        help="JSON hit dict including word/exam_type/datetime/organization_id")
    parser.add_argument("--auto-select-slot", action="store_true",
                        help="After opening step 2, select the exact hit and stop on preliminary summary")
    parser.add_argument("--confirm-reschedule", action="store_true",
                        help="After verified preliminary summary, click once to create the reservation")
    args = parser.parse_args()

    if not cdp_client.SESSION_FILE.exists():
        raise SystemExit(f"No session found at {cdp_client.SESSION_FILE} — log in first.")
    session = json.loads(cdp_client.SESSION_FILE.read_text())
    cookies = session.get("cookies", {})
    missing = cdp_client.COOKIE_NAMES - cookies.keys()
    if missing:
        raise SystemExit(f"session.json is missing {sorted(missing)} — log in again.")

    chrome = find_chrome()
    ensure_private_profile_dir(PROFILE_DIR)
    subprocess.Popen([
        chrome, *chrome_debugging_args(args.port, PROFILE_DIR), "--no-first-run",
        "--no-default-browser-check", "--start-maximized", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    cdp_client.wait_for_debug_port("127.0.0.1", args.port, timeout=20)
    cdp_client.set_cookies("127.0.0.1", args.port, {**cookies, "CookieScriptConsent": consent_cookie()})
    page_target = cdp_client.create_page_target("127.0.0.1", args.port)
    cdp_client.navigate_target("127.0.0.1", args.port, page_target, args.url)
    cdp_client.bring_target_to_front("127.0.0.1", args.port, page_target)
    print(f"Chrome opened at {args.url}, logged in using {cdp_client.SESSION_FILE}.")

    if args.no_auto_click:
        print("Manual browser open requested; reservation wizard left untouched.")
    elif not args.target_slot:
        print("No target slot supplied; reservation wizard left open for manual use.")
    else:
        try:
            target = _read_target(args.target_slot)
        except Exception as exc:
            print(f"Invalid target slot ({exc!r}); reservation wizard left open.")
        else:
            if open_reservation_results("127.0.0.1", args.port, target, page_target=page_target):
                if args.auto_select_slot:
                    try_select_target_slot(
                        "127.0.0.1", args.port, args.target_slot,
                        confirm=args.confirm_reschedule, page_target=page_target,
                    )
                else:
                    # Option 1 stops on Krok 2, but it should expose the exact
                    # winning date so the user can immediately see and choose
                    # the concrete time manually.  Do not select any slot here.
                    try:
                        target_dt = datetime.fromisoformat(target["datetime"])
                        target_date = target_dt.strftime("%d/%m/%Y")
                    except Exception as exc:
                        print(f"Couldn't parse target date ({exc!r}); leaving available exams open.")
                    else:
                        if wait_and_expand_date(
                            "127.0.0.1", args.port, target_date, timeout=20, target=page_target
                        ):
                            print(
                                f"Auto-open stops with {target_date} expanded; "
                                "choose the slot yourself."
                            )
                        else:
                            print(
                                f"Couldn't expand {target_date}; "
                                "available exams remain open for manual selection."
                            )
    print("Close the window whenever you're done — this script doesn't manage it further.")


if __name__ == "__main__":
    main()
