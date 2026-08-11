"""Loopback/same-origin request guards shared by the two local HTTP surfaces.

Both servers bind `127.0.0.1` only, but "bound to loopback" is not by itself a
security boundary against a web page the user happens to have open:

- **CSRF.** Any site can `fetch("http://127.0.0.1:8787/reset-account", {method:
  "POST"})`. It can't read the reply, but every mutating endpoint here (setup,
  reset-account, shutdown, pause, relogin, …) does its damage on the request
  alone. A cross-origin POST with no `Content-Type`, or with one of the three
  CORS-safelisted values (`text/plain`, `application/x-www-form-urlencoded`,
  `multipart/form-data`), is a *simple* request: the browser sends it with no
  preflight at all. Demanding `application/json` forces a preflight, which this
  server answers with a 501 and no CORS headers, so the real request is never
  sent.
- **DNS rebinding.** `GET /settings` renders config.json (PKK number,
  `pz_username`) into a page and `GET /status.json` returns booking history,
  both unauthenticated. An attacker's domain whose DNS briefly resolves to
  127.0.0.1 makes those *same-origin* reads for their own JS — the browser
  sends `Host: evil.example`, and nothing here used to care. Requiring a
  loopback `Host` we recognise breaks that: rebinding can change which IP a
  name resolves to, but not the `Host` header the browser sends.

Checks applied, in order:

1. Every request (GET included): `Host` must be one of our own loopback
   host:port spellings.
2. Every POST: `Origin`, *when present*, must be one of our own origins. It is
   absent for non-browser clients (curl, the app's own `already_running()`
   urllib probe) and can never be forged/omitted by page JS, so absence is
   accepted while a wrong value is rejected. Literal `null` (sandboxed iframe,
   `file://`) is rejected too.
3. Every POST: `Content-Type` must be JSON — see the CSRF note above.

The dashboard's own `/settings` iframe is unaffected: it is genuinely
same-origin, so it sends our `Host` and our `Origin`, and its `fetch` calls
send the JSON content type.

This module imports nothing from the project, so both `web.server` and `app`
can depend on it without either depending on the other.
"""

# `[::1]` is included for completeness even though both servers currently bind
# 127.0.0.1 only: a future IPv6 bind would otherwise be rejected by its own
# guard, and accepting it costs nothing (it is still a loopback literal that
# no rebinding DNS name can produce).
LOOPBACK_HOST_NAMES = ("127.0.0.1", "localhost", "[::1]")

JSON_CONTENT_TYPE = "application/json"

FORBIDDEN_HOST_MESSAGE = (
    "This app only accepts requests addressed to 127.0.0.1 or localhost."
)
FORBIDDEN_ORIGIN_MESSAGE = "Cross-origin requests are not accepted."
UNSUPPORTED_MEDIA_TYPE_MESSAGE = "Requests must use Content-Type: application/json."


def allowed_hosts(port):
    """Every `Host` header value that legitimately addresses this server."""
    return {f"{name}:{port}" for name in LOOPBACK_HOST_NAMES}


def allowed_origins(port):
    """Every `Origin` header value our own pages can send."""
    return {f"http://{name}:{port}" for name in LOOPBACK_HOST_NAMES}


def host_allowed(host_header, port):
    if not host_header:
        return False
    return host_header.strip().lower() in allowed_hosts(port)


def origin_allowed(origin_header, port):
    """`None`/empty means no browser set an Origin — see the module docstring
    for why that is accepted rather than rejected."""
    if origin_header is None or not origin_header.strip():
        return True
    return origin_header.strip().lower() in allowed_origins(port)


def content_type_is_json(content_type_header):
    """True for `application/json`, with or without parameters such as
    `; charset=utf-8`."""
    if not content_type_header:
        return False
    base = content_type_header.split(";", 1)[0].strip().lower()
    return base == JSON_CONTENT_TYPE


class LocalRequestGuardMixin:
    """Mixes the checks above into a `BaseHTTPRequestHandler` subclass.

    The subclass must set `guard_port` to the port it serves on and provide
    `_send(code, body, content_type)` (both handlers in this project already
    do). `guard_get()`/`guard_post()` return True when the request may proceed
    and otherwise send the rejection themselves, so callers read as
    `if not self.guard_get(): return`.
    """

    guard_port = None

    def _reject(self, code, message):
        self._send(code, message.encode("utf-8"), "text/plain; charset=utf-8")
        return False

    def _guard_host(self):
        if host_allowed(self.headers.get("Host"), self.guard_port):
            return True
        return self._reject(403, FORBIDDEN_HOST_MESSAGE)

    def guard_get(self):
        return self._guard_host()

    def guard_post(self):
        if not self._guard_host():
            return False
        if not origin_allowed(self.headers.get("Origin"), self.guard_port):
            return self._reject(403, FORBIDDEN_ORIGIN_MESSAGE)
        if not content_type_is_json(self.headers.get("Content-Type")):
            return self._reject(415, UNSUPPORTED_MEDIA_TYPE_MESSAGE)
        return True
