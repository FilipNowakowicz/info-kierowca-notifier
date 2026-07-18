# info-kierowca-notifier

Slot checker for info-kierowca.pl (Polish driving exam booking). Polls two endpoints on a timer;
on a matching hit it can also open a pre-authenticated browser and click through to the reschedule
date-picker for your existing booking, but stops there — picking the new date and every confirm
step past that is always a real click from you (see `open_logged_in_browser.py`). Zero third-party
dependencies (stdlib only).

## Files

- `notifier.py` — the poller. Run standalone with `--loop`, or once per invocation (systemd
  oneshot service).
  - Outcome vocabulary written to `status.json` via `update_status()`, and what
    `dashboard_server.py`'s frontend branches on: `slot_found`, `no_slot`, `auth_expired`,
    `network_error`, `unexpected`, `setup_incomplete`. `"outcome=unparseable"`/`"outcome=crash"`
    are `notifier.py` log labels only, never actually written to `status.json` (an unparseable
    response is reported as `unexpected`; a crash in `loop()` leaves `status.json` on its prior
    outcome). `no_chromium_browser` is an unrelated return value from
    `trigger_auto_refresh()`/`trigger_open_browser()` (browser-launch probing) — not a
    `run_check()`/`status.json` outcome at all.
  - `network_error` (request never reached the server — `do_request` returns `status is None` on
    `URLError`) and `setup_incomplete` (no `config.json`; normal during first-run and right after
    Reset account, while the poll thread keeps ticking under the login screen) are both
    deliberately silent — no notification, no red state — so an outage or the login screen doesn't
    fire a critical popup every tick.
  - The search endpoint (`MultipleCentersExams`) rejects any `organizationId` list whose length
    isn't exactly 5 (`400 Validation error: "Exactly 5 exam centers must be provided..."` —
    confirmed live 2026-07-18). `build_search_organization_ids()` pads `config["organization_ids"]`
    to 5 with random real ids from `word_centers.json`; results from centers outside the config are
    discarded afterward, so which fillers land doesn't matter. This makes 5 a hard ceiling, not
    just an API detail: `app.py`'s center picker enforces `MAX_CENTERS = 5` (a JS literal) and
    `build_config()` rejects more server-side too, both against `notifier.SEARCH_ORG_ID_COUNT` —
    update all three if the API's count ever moves.
  - `fetch_pkk_profiles()`/`PKK_PROFILES_URL` (`GET /bknd/status/api/v1/pkk/get_profiles`, traced
    from the site's own `main-*.js` `pkkProfilesResource()`, confirmed live 2026-07-18) lets
    `app.py`'s setup wizard prefill the PKK number and license category right after QR login
    instead of asking blind. Also returns `pesel`/`firstName`/`lastName`/`birthDate`, which are
    dropped — only `pkkNumber`/`categoryName` are kept, matching this project's minimal-footprint
    PII stance. Returns `[]` on any failure, so a fetch hiccup just falls back to manual entry.
  - Poll interval is `config.json`'s `poll_interval_seconds` (set via `app.py`'s Settings),
    re-read fresh every cycle by `configured_poll_interval()` and clamped to
    `[MIN_POLL_INTERVAL_SECONDS, MAX_POLL_INTERVAL_SECONDS]` = `[15, 1800]` — the floor was
    explicitly lowered from the original 60s by user request on 2026-07-19. `loop()`'s `interval`
    arg (from `--interval`/`app.py`'s `INTERVAL`) is only the fallback for before `config.json` has
    a `poll_interval_seconds` yet. Every wait also goes through `jittered_wait()`, which adds up to
    `POLL_JITTER_FRACTION` (15%) extra delay — never subtracted, so the effective cadence never
    beats what's configured — expressed as a fraction of the interval so the randomness scales with
    whatever's picked.
  - `loop()` computes the exact post-jitter wait once per cycle and writes it forward as
    `dash_status["next_check_at"]` (an absolute timestamp) before sleeping — this is what both
    dashboards' next-check countdown reads instead of re-deriving an estimate from the base
    interval, so the countdown shown is the *exact* resolved time, jitter included.
  - `loop()` also takes a `wake_event` — `app.py`'s `/setup` handler sets it right after saving a
    new `poll_interval_seconds` so the loop's current sleep (which could otherwise be up to the
    *old* interval long) is cut short immediately: the loop wakes, clears the event, re-checks, and
    recomputes `next_check_at` from the just-saved config. This replaced an earlier design where
    the `/setup` handler spawned a second, independent `run_check()` thread for the same "apply
    immediately" purpose — waking the one real loop thread instead removes the resulting race on
    `dash_status`/`status.json` between two threads checking concurrently.
- `paths.py` — the single owner of every config/state file location (`CONFIG_FILE`, `SESSION_FILE`,
  `STATUS_FILE`, `PAUSE_FILE`, `AUTO_REFRESH_LOCK`, …). Imports nothing from the project so it can
  sit at the bottom of the import graph; `notifier.py` re-exports the names it used to define, so
  `notifier.STATUS_FILE` and friends still resolve. These were previously re-spelled in six places
  across five modules — the promise that a frozen build and a `python app.py` run share the same
  config/session/history holds only while every copy agrees, and a typo would have split state
  silently rather than failing loudly.
- `dashboard_server.py` — stdlib HTTP server, binds `127.0.0.1:8787`, serves `status.json` state.
  History entries carry only the fastest hit (`{"seen_at", "fastest"}`), not the whole hits list —
  the only field either dashboard renders, and a busy check returning dozens of hits would
  otherwise be rewritten every cycle and re-parsed by the page every 5s. Entries written before
  that narrowing still carry `hits`; the page reads `entry.fastest || fastestOf(entry.hits)`, so
  don't drop that fallback while anyone's `status.json` predates it. The next-check countdown reads
  `status.json`'s `next_check_at` directly (jitter already baked in — see `notifier.py` above):
  `poll()` parses it into a page-level epoch-ms value, and `tickCountdown()` just diffs that against
  `Date.now()` every second — no client-side interval constant involved, so the display can't drift
  out of sync with a Settings-page interval change or the actual post-jitter wait.
- `pull_session_cookies.py` — pulls session cookies from a running Chrome via remote-debugging
  port; writes them into `session.json`. Manual: you launch Chrome and log in first.
- `auto_refresh_session.py` — launches Chrome (or, via `find_chrome()`'s `CHROME_CANDIDATES`/
  `EDGE_WIN_PATHS` fallback, Edge — preinstalled on Windows, unlike Chrome) in a dedicated throwaway
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
  Auto-triggered by `notifier.py` on `auth_expired` (`trigger_auto_refresh()`); guarded by a lock
  file at `~/.local/state/info-kierowca-notifier/auto-refresh.lock` so it won't relaunch while
  one's already in flight. Disable via `auto_refresh_chrome: false` in `config.json`.
- `cdp_client.py` — shared Chrome DevTools Protocol helpers used by `pull_session_cookies.py`,
  `auto_refresh_session.py`, and `open_logged_in_browser.py` (cookie reads *and* writes via
  `Storage.getCookies`/`setCookies`, JS eval in the page, navigation, and registering a script to
  run on every future document via `Page.addScriptToEvaluateOnNewDocument`).
- `open_logged_in_browser.py` — launches Chrome in its own dedicated profile (port `9555`, distinct
  from `auto_refresh_session.py`'s and from a regular browsing profile) and injects the cookies
  already saved in `session.json` via `cdp_client.set_cookies()` before navigating to `/cases`, so
  it opens already authenticated. `set_cookies()` deliberately sets `httpOnly: False` — confirmed
  live that the site's own frontend reads session cookies via `document.cookie` to decide its
  logged-in UI state (it doesn't call `/jwt/refresh` on page load), so an httpOnly copy would be
  sent correctly on requests but invisible to the site's own JS, rendering as logged out. Also
  pre-sets a `CookieScriptConsent` cookie (`consent_cookie()`, defaulting "necessary only" — same
  minimal-footprint stance) shaped like what the real cookie-consent banner writes, so that banner
  never renders either.
  - Runnable by hand, and auto-triggered by `notifier.py` on a matching urgent slot hit
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
    further: picking the new date, the summary step, and any confirm past that stay real clicks
    from you; no reservation/booking call happens in this file. Reuses `find_chrome()` from
    `auto_refresh_session.py` rather than duplicating it.
  - A `--no-auto-click` flag skips both clicks and just leaves the logged-in `/cases` tab open —
    used by `app.py`'s "Open browser" toolbar button (`trigger_open_browser(auto_click=False)`) so
    a manual troubleshooting click doesn't also kick off the reschedule flow; the automatic
    urgent-slot-hit trigger keeps the default `auto_click=True` since that click-through is the
    entire point there.
- `app.py` — the composed, zero-setup entry point: runs `notifier.loop()` in a background thread,
  serves the first-run setup wizard + the dashboard + `POST /shutdown` (Quit button; hard-exits via
  `os._exit(0)`) from one stdlib HTTP server, and auto-opens the browser. This is what the packaged
  release binaries (`pyinstaller.spec`, built `--windowed`, no console window) actually run; shares
  `paths.py`'s `CONFIG_DIR`/`STATE_DIR` with the source/systemd path so switching between "ran the
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
  - First run is login-first, so the wizard can prefill the PKK number/category instead of asking
    blind: `GET /` serves `LOGIN_PAGE` (not the wizard) whenever neither `config.json` nor
    `session.json` exists yet. Its "Log in with mObywatel" button hits `POST /login-start`
    (`_handle_login_start()` → `trigger_auto_refresh(force=True)` — same `force=True` rationale as
    the toolbar's "Open browser" button below, since a stale lock from a forgotten QR window must
    not silently no-op a user's own deliberate first-run click), then polls `GET /login-status`
    (`{"ready": SESSION_FILE.exists(), "in_progress": auto_refresh_in_progress()}`) every 2s and
    redirects to `/` once ready.
  - Once `session.json` exists but `config.json` still doesn't, `/` renders the wizard with
    `build_pkk_prefill()`'s result — calls `notifier.fetch_pkk_profiles()` and maps each profile's
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
    `notifier.py`) can shell out to their respective `.py` files (they don't exist on disk, and
    `sys.executable` is the bundled binary itself) — each re-invokes the binary with its own hidden
    flag instead (`--internal-auto-refresh`/`--internal-open-browser`), which `app.py`'s
    `run_internal_auto_refresh()`/`run_internal_open_browser()` dispatch straight to
    `auto_refresh_session.main()`/`open_logged_in_browser.main()`. These frozen-only paths can only
    be verified against an actual build, not `python app.py` — re-test both (delete `session.json`,
    confirm Chrome/Edge still opens for relogin; then, separately, confirm a slot hit still opens a
    logged-in tab) after any change here before tagging a release.
  - The dashboard's chrome is split across two files by design: `dashboard_server.py`'s `PAGE` owns
    the structural markup (`#headline-wrap`/`#headline-icon`/`#headline-hint`, and the `poll()` loop
    that fills them in) but leaves it inert — no cursor, no hover styling — since that file alone is
    also served read-only, with no `/pause`/`/settings`/`/manual-login`/`/shutdown` behind it.
    `app.py`'s `TOOLBAR_HTML` (appended before `</body>`) layers the actual interactivity on top, so
    the plain systemd-dashboard path never shows an affordance it can't back up:
    - **Pause/Resume** is a click (or Enter/Space) on the headline itself, not a separate button.
      Writes `notifier.PAUSE_FILE` (`POST /pause`/`/resume`) — a flag file rather than a config
      field, checked at the top of `run_check()`, so it behaves identically under `app.py`'s
      in-process loop and a systemd timer tick, and survives a settings resave. Pausing
      deliberately leaves `status.json`'s `outcome`/`message` alone (so Resume falls straight back
      to the last real result instead of being stuck on "Paused" until a fresh check); the handlers
      write `paused` synchronously and return it, read by `TOOLBAR_HTML` via the top-level
      `isPaused` `dashboard_server.py` declares (the two `<script>` tags share one global scope) —
      so the icon flips on click rather than lagging a whole interval.
    - **Open browser / Settings / Quit** are icon-only buttons in a toolbar that stays hidden until
      the pointer nears the top of the screen or it takes keyboard focus; a low-opacity dot keeps it
      discoverable. Geometry/styling live in `TOOLBAR_HTML`.
    - **Settings** opens `/settings` as a modal — `#ikw-settings-overlay` (a translucent, blurred
      backdrop) containing `#ikw-settings-frame`, an `<iframe>` pointed at `/settings` — rather than
      the old full-page navigation. An iframe was chosen over merging templates because it keeps
      `WIZARD_PAGE` and `dashboard_server.PAGE` fully independent (each still works loaded on its
      own — direct `/settings` visit, first-run `/setup`, the read-only `dashboard_server.py`-only
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
      so the date-picker is ready the moment the push lands. Why `force=True` here: see the
      auto-relogin lock gotcha below. `trigger_open_browser()` has no equivalent `force` — forcing
      there would mean a second Chrome fighting over the same fixed debug port an already-open one
      is using, so "already_running" is the desired outcome, not something to override.
- `word_centers.json` — static snapshot (id, name, location) of every active DORD/WORD/MORD/
  PORD/ZORD exam center, used by `app.py`'s setup wizard to show real, searchable center names
  instead of bare numeric IDs. Baked in rather than fetched live because the wizard has to work
  before the user has ever logged in, and the source endpoint (`/bknd/config/api/v1/dict/words`)
  needs a session (confirmed: 401 without cookies). Regenerate with `fetch_word_centers.py`.
- `fetch_word_centers.py` — maintenance script, run by hand (using your own `session.json`) to
  refresh `word_centers.json` if info-kierowca.pl adds/renames/closes a center. Reuses
  `notifier.BASE`/`SESSION_FILE`/`do_request()` rather than duplicating cookie/request logic.
- `categories.json` — static snapshot (id, code, label) of all 17 license categories (A=1 …
  B=5 … PT=17), used by the setup wizard's "License category" dropdown so the user picks "B — car"
  instead of the bare numeric id the API wants. The wizard also keeps an "Other — enter number"
  escape hatch. Regenerate with `fetch_categories.py`.
- `fetch_categories.py` — maintenance script like `fetch_word_centers.py`, run by hand with your
  own `session.json`. Categories are a two-source join: the **codes** (Am, A1, B, C1E, …) come from
  the Applications service's `GET /bknd/Applications/api/v1/dictionary/licence-category-groups`
  (note: a *different* base from `fetch_word_centers.py`'s `/bknd/config/api/v1` — the category
  catalog lives under Applications, and there is **no `/dict/categories` endpoint**), but the
  **numeric ids** the exam-search `category` field wants are not served by any endpoint — the
  frontend hardcodes a code→id enum in its JS bundle, mirrored here as `CODE_TO_ID` (search `B:5`
  in `main-*.js` to re-derive it if the site ever adds a category). Verified against the live API
  on 2026-07-18: writes all 17 categories.
- `pyinstaller.spec` — builds `app.py` into the single-file, no-console release binary; used by
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

### Known gotcha: auto-relogin (auto_refresh_session.py) needs a real GUI session

Triggered automatically by `notifier.py` on `auth_expired` via `systemd-run --user`
(`trigger_auto_refresh()`), specifically so the launched Chrome + cookie-watcher survives after the
triggering oneshot `info-kierowca-notifier.service` run exits — a plain child process would
otherwise die with it under systemd's default `KillMode=control-group`. `systemd-run --user` still
needs `DISPLAY`/`WAYLAND_DISPLAY` imported into the systemd user manager (normal on a machine
you're desktop-logged-into; not there on a headless box or before first login) — if Chrome never
appears, check `journalctl --user -u info-kierowca-auto-refresh -n 20 --no-pager`. Set
`auto_refresh_chrome: false` in `config.json` to disable and fall back to manual relogin.

The gov.pl → "Aplikacja mObywatel" click-through is text-based (`AUTO_CLICK_TARGETS` in
`auto_refresh_session.py`) — if info-kierowca.pl or gov.pl ever change that UI's copy or the login
click-path, the script will just sit on whatever screen it landed on without erring; it's still
safe to click through by hand while it waits (it never times out — see `DEFAULT_TIMEOUT`), but the
target list will need updating to restore full automation.

`wait_for_cookies()` bails out (and `main()`'s `finally` releases `AUTO_REFRESH_LOCK`) the moment
its own `chrome_proc.poll()` shows the launched Chrome has exited — confirmed live 2026-07-18: a
Chrome that had crashed hours earlier (visible only as a `<defunct>` zombie in `ps`, no window on
screen) left its wrapper spinning forever against a dead debug port, since a permanently-closed
connection was caught by the same `except Exception: pass` meant to tolerate Chrome being
mid-navigation — so the lock silently blocked every later `trigger_auto_refresh()` call with
nothing for the user to notice or close. This only covers a **crashed** Chrome, not a genuinely
still-open QR window someone forgot about — that case is unchanged and correctly not force-cleared
by the automatic path; the "Open browser" button's `force=True` is still what clears that one.

That forgotten-window case is a real reported bug, not a hypothetical: `AUTO_REFRESH_LOCK` has no
timeout (the script waits indefinitely for a QR scan) and the process is detached, so it outlives
an `app.py` restart — one observed live held the lock for ~10 hours, silently no-opping every later
`trigger_auto_refresh()` call including the next launch, with nothing to indicate why. That is why
the *automatic* path stays conservative (a background retry must never kill a window someone is
mid-scan on) and only the deliberate button click opts into `force`. `force` SIGTERMs the lock
holder and waits (~5s) for it to actually exit before relaunching: `auto_refresh_session.py`
installs a SIGTERM handler so its `finally` still runs — without one, Python dies immediately, its
Chrome child survives as an orphan still holding `PROFILE_DIR`, and the replacement Chrome launched
against that same `--user-data-dir` delegates to the orphan and exits instantly, tripping "Chrome
closed before logging in" on every retry. The wait also matters on the systemd path, where
`--unit=info-kierowca-auto-refresh` is a fixed name systemd-run refuses to reissue while the old
unit is still deactivating.

### Known gotcha: a sandboxed app.py silently hands your curls to the real instance

`HOME=/tmp/fake-home python app.py` looks isolated but isn't, for a second reason beyond the
`systemd-run` one below: `already_running()` probes `127.0.0.1:8787` *before* binding, and if
anything answers there — your own normal `app.py`, left running from earlier — the sandboxed
process just opens a browser tab and exits. Its `HOME` override then applies to nothing, and every
subsequent `curl http://127.0.0.1:8787/...` in the test talks to the **real** instance against the
**real** config/session/status. Confirmed live 2026-07-18: a test run's `POST /pause` +
`POST /shutdown` paused and then killed the developer's actual running app, while the sandbox's own
state directory was never even created. Tell: the sandbox `HOME`'s
`.local/state/info-kierowca-notifier/` doesn't exist, the redirected app log is empty, and
`status.json` comes back with history predating the test. Check the port is free first
(`ss -ltn | grep 8787`), or run the sandboxed instance on another port.

### Known gotcha: dashboard port-in-use crash loop

`dashboard_server.py` binds `127.0.0.1:8787`. If a stale process (e.g. one started manually outside
systemd, or a previous crashed instance) is still holding the port,
`info-kierowca-dashboard.service` fails fast with `OSError: Address already in use`, retries a few
times, then systemd gives up (`start-limit-hit`). Find/kill whatever holds the port, then
`systemctl --user reset-failed info-kierowca-dashboard.service` before starting again — a plain
`start` after `start-limit-hit` is a no-op.

### Known gotcha: testing app.py/auto-refresh in a sandbox on a machine with real units installed

`trigger_auto_refresh()` prefers `systemd-run --user` specifically so the Chrome+QR process
survives the triggering process exiting. That hand-off runs under the systemd user manager's own
environment, **not** the environment of the process that called `systemd-run` — so a sandboxed
`HOME` override (e.g. `HOME=/tmp/fake-home python app.py`) does *not* propagate into the launched
`auto_refresh_session.py`, which falls back to the real `~/.config`/`~/.local/state` paths
regardless. Confirmed live: a sandboxed `app.py` test run's QR scan ended up refreshing the real
production `session.json`, not the sandboxed one — harmless (same account, just a fresh session),
but surprising if you're not expecting it. To test the auto-refresh trigger itself in real
isolation, set `auto_refresh_chrome: false` in the sandboxed `config.json` first.

## Constraints to respect when changing this code

- Polling/checking stays strictly read-only. The one deliberate exception is
  `open_logged_in_browser.py`'s reschedule assist. As of 2026-07-17, by explicit user request, the
  policy ceiling was raised to allow fuller automation in future (picking the new date, and
  eventually the summary/confirm steps) — but the **current build deliberately stops at the
  date-range picker**: it clicks only "Zmień termin" and "Zmień termin rezerwacji" and lands on the
  empty "Wybierz datę początkową dla nowego terminu" screen with nothing selected. Picking the new
  date, the summary step, and every confirm past that are still real clicks from the user, and no
  code here selects a date or submits a reservation change. This matches what the README and
  `docs/ADVANCED.md` tell users. When you do extend automation past the date picker, move all three
  docs (here, README, ADVANCED) together, and get the same kind of explicit sign-off for anything
  past the summary screen, since past that point mistakes act on a real, already-paid exam booking.
- Don't lower `notifier.MIN_POLL_INTERVAL_SECONDS` (15s, itself already lowered once from 60s by
  explicit user request on 2026-07-19) further without being asked again; the interval is
  user-adjustable within `[MIN_POLL_INTERVAL_SECONDS, MAX_POLL_INTERVAL_SECONDS]`
  (`poll_interval_seconds`, see `notifier.py`/`app.py` above) but the floor itself is a hard-coded
  design choice to stay a good citizen of an undocumented API, not just a UI default.
- Session cookies / PKK number must never be sent anywhere except info-kierowca.pl itself.
