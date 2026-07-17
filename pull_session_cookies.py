#!/usr/bin/env python3
"""One-time-per-refresh helper: pulls this project's two session cookies
straight out of an already-logged-in Chrome and writes session.json,
so you don't have to copy/paste them out of DevTools by hand.

Everything here is local-only: it talks to Chrome's own remote-debugging
port on 127.0.0.1 and writes straight to session.json. Nothing is sent
to info-kierowca.pl, ntfy.sh, or anywhere else by this script.

Requires Chrome/Chromium started with the debug port open, e.g.:

    Linux:   close all Chrome windows, then:
             google-chrome --remote-debugging-port=9222
    macOS:   quit Chrome, then:
             /Applications/Google Chrome.app/Contents/MacOS/Google Chrome \
               --remote-debugging-port=9222
    Windows: quit Chrome, then:
             "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
               --remote-debugging-port=9222

You must fully quit any already-running Chrome first — the flag is
ignored if another instance owns the profile lock.

SECURITY NOTE: the remote-debugging port grants full control of the
browser and read access to every cookie for every site you're logged
into, not just info-kierowca.pl. It defaults to binding 127.0.0.1 only
— never add --remote-debugging-address=0.0.0.0 or otherwise expose this
port beyond localhost, and don't leave Chrome running this way any
longer than you need to.

See also: auto_refresh_session.py, which launches Chrome itself (in a
dedicated profile) and waits for you to log in via QR, instead of you
launching Chrome and running this script by hand.
"""
import argparse

import cdp_client


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument(
        "--all", action="store_true",
        help="Write every cookie found for info-kierowca.pl, not just the two known ones",
    )
    args = parser.parse_args()

    try:
        raw_cookies = cdp_client.fetch_cookies(args.host, args.port)
    except Exception as e:
        raise SystemExit(
            f"Couldn't reach Chrome's debug port at http://{args.host}:{args.port} ({e}).\n"
            "Quit Chrome completely, then relaunch it with "
            f"--remote-debugging-port={args.port} and try again."
        )

    cookies = cdp_client.extract_info_kierowca_cookies(raw_cookies, all_cookies=args.all)

    missing = cdp_client.COOKIE_NAMES - cookies.keys()
    if missing:
        raise SystemExit(
            f"Found cookies for {cdp_client.DOMAIN_SUFFIX} but missing {sorted(missing)} — "
            "make sure you're logged in to info-kierowca.pl in this Chrome profile."
        )

    cdp_client.write_session_file(cookies)
    print(f"Wrote {len(cookies)} cookie(s) to {cdp_client.SESSION_FILE}: {sorted(cookies.keys())}")


if __name__ == "__main__":
    main()
