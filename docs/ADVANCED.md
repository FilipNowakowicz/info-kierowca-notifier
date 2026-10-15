# Advanced / from-source setup

This covers running info-kierowca-notifier from source instead of the downloaded binaries
described in the main [README](../README.md) — for Linux systemd users, developers, or anyone
who'd rather not run a downloaded binary. Requires Python 3.9+ and the dependencies declared in
`pyproject.toml` (HTTPS trust, secure credential storage, and Windows timezone data where needed).
The release binaries package those dependencies, so they still need no Python installation or
extra setup.

## Setup

1. Install the verified HTTPS trust dependencies:

   ```
   uv sync
   ```

   On Python 3.10+, `truststore` uses the native Windows/macOS/Linux trust store. Python 3.9
   securely uses the bundled `certifi` CA set instead.

2. Copy the example config files into `~/.config/info-kierowca-notifier/` (this works the same
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

3. Get your session cookies into `session.json`. The recommended route is to run `python -m info_kierowca_notifier` in
   step 5 and use its first-run **Profil Zaufany** setup. It stores the password in the operating
   system credential vault, pairs the dedicated Chrome profile with Google Messages Web, performs
   the login, and writes `session.json` automatically. Do not put a Profil Zaufany password in
   `config.json`, `session.json`, an environment variable, or a command line.

   **Option A — app wizard (recommended, automatic Profil Zaufany):**
   ```
   uv run python -m info_kierowca_notifier
   ```
   Select Profil Zaufany, enter the username and password, and pair Google Messages Web before
   completing login. Pairing is required so the app can read the one-time SMS code. The password is saved only through the OS
   credential backend. If no supported secure backend is available, setup stops without falling
   back to plaintext storage.

   **Option B — `src/info_kierowca_notifier/auth/session.py` (uses the method already saved by the app):**
   ```
   uv run python -m info_kierowca_notifier.auth.session
   ```
   It launches the app's dedicated Chrome profile and reads `login_method` from `config.json`.
   For `profil_zaufany`, it retrieves the saved password from the OS vault and reads the fresh
   PZePUAP code from the paired Google Messages Web tab. For `mobywatel`, it opens the QR flow and
   waits indefinitely for a scan. Either route captures the resulting session cookies and writes
   `session.json`.

   **Option C — `tools/pull_session_cookies.py` (Chrome/Chromium, manual):** quit Chrome completely,
   relaunch it with its remote-debugging port open, log in to info-kierowca.pl, then run the
   script:
   ```
   google-chrome --remote-debugging-port=9222   # macOS: .../Google Chrome.app/Contents/MacOS/Google Chrome
   uv run python tools/pull_session_cookies.py
   ```
   It talks to Chrome over that debug port on `127.0.0.1` only, pulls the `__Secure-PUDOJT`,
   `__Secure-PUDOJTMD` and `__Host-Http-PUDO-DeviceId` cookies for info-kierowca.pl, and writes them
   straight to `session.json`.
   Nothing is sent anywhere else. Use `--port` if you started Chrome on a different port, and
   `--all` to dump every cookie for the domain instead of just the three required ones. See the
   script's docstring for the Windows launch command and a security note about the debug port
   (it grants full control of the browser, so don't expose it beyond localhost).

   **Option D — DevTools (manual, any browser):** log in to info-kierowca.pl, open DevTools →
   Application/Storage → Cookies, and copy the `__Secure-PUDOJT`, `__Secure-PUDOJTMD` and
   `__Host-Http-PUDO-DeviceId` values into `session.json` by hand. All three are required — without
   the device cookie the site rejects the session.

4. Edit `config.json` (or, once it's running, use the **Settings** button on the dashboard — same
   form, prefilled with your current values, saves straight back to `config.json`):

   | Field | Meaning |
   |---|---|
   | `login_method` *(managed by the app; `"profil_zaufany"` or `"mobywatel"`)* | Authentication used for initial login and session recovery. Profil Zaufany supports unattended renewal; mObywatel waits for a QR scan. Configure this through the app so credentials are handled safely. |
   | `pz_username` / `pz_credential_present` *(managed by the app)* | Identifies the Profil Zaufany credential stored in the OS vault. The marker records that a secure credential was saved; neither field contains the password. Do not fabricate or copy the marker between machines. |
   | `search_mode` *(optional, default `"multi"`)* | `"multi"` searches up to 5 centers at once; `"single"` searches exactly one center through the site's single-center search, which reaches further ahead. Set in the setup wizard. |
   | `organization_ids` | WORD center IDs to watch: up to 5 in multi mode, exactly 1 in single mode. The multi-center endpoint insists on exactly 5, so fewer picks get padded with unrelated centers whose results are then discarded. |
   | `category` | License category (5 = category B) |
   | `profile_number` | Your PKK profile number |
   | `exam_types` | Which exam(s) to watch: `["Theoretical"]`, `["Practice"]`, or both `["Theoretical", "Practice"]` |
   | `ntfy_topic` | Your [ntfy.sh](https://ntfy.sh) topic for phone push (pick a long random string — anyone who knows it can read your notifications) |
   | `current_slot_date` | Date (`"YYYY-MM-DD"`) of your current booked slot. A found slot beats this (turns the dashboard red, and — when `phone_alerts` is on — sends a phone push) only if it's on a strictly earlier date; a different time on the same date doesn't count on its own (see `notifier.is_urgent()`). |
   | `search_start_date` *(optional)* | Earliest acceptable available exam date (`"YYYY-MM-DD"`). Leave it blank (or omit it in an older config) to search from the earliest day the site allows (2 days ahead). Slots before it are ignored even if the API returns them. It must be 2 days to 6 months ahead and before `current_slot_date`. |
   | `poll_interval_seconds` *(optional, default `420`)* | Seconds between checks, clamped to 15–1800. info-kierowca.pl allows about 10 searches per hour; faster settings use them up early and the app then waits for the limit to reset rather than going over it. Set with the dashboard Settings slider, or by hand. |
   | `earliest_slot_hour` / `latest_slot_hour` *(optional, default `0` / `24`)* | Preferred time-of-day window in whole hours, `latest` exclusive. A slot outside `[earliest, latest)` is ignored entirely — no push, no dashboard entry, nothing in history. Defaults span the whole day. Set with the dashboard's dual-handle time slider. |
   | `phone_alerts` *(optional, default `true`)* | Whether a slot that beats your booked date sends a phone push at all. Set to `false` to just watch the dashboard silently; the dashboard's red/gray colouring and `auto_open_browser` still work. |
   | `phone_alerts_relogin` *(optional, default `true`)* | Whether login recovery may send a phone push. With mObywatel this asks for a QR scan; with Profil Zaufany it reports an automatic-login failure that needs attention. Independent of slot-alert pushes. |
   | `auto_refresh_chrome` *(optional, app-managed as `true`)* | Whether an `auth_expired` outcome should automatically launch `src/info_kierowca_notifier/auth/session.py` (see below). The app keeps this enabled: mObywatel reopens the QR screen, while Profil Zaufany logs in automatically. Advanced headless setups may set it to `false` directly to require manual relogin. |
   | `headless_pz_login` *(optional, default `false`)* | Run automatic Profil Zaufany relogin in headless Chrome so no window appears. Google Messages Web must first be paired in the dedicated profile. This does not apply to the mObywatel QR flow. |
   | `auto_open_browser` *(optional, default `true`)* | Whether a found slot that beats your booked date should also launch `src/info_kierowca_notifier/booking/reservation.py` (see [Reservation assist](#reservation-assist) below). Set to `false` to disable. |

5. Run it — pick whichever fits your OS:

   **Option A — `python -m info_kierowca_notifier` (the same all-in-one wizard + dashboard + Quit button the downloaded
   binaries run, just from source):**
   ```
   uv run python -m info_kierowca_notifier
   ```
   Opens a browser tab automatically; if `config.json` doesn't exist yet it replaces steps 1-3
   above with an in-browser setup wizard (using real WORD center names — see `word_centers.json` /
   `tools/fetch_word_centers.py` — and license-category names from `categories.json` /
   `tools/fetch_categories.py`). No console window management needed here either — use the page's Quit
   button, not Ctrl+C.

   **Option B — built-in loop (works on Windows, macOS, Linux):**
   ```
   uv run python -m info_kierowca_notifier.notifier --loop
   ```
   Leave this running in a terminal, or set your OS to start it in the background for you (e.g. a
   Windows Task Scheduler task running at log-on, or a macOS `launchd` agent). It checks on
   `config.json`'s `poll_interval_seconds` (default 420s, clamped to 15–1800), re-read fresh each
   cycle; `--interval` only sets the fallback used before that key exists.

   **Option C — systemd user units (Linux only, recommended if available: survives reboots and
   auto-restarts on failure):**
   ```
   cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now info-kierowca-notifier.timer
   ```
   The units run the locked uv environment and assume the repo is cloned to `~/infokierowca`.
   Run `uv sync` first. If you cloned it elsewhere, edit `WorkingDirectory=` in each `.service`
   file before copying it. The checked-in units search `%h/.local/bin`, the usual system binary
   directories, and the common Nix/NixOS profile locations `%h/.nix-profile/bin`,
   `/etc/profiles/per-user/%u/bin`, and `/run/current-system/sw/bin`. If `uv` is installed
   somewhere else, add that directory to the unit's `Environment=PATH=` line; do not hardcode a
   `/nix/store/...` path because store paths change across upgrades.

6. If you used Option A, the dashboard is already running — skip this step. Otherwise, start it
   separately (same command on every OS — plain Python, no extra setup):
   ```
   uv run python -m info_kierowca_notifier.web.server
   ```
   Then open `http://127.0.0.1:8787` for a local read-only view of the current status and history.
   It's bound to localhost only. On Linux you can instead run this as the included
   `info-kierowca-dashboard.service` unit.

7. Install the [ntfy app](https://ntfy.sh/app) on your phone and subscribe to your `ntfy_topic` to
   get pushes.

**Note:** desktop error notifications use `notify-send` on Linux and `osascript` on macOS (the
macOS path is untested). On Windows you won't get a popup on errors — check the dashboard or the log file instead, at
`~/.local/state/info-kierowca-notifier/notifier.log` (not in the repo directory).

**Being offline is not an error.** If a check can't reach info-kierowca.pl at all (no Wi-Fi, laptop
asleep, DNS down), the dashboard shows a plain "Offline" and the next check just retries — no
desktop notification, no red error state. Only responses that actually came back from the server
are treated as problems worth interrupting you about.

### HTTPS trust troubleshooting

HTTPS certificate and hostname verification is always required. For an unusual Linux layout or a
corporate CA, first use the standard `SSL_CERT_FILE` / `SSL_CERT_DIR` environment variables. An
application-specific `INFO_KIEROWCA_CA_BUNDLE` file is also supported and takes precedence. A bad
explicit bundle fails closed; check `notifier.log` for the selected trust backend and safe
diagnostics (platform, OpenSSL version, and which override variables were set). Never work around
such an error by disabling certificate verification.

## Auto-relogin on session expiry

**Expect a full authentication roughly every hour.** The access-token cookie
(`__Secure-PUDOJT`) is silently refreshed on every poll via `/jwt/refresh`, but that refresh only
extends the token — it doesn't touch a separate, absolute session ceiling of about 3600 seconds
from the last full login. Once that ceiling passes, the next check comes back `auth_expired`
regardless of how healthy the refreshes were. Profil Zaufany can complete the new login
automatically; mObywatel still requires a new QR scan. For the manual mObywatel path, the app module's
dashboard shows an estimated-expiry countdown and a reset control. The estimate comes from
`session.json`'s `captured_at`, stamped on every fresh login.

By default (`auto_refresh_chrome: true`), Profil Zaufany starts a new automatic login when the
estimated session lifetime has five minutes remaining. This gives quick failures time for the
normal 1-minute and 2-minute retry cooldowns before the current session expires. The notifier
also retains the expiry fallback: whenever a check comes back `auth_expired` — a 401, 403, 404,
or 500 on the refresh call, or a 401/403/500 on the search call, all of which have in practice
turned out to be the same underlying cookie-expiry problem — it launches
`src/info_kierowca_notifier/auth/session.py` in the background. It opens Chrome to the login page in the app's
dedicated profile and follows the `login_method` saved by the setup wizard:

- **Profil Zaufany (recommended):** retrieves the password from the OS credential vault, navigates
  the government login flow, reads a newly received eight-digit PZePUAP code from the paired
  Google Messages Web tab, submits it, and captures the new cookies. No user click or QR scan is
  normally required.
- **mObywatel:** clicks through to the QR page, sends a push and desktop notification asking for a
  scan, waits indefinitely, and captures the cookies after the scan.

In both cases the new cookies are written to `session.json`. A lock file
(`~/.local/state/info-kierowca-notifier/auto-refresh.lock`) stops it firing again on every
subsequent check while a relogin is already in flight; it's cleaned up when that run finishes
(delete it by hand if a run ever crashes without cleaning up).

If a relogin fails (for example Chrome closes, credentials are rejected, no fresh PZePUAP message
arrives, or Google Messages Web is no longer paired), the
notifier records a small, non-sensitive retry state in
`~/.local/state/info-kierowca-notifier/relogin-backoff.json`. Automatic retries wait 1 minute,
then double after repeated failures up to 1 hour, including across restarts. A successful login
clears that state immediately. Profil Zaufany failures also produce desktop and phone alerts so
you can repair the pairing or credentials. The dashboard's deliberate relogin buttons bypass
the cooldown; they do not disable certificate checks or discard session data. A normal retry
never closes an active login. If that browser window has genuinely been forgotten, clicking the
session-refresh control again offers a separate confirmed restart. The helper then receives a
per-run cooperative request, closes its own Chrome/profile resources, and only after shutdown
launches the replacement. If shutdown cannot be confirmed, no second browser is opened. A live
PID-only lock left by an older app version cannot be restarted this way; close that old Chrome
window normally and retry.

By default Chrome remains visible. Enable **Run automatic Profil Zaufany login in the background**
in Settings (`headless_pz_login: true`) to run that flow without opening a window. Pair Google
Messages Web in the dedicated profile first; pairing itself remains a visible, interactive step.
mObywatel still requires a visible QR code. Headless Profil Zaufany can also run without a desktop
session, although the OS credential vault and user service must still be available.

For Profil Zaufany, keep the dedicated Chrome profile paired with Google Messages Web and leave
the PZePUAP conversation available. Use **Pair Google Messages Web** in Settings after a
phone/browser reset or whenever automatic login reports that the provider is unavailable.
Government and Google page markup is external, so observe at least one complete live login before
relying on it unattended.

**systemd note:** the launch is handed off via `systemd-run --user` specifically so the Chrome +
watcher process survives after the triggering oneshot `info-kierowca-notifier.service` run exits
(a plain child process would otherwise be killed along with it — see `KillMode=control-group`, the
systemd default). `systemd-run --user` needs the same graphical-session environment
(`DISPLAY`/`WAYLAND_DISPLAY`) imported into your systemd user manager that any GUI app launched
from a `systemd --user` unit would need; most desktop environments do this automatically at login.
Headless Profil Zaufany relogin does not require those display variables.
If Chrome never appears, check `journalctl --user -u info-kierowca-auto-refresh -n 20 --no-pager`.

## Reservation assist

When a slot beats the booked date configured in the app, `auto_open_browser` can open a dedicated
Chrome window already authenticated with the captured info-kierowca.pl session. The reservation
helper does not recreate the original search mode. Whether the winning slot came from single- or
multi-center search, it uses the winning result's WORD and continues through the normal
`/reservation` flow for that one center.

The helper selects PKK, the configured category and the WORD from the winning search result, then
loads that centre's available-exams step. It deliberately leaves the portal's own start-date field
alone: discovery has already found the winning slot, so the reservation helper only needs the
concrete WORD and target slot. The portal itself performs `OneCenterExam`; the helper does not inject
a fabricated API response.
With only `auto_open_browser` enabled, automation stops on that available-exams screen. The user
chooses and confirms the slot manually.

Skipped automatically if something is already listening on the helper's debug port (`9555`), so a
repeated hit cannot pile up duplicate Chrome windows. Disable the feature with
`auto_open_browser: false` in `config.json`.

### Auto-selecting the matching slot and reaching the preliminary summary

Turn on "Auto-select the matching slot" in Settings → Automation (or set
`"auto_select_slot": true`). After the available-exams step is loaded, the helper selects the exact
matching exam date/time from the winning search result, confirms the "Potwierdź wybrany egzamin"
modal, advances from the term step, keeps Polish as the exam language, and advances once more.

It then stops on **"Podsumowanie wstępnej rezerwacji"**. Nothing has been submitted at that point.
The final "Zapisz i przejdź dalej" button on that summary is deliberately left untouched.

### Auto-confirming the reservation

Turn on "Auto-confirm the reservation" in Settings → Automation only together with auto-select
(or set both `"auto_select_slot": true` and `"auto_confirm_reschedule": true`). The configuration
key keeps its historical name for backward compatibility.

Before the final action, the helper verifies that the preliminary summary matches the intended
date, time, exam type and WORD. Only then may it click the summary's final
"Zapisz i przejdź dalej" button. That click is the high-stakes boundary: the live portal issues
`POST /bknd/exam/api/v1/Reservations/create`. The helper intentionally lets the portal build and
send that request rather than constructing it itself.

After the click, Chrome remains open for any response, further confirmation or payment required by
the portal. Closing the browser after this click does not undo a reservation that the server has
already created. For that reason auto-confirm remains experimental and off by default. Test the
auto-select-only path first and verify that it consistently reaches the correct preliminary summary.

## Pausing / resuming

**`python -m info_kierowca_notifier`:** click the headline on the dashboard — it toggles pause/resume (hover it and a
pause/play icon appears over the text; Enter or Space works too when it's focused). Checks stop
until you click again; the last real result stays on screen underneath. This writes a flag file
(`~/.local/state/info-kierowca-notifier/paused`) rather than a config setting, so it survives
saving settings and applies to the systemd path too. The **Quit** button in the top toolbar is a
different thing — it exits the app entirely.

**Loop mode (`python -m info_kierowca_notifier.notifier --loop`):** Ctrl+C and rerun, or `touch`/`rm` the same `paused` flag
file, which `run_check()` honours on every tick.

**systemd mode:**
```
systemctl --user stop info-kierowca-notifier.timer   # pause
systemctl --user start info-kierowca-notifier.timer  # resume; configured login recovery handles an expired session
```
After `start`, confirm it actually scheduled a next run:
```
systemctl --user list-timers info-kierowca-notifier.timer
```
`NEXT`/`Trigger` should show a real upcoming time. If it shows `n/a`, the unit file you have
installed predates the `OnActiveSec=10s` fix below — reinstall it (`cp systemd/*.timer
~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user restart
info-kierowca-notifier.timer`).

**Why `OnActiveSec` matters:** the timer also uses `OnBootSec=1min` + `OnUnitActiveSec=7min` for
its normal cadence (7 minutes stays under the site's ~10 searches/hour limit). Those alone are not enough to resume reliably: `OnBootSec` is
relative to *boot time*, so if you `start` the timer more than a minute after boot (the usual
case), that trigger is already in the past and is skipped; `OnUnitActiveSec` has no reference
point until the service has run at least once under this timer activation. Net effect: the timer
reports `active` but never actually fires. `OnActiveSec=10s` is relative to when the *timer unit
itself* starts, so every `start`/`restart` is guaranteed a first run ~10s later regardless of
uptime, which then gives `OnUnitActiveSec` its reference point for the regular cadence.

## Troubleshooting

**Dashboard port already in use:** if `info-kierowca-dashboard.service` fails to start with
`OSError: [Errno 98] Address already in use`, something else (often a stale instance from a
previous run) is already bound to port 8787. Find and stop it, then
`systemctl --user reset-failed info-kierowca-dashboard.service` before starting again — systemd
stops retrying after a few rapid failures (`start-limit-hit`).
