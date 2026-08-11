# info-kierowca-notifier

Slot checker for info-kierowca.pl (Polish driving exam booking). Polls two endpoints on a timer;
on a matching hit it can also open a pre-authenticated browser and click through to the reschedule
date-picker for your existing booking, but stops there — picking the new date and every confirm
step past that is always a real click from you (see `src/info_kierowca_notifier/booking/reschedule.py`). Runtime HTTPS
trust uses `truststore` (Python 3.10+) and `certifi`; source runs install these from
`pyproject.toml` with `uv sync`, while release binaries bundle both.

## Commands

```bash
uv run python -m info_kierowca_notifier                 # zero-setup entry point: loop thread + wizard/dashboard + auto-opens browser
uv run python -m info_kierowca_notifier.notifier --loop     # poller standalone, long-running (systemd oneshot omits --loop)
uv run python -m info_kierowca_notifier.notifier --interval 60   # fallback interval, only until config.json has poll_interval_seconds
uv run pyinstaller pyinstaller.spec  # build the single-file, no-console release binary
```

Run `uv run python -m unittest discover -s tests -v` and `uv run python -m pyflakes src tools tests` before
changes are published; the live-site and frozen-build checks below remain important for browser
automation. Regenerate the static snapshots with `tools/fetch_word_centers.py` / `tools/fetch_categories.py`.

## Repository layout

- `src/info_kierowca_notifier/` — installable application package. The package root owns the
  composed app, polling engine, paths, and the small TLS/ntfy helpers.
- `auth/`, `browser/`, `booking/`, and `web/` — domain modules. Keep dependencies pointed from
  app/orchestration toward these domains and from them toward shared browser/path infrastructure.
- `src/info_kierowca_notifier/data/` — JSON snapshots bundled as package resources and PyInstaller data.
- `tools/` — manually invoked maintenance and diagnostic utilities; these import the installed package.
- `tests/` — intentionally flat because the filenames already identify domains and flat discovery works
  consistently across supported Python versions.

## Files

- `src/info_kierowca_notifier/notifier.py` — the poller. Run standalone with `--loop`, or once per invocation (systemd
  oneshot service).
  HTTP/session-cookie mechanics live in `src/info_kierowca_notifier/client.py`; detached helper
  lifecycle lives in `auth/launch.py` and `booking/launch.py`.
  - Outcome vocabulary written to `status.json` via `update_status()`, and what
    `src/info_kierowca_notifier/web/server.py`'s frontend branches on: `slot_found`, `no_slot`, `auth_expired`,
    `network_error`, `unexpected`, `setup_incomplete`. `"outcome=unparseable"`/`"outcome=crash"`
    are `src/info_kierowca_notifier/notifier.py` log labels only, never actually written to `status.json` (an unparseable
    response is reported as `unexpected`; a crash in `loop()` leaves `status.json` on its prior
    outcome). `no_chromium_browser` is an unrelated return value from
    `trigger_auto_refresh()`/`trigger_open_browser()` (browser-launch probing) — not a
    `run_check()`/`status.json` outcome at all.
  - `network_error` (request never reached the server — `client.do_request` returns `status is None` on
    `URLError`) and `setup_incomplete` (no `config.json`; normal during first-run and right after
    Reset account, while the poll thread keeps ticking under the login screen) are both
    deliberately silent — no notification, no red state — so an outage or the login screen doesn't
    fire a critical popup every tick.
  - `is_urgent()` (what gates a phone push, the dashboard's red state, and `trigger_open_browser()`)
    compares a found slot's datetime against `config["current_slot_date"]` **exclusively**, by
    explicit user request: only a strictly earlier date counts, not a different time on the same
    date (see the function's own docstring for why, tied to `auto_confirm_reschedule` below). The
    dashboard/`status.json` history still list every hit found regardless — this only changes what
    counts as urgent enough to alert/act on.
  - The search endpoint (`MultipleCentersExams`) rejects any `organizationId` list whose length
    isn't exactly 5 (`400 Validation error: "Exactly 5 exam centers must be provided..."` —
    confirmed live 2026-07-18). `build_search_organization_ids()` pads `config["organization_ids"]`
    to 5 with random real ids from `word_centers.json`; results from centers outside the config are
    discarded afterward, so which fillers land doesn't matter. This makes 5 a hard ceiling, not
    just an API detail: `src/info_kierowca_notifier/app.py`'s center picker enforces `MAX_CENTERS = 5` (a JS literal) and
    `build_config()` rejects more server-side too, both against `notifier.SEARCH_ORG_ID_COUNT` —
    update all three if the API's count ever moves.
  - `client.fetch_pkk_profiles()`/`client.PKK_PROFILES_URL` (`GET /bknd/status/api/v1/pkk/get_profiles`, traced
    from the site's own `main-*.js` `pkkProfilesResource()`, confirmed live 2026-07-18) lets
    `src/info_kierowca_notifier/app.py`'s setup wizard prefill the PKK number and license category right after QR login
    instead of asking blind. Also returns `pesel`/`firstName`/`lastName`/`birthDate`, which are
    dropped — only `pkkNumber`/`categoryName` are kept, matching this project's minimal-footprint
    PII stance. Returns `[]` on any failure, so a fetch hiccup just falls back to manual entry.
  - Poll interval is `config.json`'s `poll_interval_seconds` (set via `src/info_kierowca_notifier/app.py`'s Settings),
    re-read fresh every cycle by `configured_poll_interval()` and clamped to
    `[MIN_POLL_INTERVAL_SECONDS, MAX_POLL_INTERVAL_SECONDS]` = `[15, 1800]` — the 15s floor is a
    deliberate good-citizen limit, lowered from 60s by explicit user request. `loop()`'s `interval`
    arg (from `--interval`/`src/info_kierowca_notifier/app.py`'s `INTERVAL`) is only the fallback for before `config.json` has
    a `poll_interval_seconds` yet. Every wait also goes through `jittered_wait()`, which adds up to
    `POLL_JITTER_FRACTION` (15%) extra delay — never subtracted, so the effective cadence never
    beats what's configured — expressed as a fraction of the interval so the randomness scales with
    whatever's picked.
  - `run_check()`'s hit-building loop also filters on hour-of-day:
    `config["earliest_slot_hour"]`/`["latest_slot_hour"]` (wizard's dual-handle time slider, 0-24,
    upper bound exclusive against `dt.hour`) are checked alongside the existing `wanted_types`/
    `watch_ids`/`max_date` filters, so a slot outside the preferred window never becomes a hit at
    all — no push, no `trigger_open_browser()`, nothing in `status.json`'s history either. Both
    keys default to `0`/`24` (the full day) via `config.get(...)` when absent, so a config
    predating this feature filters nothing, same as always.
  - `loop()` computes the exact post-jitter wait once per cycle and writes it forward as
    `dash_status["next_check_at"]` (an absolute timestamp) before sleeping — this is what both
    dashboards' next-check countdown reads instead of re-deriving an estimate from the base
    interval, so the countdown shown is the *exact* resolved time, jitter included.
  - `loop()` also takes a `wake_event` — `src/info_kierowca_notifier/app.py`'s `/setup` handler sets it right after saving a
    new `poll_interval_seconds` so the loop's current sleep (which could otherwise be up to the
    *old* interval long) is cut short immediately: the loop wakes, clears the event, re-checks, and
    recomputes `next_check_at` from the just-saved config. This replaced an earlier design where
    the `/setup` handler spawned a second, independent `run_check()` thread for the same "apply
    immediately" purpose — waking the one real loop thread instead removes the resulting race on
    `dash_status`/`status.json` between two threads checking concurrently.
- `src/info_kierowca_notifier/paths.py` — the single owner of every config/state file location (`CONFIG_FILE`, `SESSION_FILE`,
  `STATUS_FILE`, `PAUSE_FILE`, `AUTO_REFRESH_LOCK`, …). Imports nothing from the project so it can
  sit at the bottom of the import graph; `src/info_kierowca_notifier/notifier.py` re-exports the names it used to define, so
  `notifier.STATUS_FILE` and friends still resolve. These were previously re-spelled in six places
  across five modules — the promise that a frozen build and a `python -m info_kierowca_notifier` run share the same
  config/session/history holds only while every copy agrees, and a typo would have split state
  silently rather than failing loudly.
- `src/info_kierowca_notifier/web/server.py` — stdlib HTTP server, binds `127.0.0.1:8787`, serves `status.json` state.
  History entries carry only the fastest hit (`{"seen_at", "fastest"}`), not the whole hits list —
  the only field either dashboard renders, and a busy check returning dozens of hits would
  otherwise be rewritten every cycle and re-parsed by the page every 5s. Entries written before
  that narrowing still carry `hits`; the page reads `entry.fastest || fastestOf(entry.hits)`, so
  don't drop that fallback while anyone's `status.json` predates it. The next-check countdown reads
  `status.json`'s `next_check_at` directly (jitter already baked in — see `src/info_kierowca_notifier/notifier.py` above):
  `poll()` parses it into a page-level epoch-ms value, and `tickCountdown()` just diffs that against
  `Date.now()` every second — no client-side interval constant involved, so the display can't drift
  out of sync with a Settings-page interval change or the actual post-jitter wait.
- `tools/pull_session_cookies.py` — pulls session cookies from a running Chrome via remote-debugging
  port; writes them into `session.json`. Manual: you launch Chrome and log in first.
- `src/info_kierowca_notifier/auth/session.py` — launches Chrome (via `find_chrome()`: `CHROME_CANDIDATES` PATH names
  first, then `_chrome_from_macos_spotlight()`, then `CHROME_MAC_PATH`, then
  `_chrome_from_windows_registry()`, then `CHROME_WIN_PATHS`, then `EDGE_WIN_PATHS` last as the
  fallback for a Windows machine with no Chrome — preinstalled there, unlike Chrome). Both
  Windows-specific lookups exist because `CHROME_CANDIDATES`' names (`google-chrome` etc.) are a
  Linux/Mac PATH convention that a Windows Chrome install never registers under — without them,
  `find_chrome()` fell through to Edge on every Windows machine regardless of whether Chrome was
  actually installed (confirmed live 2026-07-21: Edge kept opening on a machine with Chrome
  present). `_chrome_from_windows_registry()` reads the standard "App Paths" registry key
  (`HKCU`/`HKLM` ...\App Paths\chrome.exe) — the same mechanism Windows itself uses to resolve a
  bare `chrome.exe` — and is checked before the hardcoded `CHROME_WIN_PATHS` guesses since it finds
  Chrome regardless of which drive/folder it was installed to; the fixed `%LOCALAPPDATA%`/Program
  Files paths stay only as a fallback for the rare install that skips that registry key.
  `_chrome_from_macos_spotlight()` is the same idea for macOS — `mdfind`'s bundle-identifier lookup
  finds Chrome regardless of install location (`/Applications`, `~/Applications`, or anywhere
  else), checked before the fixed `CHROME_MAC_PATH` guess (which only covers the common system-wide
  install) for the same reason the Windows registry lookup is checked before `CHROME_WIN_PATHS`.
  **UNVERIFIED as of 2026-07-22** — written without a live Mac to test on; `CHROME_MAC_PATH` stays
  as a fallback if it doesn't pan out. Launches into a dedicated throwaway
  profile at `info-kierowca.pl/login`, then auto-clicks through the gov.pl → "Aplikacja mObywatel"
  chooser via an injected DOM-mutation-observer (`AUTO_CLICK_TARGETS`/`AUTO_CLICK_OBSERVER_JS` —
  text-based, will break if the site's login UI text/labels change). The observer watches attribute
  changes as well as insertions (a tile revealed via a class/hidden toggle rather than a new node
  would otherwise only get clicked on the slower Python-side fallback poll), and disconnects itself
  the instant it clicks the final tile (a `sessionStorage` flag, `__ikw_findAndClick`, stops the
  fallback from re-clicking it too) — so backing out from the QR page to a different login method
  doesn't get auto-clicked straight back. Text-matching only considers visible elements
  (`__ikw_isVisible`) and, among equal-length matches, prefers the deeper/more specific element
  (`querySelectorAll` document order would otherwise let a wrapping `<div>` win over its own label).
  Then waits **indefinitely** for you to scan the QR and captures cookies the moment they appear.
  Auto-triggered by `src/info_kierowca_notifier/notifier.py` on `auth_expired` (`auth.launch.trigger_auto_refresh()`); guarded by a lock
  file at `~/.local/state/info-kierowca-notifier/auto-refresh.lock` so it won't relaunch while
  one's already in flight. Disable via `auto_refresh_chrome: false` in `config.json`.
  `auth.launch.trigger_auto_refresh()` launches it with stdout/stderr going to `paths.AUTO_REFRESH_LOG_FILE`
  (append mode) rather than `DEVNULL` — same rationale, and same separate-plain-file-not-`LOG_FILE`
  reasoning, as `booking.launch.trigger_open_browser()`'s `RESCHEDULE_LOG_FILE` below: a detached, auto-triggered
  run's own `print()`s (which browser binary got picked, any exception inside `try_auto_click()`,
  which target it clicked) were previously unreachable. `main()` reconfigures stdout/stderr to
  line-buffered on startup so those prints actually land in the file promptly instead of sitting in
  a full buffer for however long the QR wait takes. `notify_desktop()` (the local best-effort
  desktop toast alongside the ntfy phone push — same function name duplicated in `src/info_kierowca_notifier/notifier.py` as
  `notify()`, for the same circular-import reason as elsewhere in this project) used `notify-send`
  only, which is Linux-only and silently no-ops elsewhere; both now branch to `osascript` on macOS
  (`sys.platform == "darwin"`), quoting the summary/body via `json.dumps()` as safe AppleScript
  string literals rather than interpolating them raw. **UNVERIFIED as of 2026-07-22** — no live Mac
  to test on; worst case it silently no-ops there exactly as before.
- `src/info_kierowca_notifier/browser/cdp.py` — shared Chrome DevTools Protocol helpers used by `tools/pull_session_cookies.py`,
  `src/info_kierowca_notifier/auth/session.py`, and `src/info_kierowca_notifier/booking/reschedule.py` (cookie reads *and* writes via
  `Storage.getCookies`/`setCookies`, JS eval in the page, navigation, and registering a script to
  run on every future document via `Page.addScriptToEvaluateOnNewDocument`).
- `src/info_kierowca_notifier/browser/chrome.py` and `browser/clicking.py` — shared Chrome discovery/lifecycle and
  conservative click-safety primitives consumed by both auth and booking; neither module imports a domain flow.
  `chrome.py` owns `ensure_private_profile_dir()` (0700 profile dirs — they hold live session
  cookies) and `chrome_debugging_args()`; every launcher must go through both rather than
  hand-rolling flags. `CLICKABLE_HELPERS_JS` is a Python **raw** string, so its JS regexes take a
  single backslash: `__ikw_text()` carried `/\\s+/g` until 2026-08-11, which reaches the browser as
  "literal backslash followed by s" — a sequence page text never contains — so whitespace was never
  collapsed and every *exact* text match (`exact=true`, i.e. `SUMMARY_BUTTON_TEXT` and the
  highest-stakes `CONFIRM_SUMMARY_TEXT`) silently failed on any button whose label wrapped across
  lines. The sibling helpers (`\b`, `\d`) were always right; match them.
- `src/info_kierowca_notifier/booking/transaction.py` — fail-closed post-submit verification and
  privacy-safe diagnostics for the confirm path: scrapes `/cases` booking cards
  (`BOOKING_CARDS_JS`), classifies what changed (`classify_cards()`), and records a sanitised
  trace (`DiagnosticRecorder`, `os.open(..., 0o600)` + `O_EXCL`). Also the home of the shared
  exam-centre vocabulary (`CENTER_HELPERS_JS` for the browser side, `normalize_center()`/
  `centers_compatible()`/`center_conflict()`/`target_center()` for the Python side) that
  `reschedule.py` matches slots and summary modals with — keep the JS `__ikw_normCenter()` and the
  Python `normalize_center()` in step, since one reads the DOM and the other reads config/hit dicts.
  Two 2026-08-11 fixes here:
  - `classify_cards()` keyed VERIFIED_SUCCESS on `len(old_active) == 1 and len(current_active) == 1`,
    so anyone holding two active bookings (theory + practical is the ordinary case) could **never**
    verify a success even on a perfect match. `current_slot_date` then stayed stale, and a stale
    `current_slot_date` re-arms `notifier.is_urgent()` — so once the 900s cooldown expired, the next
    hit beating the *old* date could trigger another real confirm click, possibly for a slot worse
    than the one just booked. It now keys on the target card's own identity: exactly one matching
    card, that card not already active in the baseline, and the active-booking count not growing (a
    reschedule replaces, it doesn't add). VERIFIED_UNCHANGED generalises the same way. An empty
    baseline still verifies nothing.
  - `run_post_submit()` set `verification_target = None` on *any* exception, including the very
    first attempt — the likeliest moment for a destroyed execution context or socket timeout, right
    after `create_page_target()`/`navigate_target()`. One transient blip permanently disabled
    verification for the rest of the budget, reporting UNKNOWN for a reschedule that actually
    succeeded. Only `cdp.TargetNotFoundError`/`StaleTargetError` (a target genuinely gone) stop
    retrying now; transient errors cost one attempt and are recorded as `verification_attempt_failed`.
- `src/info_kierowca_notifier/booking/reschedule.py` — launches Chrome in its own dedicated profile (port `9555`, distinct
  from `src/info_kierowca_notifier/auth/session.py`'s and from a regular browsing profile) and injects the cookies
  already saved in `session.json` via `cdp_client.set_cookies()` before navigating to `/cases`, so
  it opens already authenticated. `set_cookies()` deliberately sets `httpOnly: False` — confirmed
  live that the site's own frontend reads session cookies via `document.cookie` to decide its
  logged-in UI state (it doesn't call `/jwt/refresh` on page load), so an httpOnly copy would be
  sent correctly on requests but invisible to the site's own JS, rendering as logged out. Also
  pre-sets a `CookieScriptConsent` cookie (`consent_cookie()`, defaulting "necessary only" — same
  minimal-footprint stance) shaped like what the real cookie-consent banner writes, so that banner
  never renders either.
  - Runnable by hand, and auto-triggered by `src/info_kierowca_notifier/notifier.py` on a matching urgent slot hit
    (`trigger_open_browser()`, called alongside the ntfy push in `run_check()`) — skipped if
    something's already listening on port `9555` so a slot that keeps reappearing doesn't pile up
    duplicate Chrome windows. Disable via `auto_open_browser: false` in `config.json`.
  - After landing on `/cases`, auto-clicks two buttons in sequence via `wait_and_click()`: "Zmień
    termin" (change date), then — once that opens the confirm-or-cancel modal — "Zmień termin
    rezerwacji" (confirm). Both text matches are deliberately narrow (exact-ish, not the login
    flow's fuzzy multi-target chooser) since the list page also has a nearby "Anuluj" (cancel the
    booking outright) button, and `CONFIRM_CHANGE_DATE_TEXT` is the longer, more specific phrase so
    it can't also match `CHANGE_DATE_TEXT`'s own button. Confirmed live this lands on the actual
    date-picker screen ("Wybierz datę początkową dla nowego terminu") with an empty range and a
    disabled "Przejdź do podsumowania" button — nothing about the booking has changed. Goes no
    further by default: picking the new date, the summary step, and any confirm past that stay
    real clicks from you; no reservation/booking call happens in this file. Reuses `find_chrome()`
    from `src/info_kierowca_notifier/browser/chrome.py` rather than duplicating it.
  - `--target-slot` is the one opt-in exception, gated behind config's experimental, default-off
    `auto_select_slot` flag (Settings → Automation toggle, off by default — hand-editing
    `config.json` still works too)
    — `trigger_open_browser()` only appends it when that flag is on, so an unset config behaves
    exactly as before. Given the fastest hit_dict notifier.py's own search already found
    (word/exam_type/datetime/places — the same object the push notification is built from),
    `try_select_target_slot()` expands the date group matching that datetime in the "Najbliższe
    dostępne terminy" list and clicks the radio button matching its exam type + time
    (`select_slot_js()`/`EXAM_TYPE_LABELS_PL`). Deliberately does not drive the "Data rozpoczęcia"
    date field — every hit notifier.py finds is already within the ~31-day window that list shows
    without it (confirmed live 2026-07-20). It then also clicks `SUMMARY_BUTTON_TEXT` ("Przejdź do
    podsumowania") via `wait_and_click_enabled()` once the selection has enabled it —
    `click_enabled_button_js()` checks `disabled`/`aria-disabled` itself rather than reusing
    `click_text_js()`, since a plain `.click()` on a still-disabled button is a silent no-op in
    most browsers and the poll loop needs to tell that apart from an actual click to keep retrying;
    it's shared between `SUMMARY_BUTTON_TEXT` and `CONFIRM_SUMMARY_TEXT` below. With
    `auto_select_slot` alone (`auto_confirm_reschedule` off), it stops unconditionally on landing on
    that "Potwierdź wybrany egzamin" summary modal — nothing past it is automated on either a
    successful or failed match, since a slot someone else just took or a DOM this hasn't been
    verified against must not submit anything on its own.
  - `--confirm-reschedule` (added by explicit user request after screenshotting the summary
    modal) is a second, separate opt-in — gated behind config's own
    also-experimental, default-off `auto_confirm_reschedule` flag, and only ever appended alongside
    `--target-slot` (`auto_confirm_reschedule` alone does nothing, since without `auto_select_slot`
    the flow never reaches this screen). It goes one click further than `auto_select_slot` alone:
    `wait_and_verify_summary()` first re-checks the summary modal's own visible text actually
    contains the target's date/time/exam-type (a safety check against `select_slot_js()` having
    matched the wrong radio row, biased to false-negative — i.e. err toward *not* confirming — over
    false-positive, since a mismatch here can't be caught any later), and only then clicks
    `CONFIRM_SUMMARY_TEXT` ("Potwierdź i przejdź dalej") via the same `wait_and_click_enabled()`.
    This is the single highest-stakes click in this entire project — the summary modal shows exam
    type/category/date-time/price with no separate payment step (screenshot-confirmed 2026-07-20),
    but unlike every click before it in this file, it actually submits the reservation change and
    can't be undone by just closing the tab. **Confirmed live 2026-07-28 that `select_slot_js()`'s
    original approach was wrong**: it queried `input[type="radio"]` and walked up 6 ancestor levels
    from each match looking for text containing both the exam label and time, but the real modal's
    slot rows aren't necessarily built on a native radio input, and the whole row rectangle is
    clickable (not just the circle) — so it silently found nothing and left the user to pick the
    slot by hand every time. Rewritten to reuse the same find-the-most-specific-matching-element-
    then-walk-up-to-a-clickable-ancestor pattern as `click_text_js`/`__ikw_findAndClick` (already
    proven live elsewhere in this project), matching on both the exam label and time substrings
    together. This fix itself is still unverified against a real confirm-reschedule run — so
    confirm the full flow actually finds/clicks/verifies the right thing before ever
    enabling `auto_confirm_reschedule` for real.
  - **2026-08-11 matching/safety pass** (a security review and a code review independently found
    overlapping bugs in exactly this path; all of the below is logic-level reasoning + the new
    Node-DOM tests in `tests/test_reschedule_slot_matching.py`, **none of it re-verified against
    the live site** — the same caveat the rest of this feature carries):
    - `select_slot_js()` matched exam-label + time **anywhere in the document**, and its
      `t.length <= best[1].length` tie-break preferred the *last* equal-length match in document
      order. Two visible date groups sharing a time/type therefore let the *later* date's row win,
      and the exam centre was never compared at all even though the hit dict carries it as `word`
      — so a same date/time/type slot at the wrong centre (in practice the existing booking's own)
      could be selected and confirmed. Now: the query is scoped to the subtree of the smallest
      visible element containing the target **date + label + time together**, the row must be
      unambiguous *within that subtree* (any disjoint second match abstains), and a centre named
      inside the group must be compatible with the target's. Every refusal returns a named reason
      (`date_group_not_found`, `ambiguous_date_group`, `ambiguous_slot_row`, `center_conflict`, …)
      that `_print_abstention()` logs once to `RESCHEDULE_LOG_FILE` — a fail-closed abstention used
      to be indistinguishable from "the page never rendered".
    - `wait_and_verify_summary()` read `document.body`, which includes the date-picker page still
      mounted *behind* the modal and already showing the expanded date group — so it could pass on
      the page behind a modal showing a different slot. Now scoped to the visible modal container
      (`MODAL_SELECTOR`, the same `[role="dialog"]/[aria-modal="true"]/dialog` set
      `transaction.PAGE_SNAPSHOT_JS` already records as `dialogs`), and it additionally requires a
      **positive** centre match — from the modal, falling back to the page. No modal, several
      plausible modals, a conflicting centre, or no centre named anywhere all refuse. `center_unknown`
      (page named none) and `center_unverifiable` (the *target* named none, i.e. a hand-written
      `--target-slot`) are deliberately distinct reasons. **This is the strictest new rule and the
      most likely to need a live-DOM adjustment**: if a real run refuses with `center_unknown`, the
      picker simply doesn't name the centre where this looks, and that's what needs revisiting —
      not the rule.
    - The final confirm click no longer goes through `_poll_until_truthy()`. That helper swallows
      every exception including `socket.timeout` (`cdp.cdp_call`'s socket has a 5s timeout) and
      retries — but `Runtime.evaluate` runs to completion in the page whether or not the response
      comes back, so a timed-out confirm may already have submitted, and the next iteration would
      click it again. `click_confirm_once()` instead makes the page the record: `confirm_click_js()`
      sets a sessionStorage marker (`CONFIRM_MARKER_KEY`) immediately *before* `.click()` and clears
      it again only when the click demonstrably didn't fire, so "marker present" means exactly "a
      click fired" and survives the navigation that click causes. A retry only ever happens on
      positive evidence nothing was clicked; a lost response returns `CONFIRM_ALREADY_CLICKED` and
      the flow goes straight to verification. A failing *probe* never falls through to a click.
    - `target_beats_current_slot()` re-reads `current_slot_date` from `config.json` right before the
      submit click and aborts if the target isn't strictly earlier (mirroring `notifier.is_urgent()`
      rather than importing it — notifier imports this module). The detached child can reach this
      point a minute or more after `run_check()` judged the slot urgent, and previously only re-read
      the ntfy topic. Fails closed on a missing/unparseable date.
    - Everything after the submit click is best-effort: `recorder.save()` goes through
      `save_diagnostic()`, and the observation/cooldown steps are individually guarded, so a failed
      diagnostic write (`O_EXCL` collision, full/unwritable state dir) can no longer propagate out
      and skip both the `current_slot_date` update *and* every `push_ntfy()` below it — which is
      exactly when the user most needs telling. Pushes now name the diagnostic by basename via
      `diagnostic_reference()`: the full path starts with `$HOME`, so interpolating it leaked the
      local OS username to ntfy.sh.
    - `record_confirm_cooldown()` returns a bool instead of swallowing failures (`except Exception:
      pass` silently removed the one gate on repeat confirm attempts); `try_select_target_slot()`
      refuses to submit if it can't arm, and `booking.launch` withholds `--confirm-reschedule`.
      `write_private_json()` replaces the old write-then-chmod for both this file and `config.json`
      — `os.open(..., 0o600)` on the temp file closes the umask window in which a world-readable
      temp copy of the whole config existed.
    - The Chrome launch here now uses `ensure_private_profile_dir()` + `chrome_debugging_args()`
      (as `auth/session.py` already did) instead of a bare `mkdir()` and hand-built flags: this
      profile has live session cookies injected into it and was being created 0755.
    - Dead code removed: `wait_and_verify_booking()` (no callers, and it called
      `classify_cards(cards, wanted, [])` with an empty baseline, which returns `UNKNOWN`
      unconditionally — so reinstating it as its docstring's "compatibility wrapper" would have
      silently broken) and `transaction.wait_for_booking_cards()`.
  - After `CONFIRM_SUMMARY_TEXT` is clicked, also by explicit user request (the
    button's own "i przejdź dalej" wording implies at least one more screen, so this deliberately
    doesn't try to read anything off of whatever page that click lands on): waits a couple seconds,
    navigates to `/cases` **in a separate CDP target** (the transaction tab is never navigated), and
    `transaction.run_post_submit()`/`classify_cards()` check whether a booking now shows there
    as our exact slot with a "Potwierdzona" (confirmed) status — not just date/time/exam-type match,
    since `/cases` also lists past/cancelled entries side by side (an "Anulowana" card right next to
    a "Potwierdzona" one, per screenshots) that could otherwise false-positive. The confirm click
    succeeding only means the button was clickable and got clicked, not that the backend accepted
    the change — this is the actual signal `update_current_slot_date()` is allowed to act on. On a
    match, it does a minimal, config.json-scoped read-modify-write (`current_slot_date` only, not
    the whole file — see its own docstring for why: this runs in a detached subprocess well after
    `src/info_kierowca_notifier/notifier.py`'s own config read for the cycle that triggered it, so the file may have picked up
    unrelated Settings edits since) so `notifier.is_urgent()`'s very next comparison reflects the
    change immediately — see that function's own bullet above for why this matters alongside the
    exclusive-urgency change. On no match within timeout, config is left untouched and the user is
    told to check/update it by hand — this never guesses at a new date.
  - Two follow-up fixes address the auto-triggered path writing all its outcomes nowhere and being
    able to re-fire before a prior attempt's own outcome was even known:
    - `booking.launch.trigger_open_browser()` launches this file with stdout/stderr going to
      `paths.RESCHEDULE_LOG_FILE` (append mode) instead of `DEVNULL` — otherwise every `print()` in
      `try_select_target_slot()` is unreachable on the auto-triggered path (visible only when run by
      hand from a terminal); the log file makes it inspectable after the fact.
      Deliberately a separate plain file, not shared with `notifier.LOG_FILE`: that one's written by
      a `RotatingFileHandler` from `src/info_kierowca_notifier/notifier.py`'s own process, and a detached subprocess writing raw
      stdout into the same path could straddle a rotation and silently write into an
      already-renamed file.
    - `push_ntfy()` (in this file — duplicated from `notifier.push_ntfy()`, same circular-import
      reason as elsewhere here, and deliberately no `tags` param to match this project's earlier
      decision to drop emoji tags from pushes) now fires, reusing `config`'s existing `ntfy_topic`,
      for every outcome from the point `auto_confirm_reschedule` starts trying to reach the real
      submit click onward: summary-mismatch abort, confirm button never becoming clickable,
      confirmed-but-unverified-on-`/cases`, and confirmed-and-verified. Scoped to only that stage —
      the earlier, lower-stakes `auto_select_slot`-only steps already got their own "slot found"
      push before the browser opened, and aren't worth a second alert on top of the log file.
    - `paths.RESCHEDULE_CONFIRM_COOLDOWN_FILE` is armed by `trigger_open_browser()` at **launch
      time** (moved there 2026-08-11) and refreshed by the child right before the real submit click
      is attempted (regardless of its outcome), and
      `notifier.confirm_reschedule_cooldown_active()` (checked in `trigger_open_browser()` before
      ever appending `--confirm-reschedule`) withholds that flag for
      `notifier.RESCHEDULE_CONFIRM_COOLDOWN_SECONDS` (900s, not user-configurable) after. This
      closes the gap the confirmed-but-unverified case leaves open: if the reschedule actually
      succeeded but `wait_and_verify_booking()` merely timed out, `current_slot_date` stays stale —
      without this cooldown, the very next poll cycle finding some other nearby slot could
      immediately attempt *another* real confirm click before a human has had any chance to see the
      push from the point above and step in. During the cooldown, `auto_select_slot` alone still
      runs normally (just without `--confirm-reschedule`) — only the actual submit step is held
      back. Writing it in the child only (as it was until 2026-08-11) left ~90s between launch and
      that write — Chrome start, `/cases` load, baseline capture, up to four 20s click waits — in
      which the sole guard was the port-9555 probe, and that probe fails *open* for the whole of
      Chrome's startup, so a second poll cycle in that window launched a duplicate confirm flow.
      Arming at launch closes it; the cost is that a run giving up before the submit click would
      burn 15 minutes for nothing, so `try_select_target_slot()` calls `release_confirm_cooldown()`
      on every pre-submit abort path. A crash between arming and that release leaves the gate armed,
      which is the safe direction.
  - A `--no-auto-click` flag skips both clicks and just leaves the logged-in `/cases` tab open —
    used by `src/info_kierowca_notifier/app.py`'s "Open browser" toolbar button (`trigger_open_browser(auto_click=False)`) so
    a manual troubleshooting click doesn't also kick off the reschedule flow; the automatic
    urgent-slot-hit trigger keeps the default `auto_click=True` since that click-through is the
    entire point there.
- `src/info_kierowca_notifier/app.py` — the composed, zero-setup entry point: runs `notifier.loop()` in a background thread,
  serves the first-run setup wizard + the dashboard + `POST /shutdown` (Quit button; hard-exits via
  `os._exit(0)`) from one stdlib HTTP server, and auto-opens the browser. This is what the packaged
  release binaries (`pyinstaller.spec`, built `--windowed`, no console window) actually run; shares
  `src/info_kierowca_notifier/paths.py`'s `CONFIG_DIR`/`STATE_DIR` with the source/systemd path so switching between "ran the
  binary" and "ran from source" never loses config/session/history. Detects an already-running
  instance on the dashboard port and just opens a browser tab at it instead of binding twice.
  - `POST /test-push` sends a one-off ntfy message so the user can confirm their topic works;
    `POST /reset-account` deletes `config.json`+`session.json` and drops back to first-run (the
    poll thread keeps running through it, which is why the missing-config path is the silent
    `setup_incomplete` outcome rather than a critical notification every tick).
  - Settings opens `/settings` in a modal (see toolbar below) rather than navigating there; `GET
    /settings` itself is unchanged and reuses `render_wizard()` — passed the existing `config.json`
    so the form comes back prefilled — rather than a separate edit page; submitting posts to the
    same `/setup` endpoint first-run setup uses, so `build_config()` stays the single place config
    validation lives.
  - "Check frequency" (in the merged "Automation" fieldset) is a range slider
    (`#poll_interval_slider`) over `POLL_INTERVAL_STEPS`, a hand-picked non-linear array
    (finer-grained near the low end, coarser near the high end) so it offers many more real
    positions than a dropdown's handful of presets, without a purely linear 15s–1800s scale wasting
    most of the range on intervals nobody wants. The slider's value is just an index into that
    array; the hidden `#poll_interval_seconds` holds the actual seconds submitted —
    `setPollIntervalSeconds()` snaps any existing config value to its nearest step when prefilling,
    so a pre-array value (or a raw `--interval` on the CLI) still lands somewhere sensible rather
    than silently resetting to the default. `POLL_INTERVAL_STEPS`' min/max must stay inside
    `notifier.MIN_POLL_INTERVAL_SECONDS`/`MAX_POLL_INTERVAL_SECONDS` — `build_config()` validates
    the submitted value against those independently, not the array, so a mismatch would only
    surface as the slider offering a step the server then rejects.
  - "Preferred time of day" (in "Exam & centers") is two overlapping native
    `input[type=range]` elements (`#time_from_slider`/`#time_to_slider`, hour granularity, 0-24)
    rather than one custom-built widget — each input's own track is hidden via CSS
    (`.dual-range input[type=range] { background: none; pointer-events: none; }`) with
    `pointer-events: auto` restored on just its thumb pseudo-element, the standard trick for two
    independently-draggable handles on one track without a pointer-capture library; a
    `.dual-range-fill` div drawn between them (`updateTimeWindow()`, positioned by percentage)
    supplies the "selected range" highlight that a single slider's track background normally would.
    `updateTimeWindow()` also enforces a minimum 1-hour gap — whichever handle just fired `input`
    wins and pushes the other one ahead of/behind it, so they can never cross or collapse to a
    zero-width (and so unmatchable-by-design) window. The hidden `#earliest_slot_hour`/
    `#latest_slot_hour` fields hold the actual submitted values; `setTimeWindow()` (mirroring
    `setPollIntervalSeconds()`) prefills both from `EXISTING_CONFIG`, defaulting to the full day
    (`0`/`24`) when absent. `build_config()` validates `0 <= earliest < latest <= 24` server-side
    independently of the slider, same pattern as the check-frequency slider above. Feeds
    `src/info_kierowca_notifier/notifier.py`'s hit filter — see its own bullet above — this file only decides what gets
    submitted, not what it does downstream.
  - "Automation" also has two toggles for the experimental
    `auto_select_slot`/`auto_confirm_reschedule` flags documented on `src/info_kierowca_notifier/booking/reschedule.py`
    above — both default off in the markup itself (no `on` class), matching the flags' own
    config-file default. `auto_confirm_reschedule`'s toggle only does anything once
    `auto_select_slot`'s is also on: `applyAutoConfirmDim()` dims its row and force-clears it via
    `setSwitch()` whenever the dependency is off, mirroring `applyNtfyDim()`'s existing
    dependent-field pattern — same idea, second instance. It's also the one switch on this page not
    wired through the shared `wireSwitch()`: turning it on runs a `confirm()` gate first (only on
    the on-transition, not off), since unlike every other toggle here this one lets the app submit
    a real, unreviewable reservation change — a stray click shouldn't be able to arm that silently.
    `build_config()` re-derives `auto_confirm_reschedule` as `auto_select_slot AND` the submitted
    value rather than trusting the JS-side dimming, so a hand-built POST body (or a config file
    edited while the two were briefly out of sync client-side) can't persist the meaningless
    confirm-without-select combination either.
  - First run is login-first, so the wizard can prefill the PKK number/category instead of asking
    blind: `GET /` serves `LOGIN_PAGE` (not the wizard) whenever neither `config.json` nor
    `session.json` exists yet. Its "Log in with mObywatel" button hits `POST /login-start`
    (`_handle_login_start()` → `trigger_auto_refresh(force=True)` — a deliberate retry bypasses
    persisted automatic backoff, while a live QR login remains protected), then polls `GET /login-status`
    (`{"ready": SESSION_FILE.exists(), "in_progress": auto_refresh_in_progress()}`) every 2s and
    redirects to `/` once ready.
  - Once `session.json` exists but `config.json` still doesn't, `/` renders the wizard with
    `build_pkk_prefill()`'s result — calls `client.fetch_pkk_profiles()` and maps each profile's
    `categoryName` to a `categories.json` id via `pkk_category_id()`, dropping any that don't map
    rather than guessing (an emptied list falls back to today's plain manual fields with no
    special-casing needed). With prefill data, the wizard shows a linked "pkkNumber — category"
    `<select>` (auto-selecting the first entry — most accounts only have one PKK profile) in place
    of the bare PKK field + category pills, with an "Enter manually instead" link to swap back (and
    a reverse link). `GET /setup` is the escape hatch — the login screen's skip link, and a stable
    direct URL — and always renders the plain manual-only wizard with no prefill, regardless of
    session state; `/settings` likewise never fetches a prefill. `_handle_setup` returns
    `needs_login` in its JSON response so the first-run "done" screen's Chrome/QR hint only shows
    when `session.json` didn't already exist by submit time — still true on the skip path, which
    still triggers `trigger_auto_refresh()` on submit exactly like every first run did before this
    existed.
  - Inside a frozen build, neither `trigger_auto_refresh()` nor `trigger_open_browser()` (both in
    `src/info_kierowca_notifier/notifier.py`) can shell out to their respective `.py` files (they don't exist on disk, and
    `sys.executable` is the bundled binary itself) — each re-invokes the binary with its own hidden
    flag instead (`--internal-auto-refresh`/`--internal-open-browser`), which `src/info_kierowca_notifier/app.py`'s
    `run_internal_auto_refresh()`/`run_internal_open_browser()` dispatch straight to
    `auto_refresh_session.main()`/`open_logged_in_browser.main()`. These frozen-only paths can only
    be verified against an actual build, not `python -m info_kierowca_notifier` — re-test both (delete `session.json`,
    confirm Chrome/Edge still opens for relogin; then, separately, confirm a slot hit still opens a
    logged-in tab) after any change here before tagging a release.
  - The dashboard's chrome is split across two files by design: `src/info_kierowca_notifier/web/server.py`'s `PAGE` owns
    the structural markup (`#headline-wrap`/`#headline-icon`/`#headline-hint`, and the `poll()` loop
    that fills them in) but leaves it inert — no cursor, no hover styling — since that file alone is
    also served read-only, with no `/pause`/`/settings`/`/manual-login`/`/shutdown` behind it.
    `TOOLBAR_HTML` (in `src/info_kierowca_notifier/web/templates.py`, appended before `</body>` by `src/info_kierowca_notifier/app.py`) layers the actual
    interactivity on top, so
    the plain systemd-dashboard path never shows an affordance it can't back up:
    - **Pause/Resume** is a click (or Enter/Space) on the headline itself, not a separate button.
      Writes `notifier.PAUSE_FILE` (`POST /pause`/`/resume`) — a flag file rather than a config
      field, checked at the top of `run_check()`, so it behaves identically under `src/info_kierowca_notifier/app.py`'s
      in-process loop and a systemd timer tick, and survives a settings resave. Pausing
      deliberately leaves `status.json`'s `outcome`/`message` alone (so Resume falls straight back
      to the last real result instead of being stuck on "Paused" until a fresh check); the handlers
      write `paused` synchronously and return it, read by `TOOLBAR_HTML` via the top-level
      `isPaused` `src/info_kierowca_notifier/web/server.py` declares (the two `<script>` tags share one global scope) —
      so the icon flips on click rather than lagging a whole interval.
    - **Open browser / Settings / Quit** are icon-only buttons in a toolbar that stays hidden until
      the pointer nears the top of the screen or it takes keyboard focus; a low-opacity dot keeps it
      discoverable. Geometry/styling live in `TOOLBAR_HTML`.
    - **Settings** opens `/settings` as a modal — `#ikw-settings-overlay` (a translucent, blurred
      backdrop) containing `#ikw-settings-frame`, an `<iframe>` pointed at `/settings` — rather than
      the old full-page navigation. An iframe was chosen over merging templates because it keeps
      `WIZARD_PAGE` and `dashboard_server.PAGE` fully independent (each still works loaded on its
      own — direct `/settings` visit, first-run `/setup`, the read-only `src/info_kierowca_notifier/web/server.py`-only
      path); the tradeoff is the form scrolls in its own inner viewport rather than the page's.
      `ikwOpenSettingsModal()` always sets `iframe.src` fresh from `about:blank` (which
      `ikwCloseSettingsModal()` resets it back to on every close) so the form is never stale without
      needing a cache-busting query string. Closes via the panel's close button, a backdrop click,
      or Escape. Because the iframe is same-origin, `WIZARD_PAGE` detects embedding via
      `IKW_EMBEDDED = window.parent !== window` and swaps its three `window.location.href = '/'`
      exits (close, save, Reset account) for `postMessage`s instead
      (`ikw-settings-close`/`-saved`/`-reset`), which `TOOLBAR_HTML`'s listener (checked against
      `window.location.origin`) turns into: closing the modal; closing it and calling `poll()`
      immediately so a changed interval/countdown shows without waiting up to 5s; and a full
      `location.href = '/'` (reset clears config/session — there's no dashboard state left to
      return to inside the modal, unlike a plain save). `IKW_EMBEDDED` being false is what keeps
      first-run `/setup` and a direct `/settings` visit navigating exactly as before.
    - **Open browser** (`POST /manual-login`, `_handle_manual_login()` — named for what it does
      rather than "Log in" since it covers two different outcomes) probes the session live via
      `check_session_valid()` (the same `REFRESH_URL` call `run_check()` makes) and routes to
      `trigger_open_browser(auto_click=False)` if the session's still good, or
      `trigger_auto_refresh(force=True)` if not — rather than guessing from file mtimes.
      `auto_click=False` is the important bit: this button is for opening the site or
      troubleshooting, not the reschedule flow, so it lands on `/cases` without clicking through to
      "Zmień termin" — unlike the automatic urgent-slot-hit trigger, which keeps `auto_click=True`
      so the date-picker is ready the moment the push lands. Here `force=True` only bypasses
      persisted automatic backoff; a live QR-login lock still returns "already_running".
      `trigger_open_browser()` has no equivalent because it does not have automatic retry backoff.
- `src/info_kierowca_notifier/web/templates.py` — holds `TOOLBAR_HTML`, `LOGIN_PAGE`, and `WIZARD_PAGE`: the three big HTML/JS
  strings `src/info_kierowca_notifier/app.py` serves, moved out verbatim since they made up the bulk of that file's line
  count (~1180 of ~1750 lines) with none of its request-handling logic. Plain string
  constants only — no rendering logic, no imports of its own; `src/info_kierowca_notifier/app.py` still owns everything that
  touches them (`WIZARD_PAGE.replace("__CENTERS_JSON__", ...)`, splicing `TOOLBAR_HTML` into
  `dashboard_server.PAGE`, etc.), so read `src/info_kierowca_notifier/app.py`'s own notes above for what each template does at
  runtime — this file is just where their markup lives.
- `word_centers.json` — static snapshot (id, name, location) of every active DORD/WORD/MORD/
  PORD/ZORD exam center, used by `src/info_kierowca_notifier/app.py`'s setup wizard to show real, searchable center names
  instead of bare numeric IDs. Baked in rather than fetched live because the wizard has to work
  before the user has ever logged in, and the source endpoint (`/bknd/config/api/v1/dict/words`)
  needs a session (confirmed: 401 without cookies). Regenerate with `tools/fetch_word_centers.py`.
- `tools/fetch_word_centers.py` — maintenance script, run by hand (using your own `session.json`) to
  refresh `word_centers.json` if info-kierowca.pl adds/renames/closes a center. Reuses
  `client.BASE`/`SESSION_FILE`/`client.do_request()` rather than duplicating cookie/request logic.
- `categories.json` — static snapshot (id, code, label) of all 17 license categories (A=1 …
  B=5 … PT=17), used by the setup wizard's "License category" dropdown so the user picks "B — car"
  instead of the bare numeric id the API wants. The wizard also keeps an "Other — enter number"
  escape hatch. Regenerate with `tools/fetch_categories.py`.
- `tools/fetch_categories.py` — maintenance script like `tools/fetch_word_centers.py`, run by hand with your
  own `session.json`. Categories are a two-source join: the **codes** (Am, A1, B, C1E, …) come from
  the Applications service's `GET /bknd/Applications/api/v1/dictionary/licence-category-groups`
  (note: a *different* base from `tools/fetch_word_centers.py`'s `/bknd/config/api/v1` — the category
  catalog lives under Applications, and there is **no `/dict/categories` endpoint**), but the
  **numeric ids** the exam-search `category` field wants are not served by any endpoint — the
  frontend hardcodes a code→id enum in its JS bundle, mirrored here as `CODE_TO_ID` (search `B:5`
  in `main-*.js` to re-derive it if the site ever adds a category). Verified against the live API
  on 2026-07-18: writes all 17 categories.
- `pyinstaller.spec` — builds `src/info_kierowca_notifier/app.py` into the single-file, no-console release binary; used by
  `.github/workflows/release.yml` (matrix over Windows/macOS/Linux, triggered on `v*` tags) and
  identical for manual local builds (`pyinstaller pyinstaller.spec`). PyInstaller is a build-time
  only dependency — doesn't change the "zero *runtime* dependencies" claim in the README.
- `systemd/*.service`, `systemd/*.timer` — source of truth for the systemd user units. These get
  copied to `~/.config/systemd/user/` — **edit the repo copy and re-`cp` + `daemon-reload`**, the
  deployed copy is not symlinked back to the repo.

## Runtime state (not in the repo)

- `~/.config/info-kierowca-notifier/config.json`, `session.json` — real config + live session
  cookies (chmod 600). Example templates are `config.example.json` / `session.example.json` in
  the repo.
- `~/.local/state/info-kierowca-notifier/notifier.log` — rotating log (2MB x3 backups).
- `~/.local/state/info-kierowca-notifier/status.json` — current status + history, what the
  dashboard reads and serves at `GET /status.json`.
- `~/.local/state/info-kierowca-notifier/reschedule.log` — plain append-only log of
  `booking/reschedule.py`'s own `print()`s when auto-triggered, including every fail-closed
  abstention reason from the slot/summary matching; chmod 0600 like every other state file (it
  inherited the umask until 2026-08-11). `reschedule-diagnostics/` (0700) holds the per-transaction
  sanitised traces, and `reschedule-confirm-cooldown` is the 15-minute repeat-confirm gate.
- `~/.local/state/info-kierowca-notifier/auto-refresh.log` — plain append-only log of
  `src/info_kierowca_notifier/auth/session.py`'s own `print()`s when auto-triggered (see `src/info_kierowca_notifier/auth/session.py`
  above); check this first when a relogin gets stuck partway through the login click-through.

## systemd units (Linux)

- `info-kierowca-notifier.timer` / `.service` — fires the poller every ~60s.
- `info-kierowca-dashboard.service` — long-running dashboard server on port 8787.

Useful commands:
```
systemctl --user status info-kierowca-notifier.timer
systemctl --user list-timers info-kierowca-notifier.timer   # check NEXT/Trigger is a real time, not n/a
journalctl --user -u info-kierowca-notifier.service -n 20 --no-pager
journalctl --user -u info-kierowca-dashboard.service -n 20 --no-pager
curl -s http://127.0.0.1:8787/status.json
```

### Known gotcha: timer looks "active" but never fires

`info-kierowca-notifier.timer` combines `OnActiveSec=10s` + `OnBootSec=1min` +
`OnUnitActiveSec=1min`. `OnActiveSec=10s` was added to fix a real incident: starting the timer well
after boot left `OnBootSec` already-elapsed (skipped) and `OnUnitActiveSec` without a reference
point (service had never run), so `systemctl --user start` reported `active` while `Trigger` stayed
`n/a` forever — it silently never fired. Don't remove `OnActiveSec`. After any `start`/`restart`,
verify with `systemctl --user list-timers info-kierowca-notifier.timer` that `NEXT` is a real
timestamp, not `-`/`n/a`.

### Known gotcha: auto-relogin (`auth.session`) needs a real GUI session

Triggered automatically by `src/info_kierowca_notifier/notifier.py` on `auth_expired` via `systemd-run --user`
(`trigger_auto_refresh()`), specifically so the launched Chrome + cookie-watcher survives after the
triggering oneshot `info-kierowca-notifier.service` run exits — a plain child process would
otherwise die with it under systemd's default `KillMode=control-group`. `systemd-run --user` still
needs `DISPLAY`/`WAYLAND_DISPLAY` imported into the systemd user manager (normal on a machine
you're desktop-logged-into; not there on a headless box or before first login) — if Chrome never
appears, check `journalctl --user -u info-kierowca-auto-refresh -n 20 --no-pager`. Set
`auto_refresh_chrome: false` in `config.json` to disable and fall back to manual relogin.

The gov.pl → "Aplikacja mObywatel" click-through is text-based (`AUTO_CLICK_TARGETS` in
`src/info_kierowca_notifier/auth/session.py`) — if info-kierowca.pl or gov.pl ever change that UI's copy or the login
click-path, the script will just sit on whatever screen it landed on without erring; it's still
safe to click through by hand while it waits (it never times out — see `DEFAULT_TIMEOUT`), but the
target list will need updating to restore full automation.

`wait_for_cookies()` bails out (and `main()`'s `finally` releases `AUTO_REFRESH_LOCK`) the moment
its own `chrome_proc.poll()` shows the launched Chrome has exited — confirmed live 2026-07-18: a
Chrome that had crashed hours earlier (visible only as a `<defunct>` zombie in `ps`, no window on
screen) left its wrapper spinning forever against a dead debug port, since a permanently-closed
connection was caught by the same `except Exception: pass` meant to tolerate Chrome being
mid-navigation — so the lock silently blocked every later `trigger_auto_refresh()` call with
nothing for the user to notice or close. This only covers a **crashed** Chrome. A genuinely
still-open QR window remains protected: both automatic and manual retries return "already_running"
instead of closing a login someone may be completing.

That forgotten-window case is a real reported bug, not a hypothetical: `AUTO_REFRESH_LOCK` has no
timeout (the script waits indefinitely for a QR scan) and the process is detached, so it outlives
an `src/info_kierowca_notifier/app.py` restart — one observed live held the lock for ~10 hours, silently no-opping every later
`trigger_auto_refresh()` call including the next launch, with nothing to indicate why. That is why
both automatic and deliberate retries preserve a live QR-login process. A manual `force=True`
only bypasses persisted automatic-failure backoff; it does not SIGTERM the lock holder. Dead or
malformed lock files are removed before launching a new process, avoiding a replacement Chrome
fighting over the same `--user-data-dir` as an active process.

### Known gotcha: a sandboxed app instance silently hands your curls to the real instance

`HOME=/tmp/fake-home python -m info_kierowca_notifier` looks isolated but isn't, for a second reason beyond the
`systemd-run` one below: `already_running()` probes `127.0.0.1:8787` *before* binding, and if
anything answers there — your own normal `src/info_kierowca_notifier/app.py`, left running from earlier — the sandboxed
process just opens a browser tab and exits. Its `HOME` override then applies to nothing, and every
subsequent `curl http://127.0.0.1:8787/...` in the test talks to the **real** instance against the
**real** config/session/status. Confirmed live 2026-07-18: a test run's `POST /pause` +
`POST /shutdown` paused and then killed the developer's actual running app, while the sandbox's own
state directory was never even created. Tell: the sandbox `HOME`'s
`.local/state/info-kierowca-notifier/` doesn't exist, the redirected app log is empty, and
`status.json` comes back with history predating the test. Check the port is free first
(`ss -ltn | grep 8787`), or run the sandboxed instance on another port.

### Known gotcha: dashboard port-in-use crash loop

`src/info_kierowca_notifier/web/server.py` binds `127.0.0.1:8787`. If a stale process (e.g. one started manually outside
systemd, or a previous crashed instance) is still holding the port,
`info-kierowca-dashboard.service` fails fast with `OSError: Address already in use`, retries a few
times, then systemd gives up (`start-limit-hit`). Find/kill whatever holds the port, then
`systemctl --user reset-failed info-kierowca-dashboard.service` before starting again — a plain
`start` after `start-limit-hit` is a no-op.

### Known gotcha: testing the app/auto-refresh in a sandbox on a machine with real units installed

`trigger_auto_refresh()` prefers `systemd-run --user` specifically so the Chrome+QR process
survives the triggering process exiting. That hand-off runs under the systemd user manager's own
environment, **not** the environment of the process that called `systemd-run` — so a sandboxed
`HOME` override (e.g. `HOME=/tmp/fake-home python -m info_kierowca_notifier`) does *not* propagate into the launched
`src/info_kierowca_notifier/auth/session.py`, which falls back to the real `~/.config`/`~/.local/state` paths
regardless. Confirmed live: a sandboxed `src/info_kierowca_notifier/app.py` test run's QR scan ended up refreshing the real
production `session.json`, not the sandboxed one — harmless (same account, just a fresh session),
but surprising if you're not expecting it. To test the auto-refresh trigger itself in real
isolation, set `auto_refresh_chrome: false` in the sandboxed `config.json` first.

## Constraints to respect when changing this code

- Polling/checking stays strictly read-only. The one deliberate exception is
  `src/info_kierowca_notifier/booking/reschedule.py`'s reschedule assist. By explicit user request, the policy ceiling was
  raised to allow fuller automation in future. By default the build still stops
  at the date-range picker: it clicks only "Zmień termin" and "Zmień termin rezerwacji" and lands on
  the empty "Wybierz datę początkową dla nowego terminu" screen with nothing selected. Picking the
  new date is implemented too, but only as an experimental, default-off
  opt-in (`auto_select_slot`, toggleable in Settings → Automation — see below —
  or by hand in `config.json`; unverified against the live site — see `src/info_kierowca_notifier/booking/reschedule.py`
  bullet above); with it on, it also clicks "Przejdź do
  podsumowania" and lands on the "Potwierdź wybrany egzamin" summary modal.
  **`auto_confirm_reschedule`** — a second, separate flag, added by explicit user
  request after they screenshotted that exact modal (exam type/category/date-time/price, no
  separate payment step) — goes past it: it re-verifies the modal matches the intended slot, then
  clicks "Potwierdź i przejdź dalej", actually submitting the reservation change. That is the
  ceiling — no code here goes past that confirm click; whatever screen follows it
  has never been scouted and stays real clicks from the user. This matches what the README and
  `docs/ADVANCED.md` tell users. When you extend automation past that confirm click, move all three
  docs (here, README, ADVANCED) together, and get the same kind of explicit sign-off again first —
  past that point mistakes act on a real, already-paid exam booking, same as this step already does,
  so treat any further extension with at least this much caution.
- Don't lower `notifier.MIN_POLL_INTERVAL_SECONDS` (15s, itself already lowered once from 60s by
  explicit user request) further without being asked again; the interval is
  user-adjustable within `[MIN_POLL_INTERVAL_SECONDS, MAX_POLL_INTERVAL_SECONDS]`
  (`poll_interval_seconds`, see `src/info_kierowca_notifier/notifier.py`/`src/info_kierowca_notifier/app.py` above) but the floor itself is a hard-coded
  design choice to stay a good citizen of an undocumented API, not just a UI default.
- Session cookies / PKK number must never be sent anywhere except info-kierowca.pl itself.
