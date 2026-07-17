#!/usr/bin/env python3
"""Regenerates word_centers.json — the static id/name/location catalog of
every active DORD/WORD/MORD/PORD/ZORD driving-exam center, used by app.py's
setup wizard to show real center names instead of bare numeric IDs.

The site's own dictionary endpoint needs an authenticated session (there's
no way to fetch it before your first login, so it can't be queried live from
the wizard itself), which is why this list is baked into the repo instead.
Run this by hand only when you suspect the site added/renamed/closed a
center, using your own already-logged-in session.json:

    python fetch_word_centers.py

Read-only: a single GET to info-kierowca.pl's own reference-data endpoint —
the same one the real site's own center picker calls. Nothing is sent
anywhere else.
"""
import json
from pathlib import Path

import notifier

DICT_URL = f"{notifier.BASE}/bknd/config/api/v1/dict/words"
OUTPUT_FILE = Path(__file__).parent / "word_centers.json"


def main():
    if not notifier.SESSION_FILE.exists():
        raise SystemExit(f"No session.json at {notifier.SESSION_FILE} — log in first.")
    session = notifier.load_json(notifier.SESSION_FILE)

    status, body, headers = notifier.do_request(DICT_URL, session, method="GET")
    if status != 200:
        raise SystemExit(f"GET {DICT_URL} -> {status}, expected 200. Session may be expired.")

    centers = json.loads(body)
    active = sorted(
        (
            {"id": c["id"], "name": c["name"], "location": c["location"]}
            for c in centers
            if c.get("isActive")
        ),
        key=lambda c: c["name"],
    )

    with open(OUTPUT_FILE, "w") as f:
        json.dump(active, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {len(active)} active centers to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
