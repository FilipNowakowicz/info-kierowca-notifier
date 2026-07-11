# info-kierowca-notifier

Read-only slot checker for info-kierowca.pl (Polish driving exam booking). Polls two endpoints on
a timer, never books/reserves anything. Zero third-party dependencies (stdlib only).

## Files

- `notifier.py` — the poller. Run standalone with `--loop`, or once per invocation (used by the
  systemd oneshot service).
- `dashboard_server.py` — stdlib HTTP server, binds `127.0.0.1:8787`, serves `status.json` state.
- `pull_session_cookies.py` — pulls session cookies from a running Chrome via remote-debugging
  port; writes them into `session.json`.
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

### Known gotcha: dashboard port-in-use crash loop

`dashboard_server.py` binds `127.0.0.1:8787`. If a stale process (e.g. one started manually
outside systemd, or a previous crashed instance) is still holding the port,
`info-kierowca-dashboard.service` fails fast with `OSError: Address already in use`, retries a
few times, then systemd gives up (`start-limit-hit`). Find/kill whatever holds the port, then
`systemctl --user reset-failed info-kierowca-dashboard.service` before starting again — a plain
`start` after `start-limit-hit` is a no-op.

## Constraints to respect when changing this code

- Read-only by design — no booking/reservation code, ever (see README "Responsible use").
- Don't tighten the poll interval below the current default without being asked; this is
  explicitly a design choice to stay a good citizen of an undocumented API.
- Session cookies / PKK number must never be sent anywhere except info-kierowca.pl itself.
