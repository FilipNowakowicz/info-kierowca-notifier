# info-kierowca-notifier

[![CI](https://github.com/FilipNowakowicz/info-kierowca-notifier/actions/workflows/ci.yml/badge.svg)](https://github.com/FilipNowakowicz/info-kierowca-notifier/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

A notification-only slot checker for [info-kierowca.pl](https://info-kierowca.pl), the Polish
driving exam booking portal. It polls for open exam slots and alerts you via a local dashboard and
a phone push when one appears, plus a desktop notification on errors (session expired, unexpected
API response, etc.) — it never books, reserves, or clicks anything on your behalf. You always do
the final booking yourself, in your own browser.

![Dashboard showing a found slot](docs/dashboard.png)

## Features

- Polls info-kierowca.pl's own API for real exam slot availability
- Phone push via [ntfy.sh](https://ntfy.sh) when a slot appears within your chosen window
- Local read-only dashboard at `http://127.0.0.1:8787`
- Strictly read-only — never books, reserves, or submits anything
- Zero *runtime* dependencies (Python standard library only) — works on Linux, macOS, and
  Windows. The packaged release binaries below are built with PyInstaller, a build-time-only
  tool — nothing new gets installed on your machine either way.

## How it works

It reads exactly two endpoints:

- `GET /bknd/auth/api/v1/jwt/refresh` — keeps your session alive
- `POST /bknd/exam/api/v1/Schedules/user/MultipleCentersExams` — reads slot availability

Both are the same endpoints info-kierowca.pl's own web app calls; this project just automates
checking them on a timer using your existing logged-in session cookies, instead of you refreshing
the page by hand.

## Responsible use

This does the minimum necessary to notify you, on purpose:

- Polls at a modest default interval (once a minute) — please don't tighten that or bolt on
  booking automation; that changes this from a notifier into something else entirely.
- Never sends your session cookies or PKK number anywhere except info-kierowca.pl itself. Only
  plain, human-readable notification text goes to ntfy.sh.
- Relies on an undocumented, reverse-engineered API that info-kierowca.pl could change or block
  at any time — use at your own risk and in line with the site's terms of service.

Requires Python 3.9+ and nothing else (standard library only).

## Download and run

The easiest way to use this: download a single file, double-click it, and follow the browser
tab that opens.

1. Grab the build for your OS from the [Releases page](../../releases) — no installer, no admin
   rights needed, nothing else gets installed on your machine.
2. Double-click it (or run it from a terminal). No console window appears — it just opens a
   browser tab.
3. First run shows a short setup form: your PKK number, exam center(s), exam type, and how you
   want to be notified. Submit it and a Chrome (or Edge) window opens for you to scan the
   mObywatel QR code and log in — same flow described in [Auto-relogin on session
   expiry](#auto-relogin-on-session-expiry) below.
4. From then on, that browser tab is your dashboard, with a **Stop** button at the top if you
   want to shut it down — closing the tab does *not* stop it, since there's no window to close;
   use Stop.

**First-run warnings:** the release binaries aren't code-signed (that needs a paid
developer account on both Windows and macOS), so your OS will warn you the first time:
- **Windows:** SmartScreen says "Windows protected your PC" → click "More info" → "Run anyway".
- **macOS:** Gatekeeper says it "cannot be opened because the developer cannot be verified" →
  right-click (or Control-click) the file → "Open" → confirm.

This only needs doing once per download. If you'd rather not run an unsigned binary at all, use
the from-source setup below instead — it's the exact same code, just run with `python app.py`.

Config and session files live in the same place either way (`~/.config/info-kierowca-notifier/`,
`~/.local/state/info-kierowca-notifier/`), so you can freely switch between the downloaded
binary and running from source without losing anything.

## Manual / from-source setup (for developers, or if you'd rather not run a downloaded binary)

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

3. Edit `config.json`:

   | Field | Meaning |
   |---|---|
   | `organization_ids` | WORD center IDs to query (defaults are Warsaw-area centers) |
   | `watch_organization_ids` | Subset of the above to actually alert on |
   | `category` | License category (5 = category B) |
   | `profile_number` | Your PKK profile number |
   | `exam_types` | Which exam(s) to watch: `["Theoretical"]`, `["Practice"]`, or both `["Theoretical", "Practice"]` |
   | `ntfy_topic` | Your [ntfy.sh](https://ntfy.sh) topic for phone push (pick a long random string — anyone who knows it can read your notifications) |
   | `push_below_days` | Only send a phone push (and turn the dashboard red) when the fastest slot is within this many days |
   | `push_before_date` *(optional)* | A fixed date (`"YYYY-MM-DD"`), exclusive — alert on any slot before this date instead of using a rolling day count. Takes priority over `push_below_days` when set. |
   | `auto_refresh_chrome` *(optional, default `true`)* | Whether an `auth_expired` outcome should automatically launch `auto_refresh_session.py` (see below). Set to `false` to fall back to a manual relogin. |

4. Run it — pick whichever fits your OS:

   **Option A — `app.py` (the same all-in-one wizard + dashboard + Stop button the downloaded
   binaries run, just from source):**
   ```
   python app.py
   ```
   Opens a browser tab automatically; skips steps 1-3 above entirely if `config.json` doesn't
   exist yet (it'll walk you through setup instead). No console window management needed here
   either — use the page's Stop button, not Ctrl+C.

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

**Troubleshooting the dashboard:** if `info-kierowca-dashboard.service` fails to start with
`OSError: [Errno 98] Address already in use`, something else (often a stale instance from a
previous run) is already bound to port 8787. Find and stop it, then
`systemctl --user reset-failed info-kierowca-dashboard.service` before starting again — systemd
stops retrying after a few rapid failures (`start-limit-hit`).

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

## Pausing / resuming

**Loop mode:** just stop the process (Ctrl+C) and rerun `python notifier.py --loop` whenever you
want to resume.

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

## Contributing

Issues and PRs welcome — this is a small, single-purpose tool, so please keep changes focused.

## License

MIT — see [LICENSE](LICENSE).
