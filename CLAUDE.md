# info-kierowca-notifier

Read-only slot checker for info-kierowca.pl (Polish driving exam booking). Polls two endpoints on
a timer, never books/reserves anything. Zero third-party dependencies (stdlib only).

## Files

- `notifier.py` — the poller. Run standalone with `--loop`, or once per invocation (used by the
  systemd oneshot service).
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
- `cdp_client.py` — shared Chrome DevTools Protocol helpers used by both `pull_session_cookies.py`
  and `auto_refresh_session.py` (cookie reads, JS eval in the page, and registering a script to run
  on every future document via `Page.addScriptToEvaluateOnNewDocument`).
- `app.py` — the composed, zero-setup entry point: runs `notifier.loop()` in a background thread,
  serves a first-run setup wizard + the dashboard + a `POST /shutdown` (the page's Stop button;
  hard-exits via `os._exit(0)`) from one stdlib HTTP server, and auto-opens the browser. This is
  what the packaged release binaries (`pyinstaller.spec`, built `--windowed` — no console window)
  actually run. Shares `notifier.CONFIG_DIR`/`STATE_DIR` with the source/systemd path, so switching
  between "ran the binary" and "ran from source" never loses config/session/history. Detects an
  already-running instance on the dashboard port and just opens a browser tab at it instead of
  binding twice. Inside a frozen build, `trigger_auto_refresh()` (in `notifier.py`) can't shell out
  to `auto_refresh_session.py` as a loose file (it doesn't exist on disk, and `sys.executable` is
  the bundled binary itself) — it re-invokes the binary with a hidden `--internal-auto-refresh`
  flag instead, which `app.py:run_internal_auto_refresh()` dispatches straight to
  `auto_refresh_session.main()`. This frozen-only path can only be verified against an actual
  build, not `python app.py` — re-test it (delete `session.json`, confirm Chrome/Edge still opens)
  after any change here before tagging a release.
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

- Read-only by design — no booking/reservation code, ever (see README "Responsible use").
- Don't tighten the poll interval below the current default without being asked; this is
  explicitly a design choice to stay a good citizen of an undocumented API.
- Session cookies / PKK number must never be sent anywhere except info-kierowca.pl itself.
