#!/usr/bin/env python3
"""Regenerates categories.json — the id/code/label catalog of licence
categories the setup wizard shows in its "License category" dropdown, so a
user picks "B — car" instead of guessing the bare numeric id the API wants
(category B happens to be 5, but that number is meaningless on its own).

Like fetch_word_centers.py, this refreshes a repo-baked snapshot by hand
using your own already-logged-in session.json:

    python fetch_categories.py

Two live sources, both read-only GETs to info-kierowca.pl:

  1. The *codes* currently offered come from the Applications service's
     licence-category dictionary:
        GET /bknd/Applications/api/v1/dictionary/licence-category-groups
     which returns e.g. {"DrivingLicense":["Am","A1",...,"B",...], ...}.

  2. The *numeric ids* the exam-search endpoint actually wants
     (organizationId + `category`, see notifier.SEARCH_URL) are NOT served
     by any dictionary endpoint — the frontend hardcodes them as an enum in
     its JS bundle. That enum is mirrored below as CODE_TO_ID. If
     info-kierowca.pl ever adds a category, this map is where to update it:
     open the site's main-*.js bundle and search for `B:5` to find the
     current object.

Nothing is sent anywhere except info-kierowca.pl itself.
"""
import json
from pathlib import Path

import notifier

# Applications-service base (from the site's /assets/config.json
# `applicationApiUrl`), where the licence-category dictionary lives. Note this
# is a *different* base from fetch_word_centers.py's `dictionaryApiUrl`
# (/bknd/config/api/v1) — the category catalog is served by Applications.
GROUPS_URL = f"{notifier.BASE}/bknd/Applications/api/v1/dictionary/licence-category-groups"
OUTPUT_FILE = Path(__file__).parent / "categories.json"

# Code -> numeric id the exam-search API expects. Mirrored verbatim from the
# frontend JS bundle (search `B:5`). This is the authoritative mapping; the
# dictionary endpoint above only lists which codes exist, not their ids.
CODE_TO_ID = {
    "A": 1, "A1": 2, "A2": 3, "Am": 4, "B": 5, "B1": 6, "Be": 7,
    "C": 8, "C1": 9, "Ce": 10, "C1E": 11, "D": 12, "D1": 13, "De": 14,
    "D1E": 15, "T": 16, "Pt": 17, "BlokC": 18, "BlokD": 19,
}

# How each code is written on an official Polish licence, plus an English
# gloss for the label. Keyed by the bundle's code spelling.
DISPLAY = {
    "Am": "AM", "A1": "A1", "A2": "A2", "A": "A", "B1": "B1", "B": "B",
    "Be": "B+E", "C1": "C1", "C1E": "C1+E", "C": "C", "Ce": "C+E",
    "D1": "D1", "D1E": "D1+E", "D": "D", "De": "D+E", "T": "T", "Pt": "PT",
    "BlokC": "C block", "BlokD": "D block",
}
GLOSS = {
    "Am": "moped", "A1": "light motorcycle", "A2": "mid motorcycle", "A": "motorcycle",
    "B1": "light quadricycle", "B": "car", "Be": "car + trailer",
    "C1": "light truck", "C1E": "light truck + trailer", "C": "truck", "Ce": "truck + trailer",
    "D1": "minibus", "D1E": "minibus + trailer", "D": "bus", "De": "bus + trailer",
    "T": "tractor", "Pt": "tram",
    "BlokC": "truck-category block exam", "BlokD": "bus-category block exam",
}


def _live_codes(session):
    """Return the set of category codes the site currently offers, or None."""
    try:
        status, body, _ = notifier.do_request(GROUPS_URL, session, method="GET")
    except Exception as exc:
        print(f"  licence-category-groups request failed ({exc})")
        return None
    if status != 200 or not body:
        print(f"  licence-category-groups: HTTP {status}")
        return None
    try:
        data = json.loads(body)
    except ValueError:
        print("  licence-category-groups: 200 but not JSON")
        return None
    codes = set()
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                codes.update(str(c) for c in v)
    elif isinstance(data, list):
        codes.update(str(c) for c in data)
    return codes or None


def main():
    if not notifier.SESSION_FILE.exists():
        raise SystemExit(f"No session.json at {notifier.SESSION_FILE} — log in first.")
    session = notifier.load_json(notifier.SESSION_FILE)

    live = _live_codes(session)
    if live is None:
        print("Couldn't confirm the live code list; falling back to the built-in "
              "CODE_TO_ID map (still writes a valid categories.json).")
        live = set(CODE_TO_ID)
    else:
        # Case-insensitive reconciliation: the dict spells codes like "Am"/"Be",
        # match them to CODE_TO_ID keys regardless of casing.
        lower = {c.lower() for c in live}
        unknown = sorted(c for c in live if c.lower() not in {k.lower() for k in CODE_TO_ID})
        if unknown:
            print(f"  note: live codes with no id in CODE_TO_ID (skipped): {unknown} "
                  "— add them to CODE_TO_ID from the JS bundle if the exam picker offers them.")
        live = {k for k in CODE_TO_ID if k.lower() in lower}

    rows = []
    for code in live:
        cid = CODE_TO_ID[code]
        disp = DISPLAY.get(code, code)
        gloss = GLOSS.get(code)
        label = f"{disp} — {gloss}" if gloss else disp
        rows.append({"id": cid, "code": disp, "label": label})

    rows.sort(key=lambda c: c["id"])
    with open(OUTPUT_FILE, "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {len(rows)} categories to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
