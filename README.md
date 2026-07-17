# info-kierowca-notifier

A notification-only slot checker for [info-kierowca.pl](https://info-kierowca.pl), the Polish
driving exam booking portal. It watches for open exam slots and alerts you — on a dashboard and
your phone — the moment one appears. It never books or reserves anything for you; you always do
that yourself, in your own browser.

![Dashboard showing a found slot](docs/dashboard.png)

## Get started

1. Download the build for your OS from the [Releases page](../../releases) — no installer, no
   Python install, nothing else gets set up on your machine.
2. Run it. A browser tab opens automatically.
3. Fill in your PKK number, pick your exam center(s), and choose how you want to be notified.
4. Scan the QR code that pops up (with the mObywatel app) to log in.

That's it — from then on, that browser tab is your dashboard, with a **Stop** button whenever you
want to shut it down.

**First run only:** since these builds aren't code-signed, Windows/macOS will show a one-time
warning. Windows: click "More info" → "Run anyway". macOS: right-click the file → "Open".

## How it works

It checks the same two endpoints info-kierowca.pl's own site uses to show you slots — it just
does that automatically, on a timer, instead of you refreshing the page by hand. It's strictly
read-only: no booking, no reserving, nothing beyond checking availability. Your session cookies
and PKK number never go anywhere except info-kierowca.pl itself.

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

## License

MIT — see [LICENSE](LICENSE).
