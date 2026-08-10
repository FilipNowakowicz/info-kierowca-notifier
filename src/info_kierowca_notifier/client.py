"""HTTP and session-cookie mechanics for info-kierowca.pl."""
import json
import urllib.error
import urllib.request

from info_kierowca_notifier import tls_transport

BASE = "https://info-kierowca.pl"
REFRESH_URL = f"{BASE}/bknd/auth/api/v1/jwt/refresh"
SEARCH_URL = f"{BASE}/bknd/exam/api/v1/Schedules/user/MultipleCentersExams"
# Traced from the site's own main-*.js (pkkProfilesResource(), used by its
# "check documents"/reservation forms to resolve a PKK number to a license
# category) — used by the app module's setup wizard to prefill the PKK number and
# category from the account instead of asking the user to type them in.
PKK_PROFILES_URL = f"{BASE}/bknd/status/api/v1/pkk/get_profiles"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 15


def cookie_header(session):
    return "; ".join(f"{k}={v}" for k, v in session.get("cookies", {}).items())


def cookie_is_deletion(value, attrs):
    """Whether a Set-Cookie is the server clearing the cookie rather than
    setting one. Servers expire a cookie by sending it back empty and/or with
    Max-Age=0 / an Expires in the past."""
    if not value:
        return True
    lowered = attrs.lower()
    if "max-age=0" in lowered.replace(" ", ""):
        return True
    return "expires=thu, 01 jan 1970" in lowered


def parse_set_cookies(headers, session):
    """Merge Set-Cookie headers into session["cookies"].

    Deletions must actually delete: a logout/invalidate response carrying
    `__Secure-PUDOJT=; Expires=Thu, 01 Jan 1970 ...` would otherwise be stored
    as an empty-string cookie, leaving session.json looking complete to
    booking.reschedule's COOKIE_NAMES check — which then injects blank
    cookies and opens a logged-*out* tab instead of reporting the problem.
    """
    if headers is None:
        return
    for raw in headers.get_all("Set-Cookie") or []:
        name, _, rest = raw.partition("=")
        value, _, attrs = rest.partition(";")
        name = name.strip()
        cookies = session.setdefault("cookies", {})
        if cookie_is_deletion(value, attrs):
            cookies.pop(name, None)
        else:
            cookies[name] = value


def do_request(url, session, method="GET", json_body=None):
    data = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie_header(session),
        "Referer": f"{BASE}/reservation",
        "Origin": BASE,
    }
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with tls_transport.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            parse_set_cookies(resp.headers, session)
            return resp.status, body, resp.headers
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, body, e.headers
    except (urllib.error.URLError, tls_transport.TLSConfigurationError) as e:
        return None, str(e).encode(), None


def fetch_pkk_profiles(session):
    """Best-effort lookup of the account's PKK profile(s) — used by the app module's
    setup wizard to prefill the PKK number/category right after QR login
    instead of asking the user to type a PKK number in blind. The endpoint
    also returns pesel/name/birthDate; only pkkNumber/categoryName are kept,
    matching this project's minimal-footprint stance on PII. Returns []
    on any failure (session not ready yet, unexpected shape, etc.) so a
    fetch hiccup just falls back to manual entry rather than blocking setup.
    """
    try:
        status, body, _headers = do_request(PKK_PROFILES_URL, session, method="GET")
        if status != 200:
            return []
        profiles = json.loads(body)
        return [
            {"pkkNumber": p["pkkNumber"], "categoryName": p["categoryName"]}
            for p in profiles
            if isinstance(p, dict) and p.get("pkkNumber") and p.get("categoryName")
        ]
    except Exception:
        return []


