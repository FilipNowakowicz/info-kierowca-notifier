# Advanced / from-source setup

This covers running info-kierowca-notifier from source instead of the downloaded binaries
described in the main [README](../README.md) — for Linux systemd users, developers, or anyone
who'd rather not run a downloaded binary. Requires Python 3.9+ and nothing else — zero *runtime*
dependencies (the release binaries are built with PyInstaller, a build-time-only tool, so this
claim still holds either way).

## Setup

1. Copy the example config files into `~/.config/info-kierowca-notifier/` (this works the same
   way on Windows, macOS and Linux — Python resolves `~` to your user profile folder either way).

   **Linux / macOS:**
   ```
   mkdir -p ~/.config/info-kierowca-notifier
   cp config.example.json ~/.config/info-kierowca-notifier/config.json
   cp session.example.json ~/.config/info-kierowca-notifier/session.json
   chmod 600 ~/.config/info-kierowca-notifier/config.json ~/.config/info-kierowca-notifier/session.json
   ```

   **Windows (PowerShell):**
   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.config\info-kierowca-notifier" | Out-Null
   Copy-Item config.example.json "$HOME\.config\info-kierowca-notifier\config.json"
   Copy-Item session.example.json "$HOME\.config\info-kierowca-notifier\session.json"
   ```
   (no `chmod` equivalent needed — the folder is already private to your Windows user account)

2. Get your session cookies into `session.json`. The notifier refreshes these itself on every run,
   so you only need to do this once — and again if the session is ever invalidated (e.g. by
   logging in fresh elsewhere).

   **Option A — `auto_refresh_session.py` (Chrome/Chromium, hands-off):** run it once to seed
   `session.json`, and from then on it also fires automatically whenever the notifier hits an
   `auth_expired` outcome (session cookie expiry, or an HTTP 500 — see [Auto-relogin on session
   expiry](#auto-relogin-on-session-expiry) below):
   ```
   python auto_refresh_session.py
   ```
   It launches Chrome in its own throwaway profile (your regular Chrome windows stay open), clicks
   through the gov.pl → "Aplikacja mObywatel" login chooser on its own, then waits — indefinitely,
   no timeout — for you to scan the mObywatel QR code in the app. Once you do, it captures the
   resulting `__Secure-PUDOJT` / `__Secure-PUDOJTMD` cookies and writes `session.json` for you.
   Nothing is sent anywhere but info-kierowca.pl/gov.pl and your own machine.

   **Option B — `pull_session_cookies.py` (Chrome/Chromium, manual):** quit Chrome completely,
   relaunch it with its remote-debugging port open, log in to info-kierowca.pl, then run the
   script:
   ```
   google-chrome --remote-debugging-port=9222   # macOS: .../Google Chrome.app/Contents/MacOS/Google Chrome
   python pull_session_cookies.py
   ```
   It talks to Chrome over that debug port on `127.0.0.1` only, pulls the `__Secure-PUDOJT` and
   `__Secure-PUDOJTMD` cookies for info-kierowca.pl, and writes them straight to `session.json`.
   Nothing is sent anywhere else. Use `--port` if you started Chrome on a different port, and
   `--all` to dump every cookie for the domain instead of just the two required ones. See the
   script's docstring for the Windows launch command and a security note about the debug port
   (it grants full control of the browser, so don't expose it beyond localhost).

   **Option C — DevTools (manual, any browser):** log in to info-kierowca.pl, open DevTools →
   Application/Storage → Cookies, and copy the `__Secure-PUDOJT` and `__Secure-PUDOJTMD` values
   into `session.json` by hand.

3. Edit `config.json` (or, once it's running, use the **Settings** button on the dashboard — same
   form, prefilled with your current values, saves straight back to `config.json`):

   | Field | Meaning |
   |---|---|
   | `organization_ids` | WORD center IDs to watch (defaults are Warsaw-area centers) |
   | `watch_organization_ids` | Which of the queried centers actually produce hits. The setup wizard keeps this identical to `organization_ids`; only hand-edit it apart if you want to query centers whose results you then ignore. |
   | `category` | License category (5 = category B) |
   | `profile_number` | Your PKK profile number |
   | `exam_types` | Which exam(s) to watch: `["Theoretical"]`, `["Practice"]`, or both `["Theoretical", "Practice"]` |
   | `ntfy_topic` | Your [ntfy.sh](https://ntfy.sh) topic for phone push (pick a long random string — anyone who knows it can read your notifications) |
   | `current_slot_date` | Date (`"YYYY-MM-DD"`) of your current booked slot. A found slot on or before this date counts as *urgent* (turns the dashboard red, and — when `phone_alerts` is on — sends a phone push), inclusive — an earlier date, or the same date at a different time. |
   | `phone_alerts` *(optional, default `true`)* | Whether an urgent slot sends a phone push at all. Set to `false` to just watch the dashboard silently; the dashboard, urgency colouring, and `auto_open_browser` still work. |
   | `auto_refresh_chrome` *(optional, default `true`)* | Whether an `auth_expired` outcome should automatically launch `auto_refresh_session.py` (see below). Set to `false` to fall back to a manual relogin. |
   | `auto_open_browser` *(optional, default `true`)* | Whether a matching urgent slot should also launch `open_logged_in_browser.py` (see [Reschedule assist](#reschedule-assist) below). Set to `false` to disable. |

   Slots are only ever considered within 31 days out — that's a hard line on info-kierowca.pl
   itself, not something this project can (or needs to) make configurable.

4. Run it — pick whichever fits your OS:

   **Option A — `app.py` (the same all-in-one wizard + dashboard + Stop button the downloaded
   binaries run, just from source):**
   ```
   python app.py
   ```
   Opens a browser tab automatically; if `config.json` doesn't exist yet it replaces steps 1-3
   above with an in-browser setup wizard (using real WORD center names — see `word_centers.json` /
   `fetch_word_centers.py`). No console window management needed here either — use the page's Stop
   button, not Ctrl+C.

   **Option B — built-in loop (works on Windows, macOS, Linux):**
   ```
   python notifier.py --loop
   ```
   Leave this running in a terminal, or set your OS to start it in the background for you (e.g. a
   Windows Task Scheduler task running at log-on, or a macOS `launchd` agent). It checks every 60
   seconds by default; use `--interval` to change that.

   **Option C — systemd user units (Linux only, recommended if available: survives reboots and
   auto-restarts on failure):**
   ```
   cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now info-kierowca-notifier.timer
   ```
   The units run `python3` via `/usr/bin/env` and assume the repo is cloned to `~/infokierowca`. If
   you cloned it elsewhere, edit the path at the end of each `ExecStart=` line in the two
   `.service` files first. If `env` can't find `python3` (some minimal setups), add its directory
   to the `Environment=PATH=` line in those files.

5. If you used Option A, the dashboard is already running — skip this step. Otherwise, start it
   separately (same command on every OS — plain Python, no extra setup):
   ```
   python dashboard_server.py
   ```
   Then open `http://127.0.0.1:8787` for a local read-only view of the current status and history.
   It's bound to localhost only. On Linux you can instead run this as the included
   `info-kierowca-dashboard.service` unit.

6. Install the [ntfy app](https://ntfy.sh/app) on your phone and subscribe to your `ntfy_topic` to
   get pushes.

**Note:** desktop error notifications use `notify-send` and only work on Linux. On Windows/macOS
you won't get a popup on errors — check the dashboard or the log file instead, at
`~/.local/state/info-kierowca-notifier/notifier.log` (not in the repo directory).

## Auto-relogin on session expiry

By default (`auto_refresh_chrome: true`), whenever a check comes back `auth_expired` — a 401,
403, 404 on the refresh call, or a 401/403/500 on the search call, all of which have in practice
turned out to be the same underlying cookie-expiry problem — `notifier.py` launches
`auto_refresh_session.py` in the background. It opens Chrome to the login page in its own profile,
clicks through the gov.pl → "Aplikacja mObywatel" chooser on its own, and sends you a single
(non-urgent) push + desktop notification to scan the QR in the app. It waits indefinitely — there's
no timeout to race, since a relogin has to happen eventually anyway — and the moment you scan it,
captures the new cookies and writes `session.json` automatically. A lock file
(`~/.local/state/info-kierowca-notifier/auto-refresh.lock`) stops it firing again on every
subsequent 60s tick while a relogin is already in flight; it's cleaned up when that run finishes
(delete it by hand if a run ever crashes without cleaning up).

**Only works if a real desktop/GUI session is available** — Chrome needs somewhere to render the
QR code. If `info-kierowca-notifier.service` runs under systemd on a headless box or before you've
logged into a desktop session, disable it (`auto_refresh_chrome: false` in `config.json`) and use
`auto_refresh_session.py` or `pull_session_cookies.py` by hand instead.

**systemd note:** the launch is handed off via `systemd-run --user` specifically so the Chrome +
watcher process survives after the triggering oneshot `info-kierowca-notifier.service` run exits
(a plain child process would otherwise be killed along with it — see `KillMode=control-group`, the
systemd default). `systemd-run --user` needs the same graphical-session environment
(`DISPLAY`/`WAYLAND_DISPLAY`) imported into your systemd user manager that any GUI app launched
from a `systemd --user` unit would need; most desktop environments do this automatically at login.
If Chrome never appears, check `journalctl --user -u info-kierowca-auto-refresh -n 20 --no-pager`.

## Reschedule assist

If you already have a paid booking and just want to move it to a fresher date, `notifier.py` can
open a browser for you the moment a matching urgent slot appears (same gating as the phone push —
see `current_slot_date`), pre-authenticated with your saved session, and click
through the first two steps of changing that booking's date:

```
python open_logged_in_browser.py   # or let a slot hit trigger it automatically
```

It launches Chrome in its own dedicated profile (a separate `--remote-debugging-port` from
`auto_refresh_session.py`'s, so the two never collide), injects your `session.json` cookies so it
opens straight into `/cases` already logged in, and suppresses the cookie-consent banner by
pre-setting the same cookie the real banner would write on "necessary only". It then clicks
"Zmień termin" on your booking, then "Zmień termin rezerwacji" in the confirm modal that opens —
and stops there, on the date-range picker, with nothing about the booking changed yet. Picking the
actual new date, the summary step, and any final confirm past that are always real clicks from
you; no code in this project selects a date or submits a reservation change on its own.

Skipped automatically if something's already listening on its debug port (`9555`), so a slot that
keeps reappearing under a new signature won't pile up duplicate Chrome windows. Disable with
`auto_open_browser: false` in `config.json`.

**Requires an existing confirmed booking.** The "Zmień termin" button only appears on a booking
that's already `Potwierdzony` (confirmed) — if you don't have one, there's nothing on `/cases` for
this to click, and it'll just report that it couldn't find the button. This flow moves the date on
a booking you already hold; it doesn't create one.

## Pausing / resuming

**Loop mode / `app.py`:** just stop the process (Ctrl+C, or the dashboard's Stop button for
`app.py`) and rerun it whenever you want to resume.

**systemd mode:**
```
systemctl --user stop info-kierowca-notifier.timer   # pause
systemctl --user start info-kierowca-notifier.timer  # resume (refresh session.json first if it's been a while)
```
After `start`, confirm it actually scheduled a next run:
```
systemctl --user list-timers info-kierowca-notifier.timer
```
`NEXT`/`Trigger` should show a real upcoming time. If it shows `n/a`, the unit file you have
installed predates the `OnActiveSec=10s` fix below — reinstall it (`cp systemd/*.timer
~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user restart
info-kierowca-notifier.timer`).

**Why `OnActiveSec` matters:** the timer also uses `OnBootSec=1min` + `OnUnitActiveSec=1min` for
its normal every-60s cadence. Those alone are not enough to resume reliably: `OnBootSec` is
relative to *boot time*, so if you `start` the timer more than a minute after boot (the usual
case), that trigger is already in the past and is skipped; `OnUnitActiveSec` has no reference
point until the service has run at least once under this timer activation. Net effect: the timer
reports `active` but never actually fires. `OnActiveSec=10s` is relative to when the *timer unit
itself* starts, so every `start`/`restart` is guaranteed a first run ~10s later regardless of
uptime, which then gives `OnUnitActiveSec` its reference point for the regular 60s cadence.

## Troubleshooting

**Dashboard port already in use:** if `info-kierowca-dashboard.service` fails to start with
`OSError: [Errno 98] Address already in use`, something else (often a stale instance from a
previous run) is already bound to port 8787. Find and stop it, then
`systemctl --user reset-failed info-kierowca-dashboard.service` before starting again — systemd
stops retrying after a few rapid failures (`start-limit-hit`).
