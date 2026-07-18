# info-kierowca-notifier

Slot checker for info-kierowca.pl (Polish driving exam booking). Polls two endpoints on a timer;
on a matching hit it can also open a pre-authenticated browser and click through to the reschedule
date-picker for your existing booking, but stops there — picking the new date and every confirm
step past that is always a real click from you (see `open_logged_in_browser.py`). Zero third-party
dependencies (stdlib only).

## Files

- `notifier.py` — the poller. Run standalone with `--loop`, or once per invocation (used by the
  systemd oneshot service). The search endpoint (`MultipleCentersExams`) rejects any
  `organizationId` list whose length isn't exactly 5 (`400 Validation error: "Exactly 5 exam
  centers must be provided..."`) — confirmed live 2026-07-18. Since a user may only want to watch
  1-4 centers, `build_search_organization_ids()` pads `config["organization_ids"]` out to 5 with
  other real center ids drawn at random from `word_centers.json`; their results are simply
  discarded afterwards by the `watch_organization_ids` filter, so which fillers land doesn't
  matter. This also means 5 is a hard ceiling, not just an API detail — `app.py`'s center picker
  enforces `MAX_CENTERS = 5` (`build_config()` rejects more server-side too) because anything past
  the 5th pick would never even be queried.
- `dashboard_server.py` — stdlib HTTP server, binds `127.0.0.1:8787`, serves `status.json` state.
- `pull_session_cookies.py` — pulls session cookies from a running Chrome via remote-debugging
  port; writes them into `session.json`. Manual: you launch Chrome and log in first.
- `auto_refresh_session.py` — launches Chrome (or, as a fallback via `CHROME_CANDIDATES`/
  `EDGE_WIN_PATHS` in `find_chrome()`, Edge — preinstalled on Windows, unlike Chrome) itself
  (dedicated throwaway profile) at `info-kierowca.pl/login`, auto-clicks through the gov.pl →
  "Aplikacja mObywatel" chooser via an injected DOM-mutation-observer (see
  `AUTO_CLICK_TARGETS`/`AUTO_CLICK_OBSERVER_JS` — text-based, will break if the site's login UI
  text/labels change), then waits **indefinitely** for you to scan the QR and captures cookies the
  moment they appear. Auto-triggered by `notifier.py` on `auth_expired` (see
  `trigger_auto_refresh()`); guarded by a lock file at
  `~/.local/state/info-kierowca-notifier/auto-refresh.lock` so it won't relaunch while one's
  already in flight. Disable via `auto_refresh_chrome: false` in `config.json`.
- `cdp_client.py` — shared Chrome DevTools Protocol helpers used by `pull_session_cookies.py`,
  `auto_refresh_session.py`, and `open_logged_in_browser.py` (cookie reads *and* writes via
  `Storage.getCookies`/`setCookies`, JS eval in the page, navigation, and registering a script to
  run on every future document via `Page.addScriptToEvaluateOnNewDocument`).
- `open_logged_in_browser.py` — launches Chrome in its own dedicated profile (port `9555`, distinct
  from `auto_refresh_session.py`'s and from a regular browsing profile) and injects the cookies
  already saved in `session.json` via `cdp_client.set_cookies()` before navigating to `/cases`, so
  it opens already authenticated instead of at a login screen. `set_cookies()` deliberately sets
  `httpOnly: False` — confirmed live that the site's own frontend reads the session cookies via
  `document.cookie` to decide its logged-in UI state (it doesn't call `/jwt/refresh` on page load),
  so an httpOnly copy is sent correctly on requests but invisible to the site's own JS, which then
  renders as logged out. Runnable by hand, and auto-triggered by `notifier.py` on a matching urgent
  slot hit (see `trigger_open_browser()`, called right alongside the ntfy push in `run_check()`) —
  skipped if something's already listening on port `9555` so a slot that keeps reappearing under a
  new signature doesn't pile up duplicate Chrome windows. Disable via `auto_open_browser: false` in
  `config.json`. Also pre-sets a `CookieScriptConsent` cookie (`consent_cookie()`) shaped like what
  the site's real cookie-consent banner writes on accept/reject, so that banner never renders either
  — defaults to "necessary only", matching this project's minimal-footprint stance. After landing on
  `/cases`, auto-clicks two buttons in sequence via the shared `wait_and_click()` poller: the
  "Zmień termin" (change date) button on your active booking, then — once that opens the "Zmiana
  terminu rezerwacji egzaminu" confirm-or-cancel modal — its own "Zmień termin rezerwacji" confirm
  button. Both text matches are deliberately narrow (exact-ish match against just button/link/
  `role=button` elements, not the login flow's fuzzy multi-target chooser) since the list page also
  has an "Anuluj" (cancel the booking outright) button close by, and `CONFIRM_CHANGE_DATE_TEXT` is
  the longer, more specific phrase so it can't also match `CHANGE_DATE_TEXT`'s own button. Confirmed
  live this lands on the actual date-picker screen ("Wybierz datę początkową dla nowego terminu")
  with an empty range and a disabled "Przejdź do podsumowania" button — nothing about the booking
  has changed yet. Goes no further than that: picking the new date, the summary step, and any final
  confirm past that stay real clicks from you. No reservation/booking calls of any kind happen in
  this file. Reuses `find_chrome()` from `auto_refresh_session.py` rather than duplicating it.
  A `--no-auto-click` flag skips both clicks and just leaves the logged-in `/cases` tab open — used
  by `app.py`'s "Open browser" toolbar button (via `trigger_open_browser(auto_click=False)`) so a
  manual troubleshooting/browsing click doesn't also kick off the reschedule flow; the automatic
  urgent-slot-hit trigger keeps the default (`auto_click=True`) since that click-through is the
  entire point there.
- `app.py` — the composed, zero-setup entry point: runs `notifier.loop()` in a background thread,
  serves a first-run setup wizard + the dashboard + a `POST /shutdown` (the page's Stop button;
  hard-exits via `os._exit(0)`) from one stdlib HTTP server, and auto-opens the browser. The
  dashboard's Settings button hits `GET /settings`, which reuses `render_wizard()` — passed the
  existing `config.json` so the same form comes back prefilled — rather than a separate edit page;
  submitting posts to the same `/setup` endpoint that first-run setup uses, so `build_config()`
  stays the single place config validation lives. This is
  what the packaged release binaries (`pyinstaller.spec`, built `--windowed` — no console window)
  actually run. Shares `notifier.CONFIG_DIR`/`STATE_DIR` with the source/systemd path, so switching
  between "ran the binary" and "ran from source" never loses config/session/history. Detects an
  already-running instance on the dashboard port and just opens a browser tab at it instead of
  binding twice. Inside a frozen build, neither `trigger_auto_refresh()` nor
  `trigger_open_browser()` (both in `notifier.py`) can shell out to their respective `.py` files
  (they don't exist on disk, and `sys.executable` is the bundled binary itself) — each re-invokes
  the binary with its own hidden flag instead (`--internal-auto-refresh` / `--internal-open-browser`),
  which `app.py:run_internal_auto_refresh()` / `run_internal_open_browser()` dispatch straight to
  `auto_refresh_session.main()` / `open_logged_in_browser.main()`. These frozen-only paths can only
  be verified against an actual build, not `python app.py` — re-test both (delete `session.json`,
  confirm Chrome/Edge still opens for relogin; then, separately, confirm a slot hit still opens a
  logged-in tab) after any change here before tagging a release.
  The dashboard's toolbar (`TOOLBAR_HTML`, appended before `</body>`) has four buttons, in this
  order — Open browser, Pause, Settings, Quit (Quit last/rightmost since it's the most destructive):
  - **Pause/Resume** toggles `notifier.PAUSE_FILE` (`POST /pause` / `POST /resume`) — a plain flag
    file rather than a config field, checked at the top of `run_check()`, so it works identically
    whether checks are driven by `app.py`'s in-process loop or a systemd timer tick, and survives a
    settings resave. `run_check()` no longer overwrites `status.json`'s `outcome`/`message` with an
    artificial "paused" value when paused — it just skips the real work, leaving the last real
    result underneath. Instead, `/pause` and `/resume` (`AppHandler._set_paused()`) write
    `status.json`'s `paused` field directly and synchronously, and return it in the response body —
    so the toolbar button's label flips instantly on click instead of lagging behind up to
    `INTERVAL` seconds for the next tick to pick it up. `dashboard_server.py`'s frontend checks
    `data.paused` *before* `data.outcome` when choosing the headline, so Resume falls straight back
    to whatever the last real outcome was (e.g. "No slots in the next 31 days") rather than being
    stuck showing "Paused" until a fresh check runs.
  - **Open browser** (`POST /manual-login`, `_handle_manual_login()`, named for what it does rather
    than "Log in" since it covers two different outcomes) probes the session live via
    `check_session_valid()` (the same `REFRESH_URL` call `run_check()` makes) and routes to whichever
    flow actually applies — `trigger_open_browser(auto_click=False)` if the session's still good, or
    `trigger_auto_refresh(force=True)` if not — rather than guessing from file mtimes.
    `auto_click=False` is the important bit: this button is for opening the site or troubleshooting,
    not for the reschedule flow, so `open_logged_in_browser.py` is invoked with `--no-auto-click`
    and just lands on `/cases` logged in, without clicking through to "Zmień termin" — unlike the
    automatic urgent-slot-hit trigger (`notifier.py`'s call site keeps the default `auto_click=True`,
    unchanged), which still wants that click-through so the date-picker is ready the moment the push
    notification lands. This is also the fix/workaround for a reported bug: auto-login reliably
    fires when cookies expire *while the app is already running*, but was reported as not firing on
    a fresh launch with cookies that were *already* stale. Root cause (confirmed from
    `notifier.log`): `AUTO_REFRESH_LOCK` has no timeout (`auto_refresh_session.py` waits indefinitely
    for a QR scan) and is a detached process, so it outlives an `app.py` restart — a QR window left
    open and forgotten in a previous session (one observed live held the lock for ~10 hours)
    silently no-ops every later `trigger_auto_refresh()` call, including the very next launch, with
    zero visible indication why. The automatic path stays conservative on purpose (a background
    retry must never kill a window mid-scan) — `force` is what the manual button opts into instead:
    it kills whatever pid holds the lock and clears it before relaunching. `trigger_open_browser()`
    has no equivalent `force` — forcing there would mean a second Chrome fighting over the same fixed
    debug port (`9555`) an already-open one is using, which is fragile rather than useful, so
    "already_running" is treated as the desired outcome, not something to override.
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
`OnUnitActiveSec=1min`. The `OnActiveSec=10s` line was added specifically to fix a real incident:
starting the timer well after boot left `OnBootSec` already-elapsed (skipped) and
`OnUnitActiveSec` without a reference point (service had never run), so `systemctl --user start`
reported `active` while `Trigger` stayed `n/a` forever — it silently never fired. Don't remove
`OnActiveSec`. After any `start`/`restart`, verify with `systemctl --user list-timers
info-kierowca-notifier.timer` that `NEXT` is a real timestamp, not `-`/`n/a`.

### Known gotcha: auto-relogin (auto_refresh_session.py) needs a real GUI session

Triggered automatically by `notifier.py` on `auth_expired` via `systemd-run --user` (see
`trigger_auto_refresh()`), specifically so the launched Chrome + cookie-watcher survives after the
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
by the automatic path (see `trigger_auto_refresh()`'s docstring); the "Open browser" button's
`force=True` is still what clears that one.

### Known gotcha: dashboard port-in-use crash loop

`dashboard_server.py` binds `127.0.0.1:8787`. If a stale process (e.g. one started manually
outside systemd, or a previous crashed instance) is still holding the port,
`info-kierowca-dashboard.service` fails fast with `OSError: Address already in use`, retries a
few times, then systemd gives up (`start-limit-hit`). Find/kill whatever holds the port, then
`systemctl --user reset-failed info-kierowca-dashboard.service` before starting again — a plain
`start` after `start-limit-hit` is a no-op.

### Known gotcha: testing app.py/auto-refresh in a sandbox on a machine with real units installed

`trigger_auto_refresh()` prefers `systemd-run --user` (see its docstring) specifically so the
Chrome+QR process survives the triggering process exiting. That hand-off runs under the systemd
user manager's own environment, **not** the environment of the process that called
`systemd-run` — so a sandboxed `HOME` override (e.g. `HOME=/tmp/fake-home python app.py`) does
*not* propagate into the launched `auto_refresh_session.py`, which falls back to the real
`~/.config`/`~/.local/state` paths regardless. Confirmed live: a sandboxed `app.py` test run's
QR scan ended up refreshing the real production `session.json`, not the sandboxed one — harmless
(same account, just a fresh session), but surprising if you're not expecting it. To test the
auto-refresh trigger itself in real isolation, set `auto_refresh_chrome: false` in the sandboxed
`config.json` first.

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
- Don't tighten the poll interval below the current default without being asked; this is
  explicitly a design choice to stay a good citizen of an undocumented API.
- Session cookies / PKK number must never be sent anywhere except info-kierowca.pl itself.
