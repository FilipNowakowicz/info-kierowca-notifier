# info-kierowca-notifier

[Polski](README.pl.md) · [English](README.md)

A slot checker for [info-kierowca.pl](https://info-kierowca.pl), the Polish driving exam booking
portal. It watches for open exam slots and alerts you — on a dashboard and your phone — the moment
one appears. Checking is always read-only. Optionally, if you already have a paid booking and want
to move it earlier, it can also open a browser that's already logged in and click through to the
reschedule date picker for you. By default, picking the new date and every confirm step after that
is still a click you make yourself; nothing gets rebooked automatically. Two experimental,
off-by-default toggles in Settings → Automation go further and can pick the matching slot and
submit the reservation change with no clicks from you at all — see
[How it works](#how-it-works) and [docs/ADVANCED.md](docs/ADVANCED.md) before turning those on.

![Dashboard showing a found slot](docs/dashboard.png)

## Get started

1. Download the build for your OS from the [Releases page](../../releases) — no installer, no
   Python install, nothing else gets set up on your machine.
2. Run it. A browser tab opens automatically.
3. Choose **Profil Zaufany** (recommended), enter your username and password, and pair Google
   Messages Web when prompted. This lets the app complete both the initial login and later
   session renewals automatically. mObywatel QR login remains available as a manual fallback.
4. You need an existing booked exam: this app changes the date of that booking; it does not
   create a new booking. Have that booking's date ready.
5. Confirm the PKK number/license category it found for you (or fill them in manually if you
   skipped), pick your exam center(s), **enter the date of the booking to reschedule**, and choose how you want to be notified.

That's it — from then on, that browser tab is your dashboard, with a **Quit** button whenever you
want to shut it down.

**First run only:** since these builds aren't code-signed, Windows/macOS will show a one-time
warning. Windows: click "More info" → "Run anyway". macOS: right-click the file → "Open".

## How it works

### Authentication

Profil Zaufany is the recommended login method because it can renew expired sessions without
waiting for a QR scan. mObywatel QR login remains available as a manual alternative. Profil
Zaufany stores the password only in the operating system's
credential vault (Windows Credential Manager, macOS Keychain, or a supported
Linux Secret Service); it is never written to `config.json` or page source.
Pair Google Messages Web once in the app's dedicated Chrome profile so fresh
PZePUAP verification messages can be read during unattended relogin. If no
secure credential backend is available, Profil Zaufany setup fails closed.

Pairing Google Messages Web is required for automatic Profil Zaufany login and
session recovery. Because the government and Google page structure is external
and may change, watch one complete live login before relying on unattended recovery.

It checks the same two endpoints info-kierowca.pl's own site uses to show you slots — it just
does that automatically, on a timer, instead of you refreshing the page by hand. Checking is
strictly read-only: no booking, no reserving, nothing beyond checking availability.

If you turn on the reschedule assist (on by default, toggle with `auto_open_browser`), a matching
slot also opens a Chrome window already logged in with your session, and clicks through to the
"change date" screen for your existing booking. By default it stops there, on an empty date-range
picker with nothing submitted — picking the new date and confirming is always done by you, by hand.
Settings → Automation has two more toggles for this, both off by default: one pre-selects the
matching slot and reaches the summary screen, the other — requiring the first, and its own
confirm dialog before it'll switch on — also confirms it, actually submitting the reservation
change with no click from you at all. See [docs/ADVANCED.md](docs/ADVANCED.md) for exactly what
each one clicks and why, and confirm the slot-selection step works reliably before ever turning
on the second.

The info-kierowca.pl session still expires after roughly an hour. With Profil Zaufany configured,
the app starts refreshing it five minutes before the estimated expiry: it opens its dedicated
Chrome profile, submits the securely stored credentials, reads the fresh PZePUAP verification
code from the paired Google Messages Web tab, and restores the session automatically. With
mObywatel selected, it waits for actual expiry, then opens the QR screen for you to scan. See
[Auto-relogin on session expiry](docs/ADVANCED.md#auto-relogin-on-session-expiry) for requirements,
fallback behavior, and troubleshooting.

Your session cookies and PKK number never go anywhere except info-kierowca.pl itself.

It relies on an undocumented API that info-kierowca.pl could change or block at any time, so use
it at your own risk and in line with the site's terms of service.

## Notifications

During setup you'll get a private link — install the [ntfy app](https://ntfy.sh/app) and
subscribe to it exactly to get a push the moment a slot appears in your chosen window.

## Running from source / advanced setup

Want to run this from source, use it on Linux with systemd, or see exactly how the auto-login
works? See [docs/ADVANCED.md](docs/ADVANCED.md).

## Contributing

Issues and PRs welcome — this is a small, single-purpose tool, so please keep changes focused.
Runtime code lives in the installable `src/info_kierowca_notifier/` package, manually run
maintenance utilities live in `tools/`, and the flat `tests/` directory is discovered with
`uv run python -m unittest discover -s tests -v`.

## License

MIT — see [LICENSE](LICENSE).
