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
   It talks to Chrome over that debug port on `127.0.0.1` only, pulls the `__Secure-PUDOJT` and
   `__Secure-PUDOJTMD` cookies for info-kierowca.pl, and writes them straight to `session.json`.
   Nothing is sent anywhere else. Use `--port` if you started Chrome on a different port, and
   `--all` to dump every cookie for the domain instead of just the two required ones. See the
   script's docstring for the Windows launch command and a security note about the debug port
   (it grants full control of the browser, so don't expose it beyond localhost).

   **Option D — DevTools (manual, any browser):** log in to info-kierowca.pl, open DevTools →
   Application/Storage → Cookies, and copy the `__Secure-PUDOJT` and `__Secure-PUDOJTMD` values
   into `session.json` by hand.

4. Edit `config.json` (or, once it's running, use the **Settings** button on the dashboard — same
   form, prefilled with your current values, saves straight back to `config.json`):

   | Field | Meaning |
   |---|---|
   | `login_method` *(managed by the app; `"profil_zaufany"` or `"mobywatel"`)* | Authentication used for initial login and session recovery. Profil Zaufany supports unattended renewal; mObywatel waits for a QR scan. Configure this through the app so credentials are handled safely. |
   | `pz_username` / `pz_credential_present` *(managed by the app)* | Identifies the Profil Zaufany credential stored in the OS vault. The marker records that a secure credential was saved; neither field contains the password. Do not fabricate or copy the marker between machines. |
   | `organization_ids` | WORD center IDs to watch, up to 5 (defaults are Warsaw-area centers). The search endpoint insists on exactly 5, so fewer picks get padded with unrelated centers whose results are then discarded. |
   | `category` | License category (5 = category B) |
   | `profile_number` | Your PKK profile number |
   | `exam_types` | Which exam(s) to watch: `["Theoretical"]`, `["Practice"]`, or both `["Theoretical", "Practice"]` |
   | `ntfy_topic` | Your [ntfy.sh](https://ntfy.sh) topic for phone push (pick a long random string — anyone who knows it can read your notifications) |
   | `current_slot_date` | Date (`"YYYY-MM-DD"`) of your current booked slot. A found slot beats this (turns the dashboard red, and — when `phone_alerts` is on — sends a phone push) only if it's on a strictly earlier date; a different time on the same date doesn't count on its own (see `notifier.is_urgent()`). |
   | `search_start_date` *(optional)* | Earliest acceptable available exam date (`"YYYY-MM-DD"`). Leave it blank (or omit it in an older config) to search from today. This lower bound is separate from `current_slot_date`; slots before it are ignored even if the API returns them. It must fall within the site's 31-day search horizon. |
   | `poll_interval_seconds` *(optional, default `60`)* | Seconds between checks, clamped to 15–1800. Lower is more responsive but hits an undocumented API harder — 15s is a deliberate floor. Set with the dashboard Settings slider, or by hand. |
   | `earliest_slot_hour` / `latest_slot_hour` *(optional, default `0` / `24`)* | Preferred time-of-day window in whole hours, `latest` exclusive. A slot outside `[earliest, latest)` is ignored entirely — no push, no dashboard entry, nothing in history. Defaults span the whole day. Set with the dashboard's dual-handle time slider. |
   | `phone_alerts` *(optional, default `true`)* | Whether a slot that beats your booked date sends a phone push at all. Set to `false` to just watch the dashboard silently; the dashboard's red/gray colouring and `auto_open_browser` still work. |
   | `phone_alerts_relogin` *(optional, default `true`)* | Whether login recovery may send a phone push. With mObywatel this asks for a QR scan; with Profil Zaufany it reports an automatic-login failure that needs attention. Independent of slot-alert pushes. |
   | `auto_refresh_chrome` *(optional, app-managed as `true`)* | Whether an `auth_expired` outcome should automatically launch `src/info_kierowca_notifier/auth/session.py` (see below). The app keeps this enabled: mObywatel reopens the QR screen, while Profil Zaufany logs in automatically. Advanced headless setups may set it to `false` directly to require manual relogin. |
   | `headless_pz_login` *(optional, default `false`)* | Run automatic Profil Zaufany relogin in headless Chrome so no window appears. Google Messages Web must first be paired in the dedicated profile. This does not apply to the mObywatel QR flow. |
   | `auto_open_browser` *(optional, default `true`)* | Whether a found slot that beats your booked date should also launch `src/info_kierowca_notifier/booking/reschedule.py` (see [Reschedule assist](#reschedule-assist) below). Set to `false` to disable. |

   Slots are only ever considered within 31 days out — that's a hard line on info-kierowca.pl
   itself, not something this project can (or needs to) make configurable.

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
   `config.json`'s `poll_interval_seconds` (default 60s, clamped to 15–1800), re-read fresh each
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

**Note:** desktop error notifications use `notify-send` and only work on Linux. On Windows/macOS
you won't get a popup on errors — check the dashboard or the log file instead, at
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
subsequent 60s tick while a relogin is already in flight; it's cleaned up when that run finishes
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

## Reschedule assist

If you already have a paid booking and just want to move it to a fresher date, `src/info_kierowca_notifier/notifier.py` can
open a browser for you the moment a matching slot beats your booked date (same gating as the phone push —
see `current_slot_date`), pre-authenticated with your saved session, and click
through the first two steps of changing that booking's date:

```
uv run python -m info_kierowca_notifier.booking.reschedule   # or let a slot hit trigger it automatically
```

It launches Chrome in its own dedicated profile (a separate `--remote-debugging-port` from
`src/info_kierowca_notifier/auth/session.py`'s, so the two never collide), injects your `session.json` cookies so it
opens straight into `/cases` already logged in, and suppresses the cookie-consent banner by
pre-setting the same cookie the real banner would write on "necessary only". It then clicks
"Zmień termin" on your booking, then "Zmień termin rezerwacji" in the confirm modal that opens —
and, with the default settings, stops there on the date-range picker with nothing about the booking
changed yet. You can finish the change manually or enable the options below to select and confirm
the matching slot automatically.

Skipped automatically if something's already listening on its debug port (`9555`), so a slot that
keeps reappearing under a new signature won't pile up duplicate Chrome windows. Disable with
`auto_open_browser: false` in `config.json`.

**Requires an existing confirmed booking.** The "Zmień termin" button only appears on a booking
that's already `Potwierdzony` (confirmed) — if you don't have one, there's nothing on `/cases` for
this to click, and it'll just report that it couldn't find the button. The app reschedules the
booking you already hold; it cannot create a new one.

### Auto-selecting the matching slot and reaching the summary screen

Turn on "Auto-select the matching slot" in Settings → Automation (or add `"auto_select_slot": true`
to `config.json` by hand) to go further: after landing on the empty date-range picker, it also expands the date group that
matches the slot notifier.py just found, clicks the radio button for that exact exam type + time,
and then clicks "Przejdź do podsumowania" (go to summary) to land on the summary/review screen. It
deliberately never touches the "Data rozpoczęcia" field — every slot notifier finds is already
within the ~31-day window the picker shows without changing it.

With just this flag on, it stops there, unconditionally, on the summary/review screen: nothing past
that click is automated, whether or not a matching slot was found (someone else may have taken it
in the few seconds since the check that triggered this).

This option is off by default, so enabling it is an explicit choice to go beyond the manual
reschedule-assist workflow.

### Auto-confirming the reservation change

The summary screen (the "Potwierdź wybrany egzamin" modal — exam type, category, date/time, and
price, with no separate payment step) has its own confirm button, "Potwierdź i przejdź dalej".
Turn on "Auto-confirm the reservation change" in Settings → Automation — it asks you to confirm
once in a browser dialog before it takes effect, and only appears there once "Auto-select the
matching slot" is already on — **in addition to** that toggle. (Or set both
`"auto_select_slot": true` and `"auto_confirm_reschedule": true` in `config.json` by hand.) It
clicks that button too — actually submitting the reservation date change. `auto_confirm_reschedule`
alone, without `auto_select_slot`, does nothing: without it, the flow never reaches the summary
screen to confirm on — the wizard enforces this by dimming/disabling the toggle, and the app module
enforces it again server-side regardless of what a saved `config.json` says.

Before that click, it re-checks the summary screen's own text actually shows the date, time, and
exam type you intended — a safety check against the slot-selection step having matched the wrong
row. If that check fails, or the confirm button never becomes clickable, it stops and leaves the
screen for you to finish by hand instead of guessing.

**This is the single highest-stakes automated action in this project.** Every earlier step in the
reschedule flow can be undone just by closing the Chrome window; this one can't — it submits a real
change to an already-paid exam booking. It remains off by default and requires an explicit
confirmation when you enable it.

After clicking confirm, it also reloads `/cases` and checks whether the booking now actually shows
your slot with a confirmed status. If — and only if — that check succeeds, it updates
`current_slot_date` in `config.json` to the new date for you, so the notifier immediately knows
about its own success instead of comparing future checks against the old date. If the check doesn't
succeed (the page didn't update in time, wording differs, etc.), `config.json` is left untouched and
you're told to check the site and update "Date of your current booked slot" in Settings yourself if
it did go through.

**How you find out what happened.** This whole flow is auto-triggered by a detached background
process, so none of it prints to a terminal you're watching. Two things surface it instead:

- Everything gets logged to `~/.local/state/info-kierowca-notifier/reschedule.log` (plain
  append-only text — separate from the main `notifier.log`).
- A phone push (via your existing `ntfy_topic`) fires for anything from the point it starts trying
  to click the final confirm button onward: couldn't verify the summary matched, couldn't click
  confirm, confirmed but couldn't verify it landed, or confirmed and verified. The earlier
  slot-selection steps don't get a second push — you already got one when the slot was first found.

**A short cooldown after every confirm attempt.** Once it's tried the final confirm click — whether
or not that click, or the verification after it, actually succeeded — it won't try again for 15
minutes. This matters specifically for the "confirmed but couldn't verify" case: if the reservation
actually went through but the check just timed out, `current_slot_date` stays on the old date, and
without this cooldown the very next check finding some other nearby slot could immediately submit
*another* real reservation change before you've had a chance to see the push above and step in.

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
