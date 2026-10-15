# Contributing

Thanks for helping out! Issues and pull requests are welcome, in English or Polish.

## Project status — please read first

I've passed my driving exams and **no longer have access to info-kierowca.pl**, so I can't
reproduce bugs or test changes against the live site myself. That means:

- **You are the only one who can test your change against the real site.** Please do, and say in
  the PR exactly what you tested (login method, OS, which flow, what happened).
- I review for correctness and safety, run the test suite, and merge. I can't confirm that a
  site-facing change actually works, so a clear test description matters more than usual.

## Pull requests

- **Tick "Allow edits by maintainers"** when opening the PR, so I can push small fixes directly
  instead of asking you for another round trip.
- Don't bump the version or edit `CHANGELOG`-style release notes in a PR — that happens at release.
- Add or update tests for what you changed (see below).
- Every user-visible English string needs a matching Polish entry in
  `src/info_kierowca_notifier/web/localization.py`; a missing one silently falls back to English.

## Development setup

The project uses [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run python -m info_kierowca_notifier          # run the app
uv run python -m unittest discover -s tests -v   # tests
uv run python -m pyflakes src tools tests        # lint
```

Both the tests and pyflakes must pass — CI runs them on Python 3.9, 3.11 and 3.13.

Runtime code lives in `src/info_kierowca_notifier/`, manual maintenance scripts in `tools/`, and
tests in the flat `tests/` directory. `docs/ADVANCED.md` explains running from source, systemd,
and how auto-login works.

## Rules that protect users

This tool acts on real accounts and real, paid exam bookings. Changes must keep these properties:

- **Be a good citizen of the site.** info-kierowca.pl allows about 10 searches per hour and
  going over can get an account temporarily blocked. Don't lower `MIN_POLL_INTERVAL_SECONDS` or
  weaken the rate-limit handling.
- **Booking automation stays opt-in and fails closed.** Anything that can create or change a
  reservation must be off by default, must verify what it's about to submit, and must stop
  (leaving the step to the user) whenever it isn't sure. The final submit click must never be
  retried automatically.
- **Personal data stays local.** Session cookies, PKK numbers and login credentials are only ever
  sent to info-kierowca.pl (and the identity provider during login). Never log them or send
  them anywhere else.

## Reporting a bug

Please include:

- How you run it (Windows/macOS/Linux release binary, or from source) and the version.
- Login method (mObywatel or Profil Zaufany).
- What you expected and what happened.
- Relevant lines from the logs in `~/.local/state/info-kierowca-notifier/` (`notifier.log`,
  `auto-refresh.log`, `reschedule.log`).

**Before pasting logs, remove anything personal** — PKK number, name, PESEL, cookies, phone
number, or your ntfy topic.
