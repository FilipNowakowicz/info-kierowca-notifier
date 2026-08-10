# Info-Kierowca Notifier — wolne terminy egzaminu na prawo jazdy

### Powiadomienia i automatyczna zmiana rezerwacji

[Pełna dokumentacja po polsku](README.pl.md) · English

**Po polsku:** Bezpłatna i otwartoźródłowa alternatywa dla aplikacji takich jak
Złap Termin, PrawkoBot, PrawkoSniper, Szybki Egzamin i SzybkiePrawko. Wyszukuje
wolne, wcześniejsze terminy egzaminu na prawo jazdy w WORD, wysyła powiadomienia
i może automatycznie zmienić termin istniejącej rezerwacji w info-kierowca.pl.
Działa na Twoim komputerze, bez abonamentu i przekazywania sesji zewnętrznemu
operatorowi. Logujesz się raz przez Profil Zaufany, a kolejne logowania i
odnawianie sesji odbywają się automatycznie.

Find earlier Polish driving exam dates and automatically reschedule your existing booking on
[info-kierowca.pl](https://info-kierowca.pl). Sign in once with Profil Zaufany, and the app keeps
your session active, monitors your chosen exam centers, and alerts you on the dashboard and your
phone when a suitable slot appears. If you already have a paid booking, you can either complete the
date change yourself or enable automatic rebooking so the app selects the matching slot, verifies
its details, and submits the change for you.

## Features

- **Sign in once:** Profil Zaufany login and session renewal can run automatically.
- **Find the right slot:** monitor selected WORD centers, exam types, dates, and times.
- **Get notified immediately:** see matches on the dashboard and receive optional phone alerts.
- **Reschedule automatically:** let the app select, verify, and confirm an earlier matching slot.
- **Keep control of your data:** credentials and session stay on your computer.
- **Use it for free:** open source, MIT-licensed, and subscription-free.

## Download

| Windows | macOS | Linux |
|---|---|---|
| [Download `.exe`](../../releases/latest/download/info-kierowca-notifier-windows.exe) | [Download](../../releases/latest/download/info-kierowca-notifier-macos) | [Download](../../releases/latest/download/info-kierowca-notifier-linux) |

[View all releases and release notes](../../releases)

![Dashboard showing a found slot](docs/dashboard.png)

## Get started

1. Download the build for your OS above — no installer or Python installation required.
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
Settings → Automation has two optional toggles for this, both off by default: one pre-selects the
matching slot and reaches the summary screen, the other — requiring the first, and its own
confirm dialog before it'll switch on — also confirms it, actually submitting the reservation
change with no click from you at all. See [docs/ADVANCED.md](docs/ADVANCED.md) for exactly what
each one clicks and the safeguards around the final change.

The info-kierowca.pl session still expires after roughly an hour. With Profil Zaufany configured,
the app starts refreshing it five minutes before the estimated expiry: it opens its dedicated
Chrome profile, submits the securely stored credentials, reads the fresh PZePUAP verification
code from the paired Google Messages Web tab, and restores the session automatically. Settings
can run this browser headlessly so no window appears during automatic renewal. With mObywatel
selected, it waits for actual expiry, then opens the QR screen for you to scan. See
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
