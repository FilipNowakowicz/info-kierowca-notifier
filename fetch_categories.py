#!/usr/bin/env python3
"""Regenerates categories.json — the id/code/label catalog of license
categories the setup wizard shows in its "License category" dropdown, so a
user picks "B — car" instead of guessing the bare numeric id the API wants
(category B happens to be 5, but that number is meaningless on its own).

Like fetch_word_centers.py, the site's dictionary endpoints need an
authenticated session, so this is baked into the repo and refreshed by hand
using your own already-logged-in session.json:

    python fetch_categories.py

The exact dict path for categories isn't documented, so this probes a list
of likely endpoint names under /bknd/config/api/v1/dict/ and uses the first
one that returns a JSON array of category-shaped objects. If none match,
open info-kierowca.pl's category picker with the browser Network tab open,
find the dict request it makes, and add that path to CANDIDATES below.

Read-only: GETs to info-kierowca.pl's own reference-data endpoints only.
Nothing is sent anywhere else.
"""
import json
from pathlib import Path

import notifier

# Likely dict endpoint names, tried in order. `words` is the known-good
# sibling (see fetch_word_centers.py); the rest are guesses for categories.
CANDIDATES = [
    "categories",
    "category",
    "examCategories",
    "licenseCategories",
    "licenceCategories",
    "drivingLicenseCategories",
    "drivingLicenceCategories",
    "profileCategories",
    "vehicleCategories",
]
DICT_BASE = f"{notifier.BASE}/bknd/config/api/v1/dict"
OUTPUT_FILE = Path(__file__).parent / "categories.json"

# Human-readable English glosses for the standard Polish categories, keyed by
# the code the API returns. Only used to enrich the label; unknown codes just
# fall back to the bare code.
GLOSS = {
    "AM": "moped", "A1": "light motorcycle", "A2": "mid motorcycle", "A": "motorcycle",
    "B1": "light quadricycle", "B": "car", "B+E": "car + trailer", "BE": "car + trailer",
    "C1": "light truck", "C1+E": "light truck + trailer", "C": "truck", "C+E": "truck + trailer",
    "CE": "truck + trailer", "D1": "minibus", "D1+E": "minibus + trailer", "D": "bus",
    "D+E": "bus + trailer", "DE": "bus + trailer", "T": "tractor", "PT": "tram",
}


def _pick(item, *keys):
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None


def _normalize(items):
    """Map raw dict rows to {id, code, label}, skipping inactive/malformed rows."""
    out = []
    for item in items:
        if not isinstance(item, dict):
            return None  # not category-shaped; wrong endpoint
        cid = _pick(item, "id", "categoryId", "value")
        code = _pick(item, "code", "name", "symbol", "category", "label")
        if cid is None or code is None:
            return None
        if item.get("isActive") is False:
            continue
        code = str(code).strip()
        gloss = GLOSS.get(code.upper().replace(" ", ""))
        label = f"{code} — {gloss}" if gloss else code
        out.append({"id": int(cid), "code": code, "label": label})
    return out or None


def main():
    if not notifier.SESSION_FILE.exists():
        raise SystemExit(f"No session.json at {notifier.SESSION_FILE} — log in first.")
    session = notifier.load_json(notifier.SESSION_FILE)

    for name in CANDIDATES:
        url = f"{DICT_BASE}/{name}"
        try:
            status, body, _ = notifier.do_request(url, session, method="GET")
        except Exception as exc:
            print(f"  {name}: request failed ({exc})")
            continue
        if status != 200 or not body:
            print(f"  {name}: HTTP {status}")
            continue
        try:
            data = json.loads(body)
        except ValueError:
            print(f"  {name}: 200 but not JSON")
            continue
        if not isinstance(data, list):
            print(f"  {name}: 200 but not a JSON array")
            continue
        normalized = _normalize(data)
        if not normalized:
            print(f"  {name}: 200 but rows aren't category-shaped "
                  f"(first row: {data[0] if data else 'empty'!r})")
            continue

        normalized.sort(key=lambda c: c["id"])
        with open(OUTPUT_FILE, "w") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"Matched /dict/{name} — wrote {len(normalized)} categories to {OUTPUT_FILE}")
        return

    raise SystemExit(
        "No category dict endpoint matched. Open info-kierowca.pl's category "
        "picker with the browser Network tab open, find the /bknd/config/api/v1/"
        "dict/... request, and add its name to CANDIDATES in this script."
    )


if __name__ == "__main__":
    main()
