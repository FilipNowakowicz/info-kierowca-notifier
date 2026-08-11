#!/usr/bin/env python3
"""Auto-heals an expired info-kierowca.pl session.

Launches Chrome (in its own throwaway profile, separate from your regular
browsing) pointed at the login page, waits for you to scan the mObywatel QR
code in the app, then captures the resulting session cookies the moment
they appear and writes session.json — no manual "launch Chrome, log in, run
a script" dance required.

Run by hand:

    python -m info_kierowca_notifier.auth.session

or invoked automatically by notifier.py (see trigger_auto_refresh() in
auth.launch) whenever a check comes back auth_expired. A lock file at
~/.local/state/info-kierowca-notifier/auto-refresh.lock stops it firing
more than once concurrently — delete that file if a previous run crashed
without cleaning up.

Nothing but the two info-kierowca.pl session cookies is read or sent
anywhere; see browser.cdp's docstring for the debug-port security note.
"""
import argparse
import json
import signal
import subprocess
import sys
import time

from info_kierowca_notifier.browser import cdp as cdp_client
from info_kierowca_notifier.browser.chrome import (
    AttachedChrome,
    chrome_debugging_args,
    ensure_private_profile_dir,
    find_chrome,
)
from info_kierowca_notifier.browser.clicking import (
    CLICKABLE_HELPERS_JS,
    sanitize_click_diagnostics,
)
from info_kierowca_notifier.auth import providers as auth_providers
from info_kierowca_notifier.auth import credentials as credential_store
from info_kierowca_notifier.auth import sms as sms_provider
from info_kierowca_notifier import ntfy_transport
from info_kierowca_notifier.auth import relogin_control

from info_kierowca_notifier.paths import AUTO_REFRESH_LOCK as LOCK_FILE  # noqa: E402
from info_kierowca_notifier.paths import AUTO_REFRESH_RESTART_REQUEST as RESTART_REQUEST_FILE  # noqa: E402
from info_kierowca_notifier.paths import (  # noqa: E402,F401
    CONFIG_FILE, RELOGIN_BACKOFF_FILE, STATE_DIR, ensure_state_dir,
)
from info_kierowca_notifier.auth.relogin_backoff import RetryBackoff  # noqa: E402

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


def authentication_chrome_args(port, profile_dir, *, headless=False):
    """Build the dedicated authentication-browser command-line arguments."""
    # Smaller widths can make the login page replace its QR image with a
    # numeric backup code, so preserve a desktop-sized viewport in both modes.
    args = [
        *chrome_debugging_args(port, profile_dir),
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=900,850",
        "--app=about:blank",
        # AUTO_CLICK_TARGETS below is hardcoded Polish text ("Zaloguj się",
        # "Aplikacja mObywatel"). A brand-new throwaway profile (see
        # ensure_private_profile_dir — this one is freshly created every
        # run) has no saved language preference, so absent --lang Chrome
        # falls back to the OS display language for its UI and, more
        # importantly, its default Accept-Language header — which
        # info-kierowca.pl/gov.pl may honor to serve English (or another
        # language) copy instead of Polish. On a Polish-locale machine that
        # accidentally matches and everything works; on a non-Polish-locale
        # machine (plausible on a Windows install, less likely on a
        # Polish user's own Linux box) every text match in AUTO_CLICK_TARGETS
        # would silently fail from the very first page — no exception, no
        # log line, indistinguishable from the CDP connection itself never
        # having worked. Forcing pl-PL here makes the click-through
        # deterministic regardless of the host OS's own locale.
        "--lang=pl-PL",
    ]
    if headless:
        args.append("--headless=new")
    return args


# The login click-path: info-kierowca.pl -> (maybe) "Zaloguj się" -> a PWPW
# identity-provider chooser with a "gov.pl" tile -> a login.gov.pl chooser
# with an "Aplikacja mObywatel" tile -> QR code. Checked in this order (most
# specific/downstream first) so whichever screen is showing gets exactly one
# click; harmless no-op once the QR page itself is showing, since nothing
# there matches. Site markup could change and break this — if it stops
# matching, you can still click through by hand while the script waits.
AUTO_CLICK_TARGETS = ["Aplikacja mObywatel", "gov.pl", "Zaloguj się"]


CLICK_LOGIC_JS = """
var __IKW_STOP_KEY = '__ikw_auto_click_stopped';
function __ikw_stopped() {
  try { return !!sessionStorage.getItem(__IKW_STOP_KEY); } catch (e) { return false; }
}
""" + CLICKABLE_HELPERS_JS + """
function __ikw_pageDiag() {
  // Windows QR-login-stall diagnostics (see this file's own CLAUDE.md bullet
  // for the ranked hypotheses this is meant to distinguish between).
  // Gathered on every eval, clicked or not, and merged onto whatever
  // __ikw_diagnostics() itself returns (which lives in browser/clicking.py
  // and isn't extended here) so a stuck heartbeat log already carries
  // enough to tell "the DOM genuinely never matches" apart from "this
  // document's viewport/overlay state looks nothing like what was tested on
  // Linux", without a second round-trip to the reporter. observer_injected
  // reflects a marker AUTO_CLICK_OBSERVER_JS sets the instant it *executes*
  // for the current document (see below) — a much stronger signal than the
  // raw Page.addScriptToEvaluateOnNewDocument CDP ack (logged separately by
  // register_and_navigate()), which only confirms Chrome accepted the
  // registration call, not that it actually ran for this document.
  var diag = {};
  try {
    diag.viewport_width = window.innerWidth;
    diag.viewport_height = window.innerHeight;
    diag.device_pixel_ratio = window.devicePixelRatio || 1;
    diag.observer_injected = !!(window.__ikw_diag && window.__ikw_diag.observer_injected);
    var overlay = document.querySelector(
      '[class*="cookie" i], [id*="cookie" i], [class*="consent" i], [id*="consent" i]'
    );
    diag.consent_overlay_present = !!(overlay && __ikw_isVisible(overlay));
  } catch (e) {}
  return diag;
}
function __ikw_withPageDiag(result) {
  var diag = __ikw_pageDiag();
  for (var key in diag) { result[key] = diag[key]; }
  return result;
}
function __ikw_findAndClick(targets) {
  if (__ikw_stopped()) return __ikw_withPageDiag(__ikw_diagnostics('', '', null, 'stopped'));
  if (__ikw_pageIsKnownError()) return __ikw_withPageDiag(__ikw_diagnostics('', '', null, 'known_error_page'));
  var all = document.querySelectorAll('button, a, [role="button"], li, div, span');
  for (var ti = 0; ti < targets.length; ti++) {
    var text = targets[ti];
    var candidates = [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      // textContent (unlike innerText) includes text from display:none
      // elements, so a not-yet-revealed tile that's already in the DOM
      // (common in SPA choosers that toggle visibility via a class rather
      // than mounting/unmounting) must not be matched via that fallback.
      if (!__ikw_isVisible(el)) continue;
      var t = __ikw_text(el);
      if (t && t.length < 200 && t.toLowerCase().indexOf(text.toLowerCase()) !== -1) {
        // Multiple descendants can carry the same label. Resolve them first
        // so they count as one candidate when they belong to one control.
        var clickable = __ikw_clickableAncestor(el);
        if (!clickable || __ikw_isExcludedControl(el, clickable)) continue;
        var duplicate = false;
        for (var c = 0; c < candidates.length; c++) {
          if (candidates[c][0] === clickable) {
            if (t.toLowerCase() === text.toLowerCase() || t.length < candidates[c][1].length) {
              candidates[c] = [clickable, t, t.toLowerCase() === text.toLowerCase()];
            }
            duplicate = true; break;
          }
        }
        if (!duplicate) candidates.push([clickable, t, t.toLowerCase() === text.toLowerCase()]);
      }
    }
    if (candidates.length) {
      var exactCandidates = candidates.filter(function(candidate) { return candidate[2]; });
      if (exactCandidates.length === 1) {
        candidates = exactCandidates;
      } else if (candidates.length > 1) {
        return __ikw_withPageDiag(__ikw_diagnostics(text, '', null, 'ambiguous_match'));
      }
      var best = candidates[0];
      best[0].click();
      if (text === targets[0]) {
        try { sessionStorage.setItem(__IKW_STOP_KEY, '1'); } catch (e) {}
      }
      var result = __ikw_diagnostics(text, best[1], best[0], 'clicked');
      result.clicked = true;
      return __ikw_withPageDiag(result);
    }
  }
  return __ikw_withPageDiag(__ikw_diagnostics('', '', null, 'not_found'));
}
"""

# One-shot version: used as a slow Python-polled fallback (see try_auto_click).
AUTO_CLICK_JS = CLICK_LOGIC_JS + (
    "(function(targets) { return __ikw_findAndClick(targets); })(%s)"
    % json.dumps(AUTO_CLICK_TARGETS)
)

# Persistent version: registered via Page.addScriptToEvaluateOnNewDocument
# (see register_and_navigate() below, which — unlike cdp_client's own
# inject_and_navigate() — also captures the registration's CDP response so
# it can later be unregistered; see remove_observer_script()) so it's
# already watching the DOM before the first paint of *every* document in
# this tab — including cross-origin OAuth redirects — and clicks the
# instant a target appears, instead of waiting on our next poll tick. This
# is what makes the click-through effectively instant rather than bounded
# by a sleep interval.
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
# That sessionStorage flag is deliberately origin-scoped (see
# CLICKABLE_HELPERS_JS's own comment on __ikw_findAndClick) — which covers
# same-origin back-navigation, but not the whole story: this login chain
# genuinely crosses at least one real origin boundary (info-kierowca.pl ->
# the gov.pl/login.gov.pl family), and the flag is only ever *set* on
# whichever origin the final tile happens to live on. Back out far enough to
# reach an earlier, different-origin page in the chain and this same script
# gets re-registered (Page.addScriptToEvaluateOnNewDocument fires again for
# any new document) onto a page where sessionStorage has no flag at all —
# and would auto-click forward all over again, undoing the user's own
# intentional back navigation. Sets a `window.__ikw_diag.observer_injected`
# marker the instant it runs (read by __ikw_pageDiag() above) purely as a
# Windows-stall diagnostic — confirms this script actually executed for the
# current document, not just that Chrome accepted the registration call.
# The actual fix for the cross-origin gap is Python-side, not another
# in-page flag: wait_for_cookies() unregisters this script outright
# (remove_observer_script(), via Page.removeScriptToEvaluateOnNewDocument)
# the moment it sees targets[0] has been reached (by itself or by this
# observer), so no *future* document — on any origin, forward or backward —
# gets this script injected again at all.
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
  try { window.__ikw_diag = window.__ikw_diag || {}; window.__ikw_diag.observer_injected = true; } catch (e) {}
  if (__ikw_stopped()) return;
  var scheduled = false;
  var observer = new MutationObserver(schedule);
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function() {
      scheduled = false;
      var clicked = __ikw_findAndClick(targets);
      if (clicked && clicked.clicked && clicked.requested_label === targets[0]) observer.disconnect();
    });
  }
  var clicked = __ikw_findAndClick(targets);
  if (clicked && clicked.clicked && clicked.requested_label === targets[0]) return;
  observer.observe(
    document, {childList: true, subtree: true, characterData: true, attributes: true}
  );
})(%s)
"""
    % json.dumps(AUTO_CLICK_TARGETS)
)


def try_auto_click(host, port, target=None):
    """Returns the click diagnostics dict for any outcome the JS itself
    completed (clicked or not — see reason: not_found/ambiguous_match/
    known_error_page/stopped), or None only when the eval call itself
    raised. Distinguishing those two is the whole point: a script that ran
    fine but matched nothing (e.g. the page rendered in an unexpected
    language — see --lang=pl-PL above) looks, from a single call, exactly
    like a CDP connection that never worked at all. Only the exception case
    is truly silent-by-design (Chrome may be mid-navigation); the no-match
    case is now visible via wait_for_cookies's periodic heartbeat log below.
    """
    try:
        result = sanitize_click_diagnostics(
            cdp_client.evaluate_in_page(host, port, AUTO_CLICK_JS, target=target)
        )
    except Exception as e:
        # Swallowed by design (Chrome may be mid-navigation) but logged: a
        # click failing here silently every 0.5s for the whole wait looks
        # identical, from the outside, to the tile chooser just never
        # matching — this print is what tells the two apart in
        # AUTO_REFRESH_LOG_FILE after the fact.
        print(f"try_auto_click error: {e!r}")
        return None
    if result and result.get("clicked"):
        # The browser's URL/host and non-secret DOM identity are useful
        # when a site markup change needs diagnosis. Do not log page text,
        # cookies, form values, or any authentication material. The
        # viewport/DPR/consent-overlay fields are the Windows-stall
        # diagnostics from __ikw_pageDiag() — cheap to always include here
        # since a successful click is exactly the moment worth recording
        # what "known-good" page state looked like, for comparison against a
        # future stuck report's heartbeat line below.
        print(
            "auto-click result "
            f"label={result['requested_label']!r} matched={result['matched_text']!r} "
            f"url={result.get('page_url', '')!r} host={result['page_host']!r} tag={result['tag']!r} "
            f"id={result['element_id']!r} class={result['element_class']!r} "
            f"href={result['href']!r} viewport={result.get('viewport_width')}x{result.get('viewport_height')} "
            f"dpr={result.get('device_pixel_ratio')} observer_injected={result.get('observer_injected')} "
            f"consent_overlay={result.get('consent_overlay_present')}"
        )
    return result


def register_and_navigate(host, port, target, url, script):
    """Register `script` to run on every future document in `target`, then
    navigate it to `url` — same effect as cdp_client.inject_and_navigate(),
    but also returns the Page.addScriptToEvaluateOnNewDocument registration
    identifier, which that shared helper discards.

    Needed for two things: (1) a Windows QR-login-stall diagnostic — logging
    whether registration actually succeeded rather than just assuming it did,
    since a silent registration failure would look identical, from the
    outside, to a page that simply never matches any click target; and (2)
    remove_observer_script() below, which needs the identifier to unregister
    the observer once it's no longer wanted. Duplicates
    cdp_client.navigate_target()'s three-call body rather than editing
    browser/cdp.py, which is out of scope for this change (see this file's
    own module boundary — browser/cdp.py is shared with booking.reschedule).
    """
    current = cdp_client.get_page_target(host, port, target.id)
    with cdp_client.cdp_socket(current.websocket_url) as sock:
        cdp_client.cdp_call(sock, 1, "Page.enable")
        result = cdp_client.cdp_call(
            sock, 2, "Page.addScriptToEvaluateOnNewDocument", {"source": script}
        )
        cdp_client.cdp_call(sock, 3, "Page.navigate", {"url": url})
    identifier = result.get("identifier")
    print(f"auto-click observer script registered: identifier={identifier!r}")
    return identifier


def remove_observer_script(host, port, target, identifier):
    """Best-effort unregister of the persistent click-observer once the
    final auto-click target (targets[0], "Aplikacja mObywatel") has
    definitely been reached — see AUTO_CLICK_OBSERVER_JS's own comment for
    why this exists: its sessionStorage stop-flag is origin-scoped, so it
    already prevents a same-origin back-navigation from re-clicking, but the
    login chain crosses at least one real origin boundary the flag never
    reaches. Backing up past that boundary would hand the observer a
    brand-new document with no stop-flag in sight, and — since
    Page.addScriptToEvaluateOnNewDocument re-injects it into *every* new
    document regardless of origin — it would auto-click forward all over
    again, undoing the user's own intentional back navigation.

    Removing the registration outright, instead of relying on any in-page
    flag, means no future document in this tab gets the observer at all once
    we're done — regardless of which origin it's on, forward or backward.
    Best-effort and silent on failure: by the time this is called the target
    tab may already be mid-navigation, and nothing past this point is
    supposed to click anything either way, so a failed unregister here is
    not itself a correctness problem — it only reopens the (much narrower,
    already-mostly-covered) origin-scoped gap this closes.
    """
    if not identifier:
        return
    try:
        current = cdp_client.get_page_target(host, port, target.id)
        with cdp_client.cdp_socket(current.websocket_url) as sock:
            cdp_client.cdp_call(
                sock, 1, "Page.removeScriptToEvaluateOnNewDocument", {"identifier": identifier}
            )
        print("auto-click observer unregistered (final target reached)")
    except Exception as e:
        print(f"couldn't unregister auto-click observer (non-fatal): {e!r}")


def open_google_messages_pairing(host="127.0.0.1", port=DEFAULT_PORT):
    """Open/reuse one explicitly identified Messages target in the persistent profile."""
    try:
        cdp_client.wait_for_debug_port(host, port, timeout=0.2)
    except Exception:
        chrome = find_chrome()
        ensure_private_profile_dir(PROFILE_DIR)
        subprocess.Popen([
            chrome, *chrome_debugging_args(port, PROFILE_DIR),
            "--no-first-run", "--no-default-browser-check", "--window-size=1100,850",
            "--app=about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cdp_client.wait_for_debug_port(host, port, timeout=20)
    provider = sms_provider.GoogleMessagesWebProvider(host, port)
    target = provider.find_or_create_target(create=True)
    cdp_client.bring_target_to_front(host, port, target)
    return target


def load_pz_credentials(config, store=None):
    """Retrieve PZ credentials only after a deliberate save succeeded.

    On Linux, asking Secret Service for a missing item can create a new
    collection and display an OS prompt.  Background relogin must never cause
    that prompt, so the nonsecret marker written after ``save()`` is checked
    before the keyring is touched.
    """
    if not config.get("pz_credential_present"):
        raise credential_store.CredentialNotFound(
            "No saved Profil Zaufany password was found."
        )
    username = (config.get("pz_username") or "").strip()
    if not username:
        raise credential_store.CredentialNotFound(
            "No saved Profil Zaufany password was found."
        )
    store = store or credential_store.SecureCredentialStore()
    return username, store.get(username)


def notify_desktop(summary, body, urgency="normal"):
    """Best-effort local desktop notification — the ntfy phone push is the
    primary alert, this is a secondary one for whoever's at the machine.

    notify-send is Linux-only; osascript is macOS's always-available
    equivalent (no third-party notifier needed, matching this project's
    zero-dependency stance). json.dumps() quotes summary/body as AppleScript
    string literals safely rather than interpolating them raw into the
    -e script text. UNVERIFIED as of 2026-07-22 — written without a live Mac
    to test on; worst case it silently no-ops there, same as before.
    """
    if sys.platform == "darwin":
        script = f"display notification {json.dumps(body)} with title {json.dumps(summary)}"
        try:
            subprocess.run(["osascript", "-e", script], check=False)
        except FileNotFoundError:
            pass
        return
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
        return ntfy_transport.NotificationOutcome(
            ntfy_transport.NOT_CONFIGURED, "No notification topic is configured."
        )
    topic = config.get("ntfy_topic")
    if not topic or not config.get("phone_alerts_relogin", True):
        return ntfy_transport.NotificationOutcome(
            ntfy_transport.NOT_CONFIGURED, "Phone alerts are disabled or not configured."
        )
    return ntfy_transport.push_ntfy(topic, title, message, priority=priority, tags=tags)


def acquire_lock(owner):
    if LOCK_FILE.exists():
        pid = relogin_control.lock_pid(LOCK_FILE)
        if pid is not None:
            if relogin_control.process_alive(pid):
                return False  # a refresh is already in progress
            # stale lock — the owning process is gone
    ensure_state_dir()
    # Remove leftovers before publishing this owner. Once its lock is visible,
    # the dashboard may legitimately write a request for this token.
    RESTART_REQUEST_FILE.unlink(missing_ok=True)
    relogin_control.write_lock(LOCK_FILE, owner)
    return True


def release_lock(owner):
    if relogin_control.read_lock(LOCK_FILE) == owner:
        relogin_control.clear_restart_request_if_owned(RESTART_REQUEST_FILE, owner)
        relogin_control.clear_if_owned(LOCK_FILE, owner)


class ReloginRestartRequested(Exception):
    """Raised only after this helper receives its own cooperative token."""


def wait_for_cookies(
    host, port, timeout, chrome_proc, target=None, should_restart=None, observer_identifier=None,
):
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

    `observer_identifier` (from register_and_navigate()) lets this stop
    forwarding progress entirely — not just cheaply no-op — once the final
    auto-click target has definitely been reached: `reached_target` below is
    a plain Python-side latch, immune to AUTO_CLICK_OBSERVER_JS's own
    sessionStorage flag being origin-scoped (see that constant's own
    comment), and this loop stops calling try_auto_click() at all once it's
    set, on top of unregistering the observer script via
    remove_observer_script() so no future document — this tab's own
    fallback poll or the injected observer, on any origin, reached by
    forward or backward navigation — auto-clicks anything again.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    # Periodic heartbeat, not per-poll: try_auto_click() now returns a
    # diagnostics dict on every completed eval, not just a successful click,
    # so a stuck run (page rendered in an unexpected language, site markup
    # changed, stuck on a "wkProcessUsed" error page, an unexpectedly small
    # viewport hiding the target behind a responsive layout, a never-fired
    # observer registration, ...) can be told apart from a CDP connection
    # that silently never worked — without spamming AUTO_REFRESH_LOG_FILE
    # every 0.5s for however long a human takes to scan the QR.
    heartbeat_interval = 30
    next_heartbeat = time.monotonic()
    reached_target = False
    while deadline is None or time.monotonic() < deadline:
        if should_restart and should_restart():
            raise ReloginRestartRequested
        if chrome_proc.poll() is not None:
            return None
        try:
            raw = cdp_client.fetch_cookies(host, port)
            cookies = cdp_client.extract_info_kierowca_cookies(raw)
            if cdp_client.COOKIE_NAMES <= cookies.keys():
                return cookies
        except Exception:
            pass  # Chrome may be mid-navigation; just retry
        if not reached_target:
            diagnostics = try_auto_click(host, port, target=target)
            # reason == 'stopped' means __ikw_stopped() saw the sessionStorage
            # flag already set — i.e. some eval (very possibly the injected
            # observer, faster than our own 0.5s cadence) already clicked
            # targets[0] before we got here. Treat that the same as clicking
            # it ourselves: either way the final target has been reached and
            # nothing should keep trying to click anything, on this document
            # or the next one.
            if diagnostics and (
                diagnostics.get("reason") == "stopped"
                or (diagnostics.get("clicked") and diagnostics.get("requested_label") == AUTO_CLICK_TARGETS[0])
            ):
                reached_target = True
                remove_observer_script(host, port, target, observer_identifier)
            # try_auto_click() already prints on a successful click; this is
            # only the "still waiting" case.
            elif diagnostics and not diagnostics.get("clicked") and time.monotonic() >= next_heartbeat:
                print(
                    "auto-click heartbeat: no target matched yet "
                    f"reason={diagnostics.get('reason')!r} url={diagnostics.get('page_url', '')!r} "
                    f"host={diagnostics.get('page_host', '')!r} "
                    f"viewport={diagnostics.get('viewport_width')}x{diagnostics.get('viewport_height')} "
                    f"dpr={diagnostics.get('device_pixel_ratio')} "
                    f"observer_injected={diagnostics.get('observer_injected')} "
                    f"consent_overlay={diagnostics.get('consent_overlay_present')}"
                )
                next_heartbeat = time.monotonic() + heartbeat_interval
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
    parser.add_argument(
        "--automatic", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--pz-confirm-delay", type=float, default=0, help=argparse.SUPPRESS
    )
    args = parser.parse_args()

    # Translate an external service-manager shutdown into SystemExit so the
    # finally block still closes Chrome and releases our owned lock. Dashboard
    # restarts use the cooperative token above and never signal a lock-file PID.
    def _terminate(signum, _frame):
        raise SystemExit(f"terminated by signal {signum}")

    signal.signal(signal.SIGTERM, _terminate)

    # stdout/stderr are redirected to a plain file (AUTO_REFRESH_LOG_FILE) when
    # launched via auth.launch.trigger_auto_refresh(), which fully-buffers by
    # default for a non-tty — without this, prints below (including
    # try_auto_click's failure logging) wouldn't actually land in the file
    # until the process exits, which could be hours into an unattended wait.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass  # older Python without reconfigure(); harmless to skip

    owner = relogin_control.new_owner()
    if not acquire_lock(owner):
        print("A refresh is already in progress (lock file present) — exiting.")
        return

    chrome_proc = None
    restart_shutdown = False
    backoff = RetryBackoff(RELOGIN_BACKOFF_FILE)
    try:
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except Exception:
            config = {}
        login_method = config.get("login_method", "mobywatel")
        headless = (
            login_method == "profil_zaufany"
            and config.get("headless_pz_login", False)
        )
        password = None
        if login_method == "profil_zaufany":
            username, password = load_pz_credentials(config)
        chrome = find_chrome()
        print(f"using browser: {chrome}")
        ensure_private_profile_dir(PROFILE_DIR)
        try:
            cdp_client.wait_for_debug_port("127.0.0.1", args.port, timeout=0.2)
            chrome_proc = AttachedChrome("127.0.0.1", args.port)
            print("using existing dedicated pairing browser")
        except Exception:
            chrome_proc = subprocess.Popen(
                [
                chrome,
                *authentication_chrome_args(args.port, PROFILE_DIR, headless=headless),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        if login_method == "mobywatel":
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
        login_target = cdp_client.create_page_target("127.0.0.1", args.port)
        if login_method == "profil_zaufany":
            messages = sms_provider.GoogleMessagesWebProvider("127.0.0.1", args.port)
            messages.find_or_create_target(create=True)
            # Messages may become the foreground tab when it is created, but
            # authentication remains on the retained auth target. Put that
            # target back in front so the visible browser reflects the active
            # state machine; target-scoped evaluation does not depend on focus.
            cdp_client.bring_target_to_front("127.0.0.1", args.port, login_target)
            browser = auth_providers.CDPProfilZaufanyBrowser(
                "127.0.0.1", args.port, login_target, chrome_proc, args.url
            )
            provider = auth_providers.ProfilZaufanyProvider(
                browser, messages, username, password,
                otp_confirm_delay=args.pz_confirm_delay,
                logger=lambda message: print(message),
            )
            provider.cancelled = lambda: relogin_control.restart_requested(
                RESTART_REQUEST_FILE, owner
            )
            cookies = provider.authenticate()
            password = None
        else:
            observer_identifier = register_and_navigate(
                "127.0.0.1", args.port, login_target, args.url, AUTO_CLICK_OBSERVER_JS
            )
            provider = auth_providers.MObywatelProvider(lambda: wait_for_cookies(
                "127.0.0.1", args.port, args.timeout, chrome_proc, target=login_target,
                should_restart=lambda: relogin_control.restart_requested(
                    RESTART_REQUEST_FILE, owner
                ),
                observer_identifier=observer_identifier,
            ))
            cookies = provider.authenticate()

        if cookies is None:
            if chrome_proc.poll() is not None:
                print("Chrome exited before logging in (crashed or was closed).")
                failure_reason = "browser_closed"
                notify_desktop(
                    "info-kierowca: relogin failed",
                    "Chrome closed before logging in — run the login flow again",
                    "critical",
                )
            else:
                print(f"No login detected within {args.timeout}s.")
                failure_reason = "authentication_timeout"
                notify_desktop(
                    "info-kierowca: relogin timed out",
                    f"No login detected within {args.timeout}s — run the login flow again when ready",
                    "critical",
                )
            if args.automatic:
                delay = backoff.record_failure(failure_reason)
                print(f"automatic relogin backoff: {delay}s ({failure_reason})")
            sys.exit(1)

        cdp_client.write_session_file(cookies)
        backoff.record_success()
        print(f"Wrote {len(cookies)} cookie(s) to {cdp_client.SESSION_FILE}")
        notify_desktop(
            "info-kierowca: session refreshed",
            "Logged back in — the notifier will pick it up on the next check",
        )
    except ReloginRestartRequested:
        restart_shutdown = True
        print("Explicit restart requested; closing the current QR login cleanly.")
    except auth_providers.AuthenticationFailure as exc:
        reason = exc.reason
        if reason == "restart_requested":
            restart_shutdown = True
            print("Explicit restart requested; closing the current Profil Zaufany login cleanly.")
            return
        print(f"Profil Zaufany authentication failed: {reason}")
        if args.automatic:
            delay = backoff.record_failure(reason)
            print(f"automatic relogin backoff: {delay}s ({reason})")
        messages = {
            "invalid_credentials": "Profil Zaufany credentials were rejected.",
            "profil_zaufany_inactive": "This account does not have a valid active Profil Zaufany.",
            "sms_provider_unavailable": "Google Messages is unavailable or no longer paired.",
            "messages_target_lost": "The Google Messages tab was closed.",
            "sms_timeout": "No fresh PZePUAP verification message arrived in time.",
        }
        body = "Automatic Profil Zaufany login failed: " + messages.get(
            reason, "open the app to retry or review the authentication log."
        )
        notify_desktop("info-kierowca: automatic login failed", body, "critical")
        push_ntfy("info-kierowca: automatic login failed", body, priority="default")
        sys.exit(1)
    except (credential_store.CredentialStorageUnavailable,
            credential_store.CredentialNotFound):
        reason = "secure_credential_unavailable"
        print(f"Profil Zaufany authentication failed: {reason}")
        if args.automatic:
            delay = backoff.record_failure(reason)
            print(f"automatic relogin backoff: {delay}s ({reason})")
        body = "Automatic Profil Zaufany login failed: secure credentials are unavailable."
        notify_desktop("info-kierowca: automatic login failed", body, "critical")
        push_ntfy("info-kierowca: automatic login failed", body, priority="default")
        sys.exit(1)
    except Exception:
        if args.automatic:
            delay = backoff.record_failure("authentication_flow_failed")
            print(f"automatic relogin backoff: {delay}s (authentication_flow_failed)")
        raise
    finally:
        if chrome_proc and (not args.keep_open or restart_shutdown):
            chrome_proc.terminate()
            try:
                chrome_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome_proc.kill()
                chrome_proc.wait(timeout=2)
        release_lock(owner)


if __name__ == "__main__":
    main()
