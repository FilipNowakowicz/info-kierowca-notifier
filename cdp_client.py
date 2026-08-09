#!/usr/bin/env python3
"""Shared Chrome DevTools Protocol (CDP) helpers for reading (and, for
open_logged_in_browser.py, writing) info-kierowca.pl session cookies via a
Chrome instance's local remote-debugging port. Used by pull_session_cookies.py
(manual, Chrome already running), auto_refresh_session.py (launches Chrome
itself and waits for login), and open_logged_in_browser.py (launches Chrome
and injects an already-saved session instead of waiting for a fresh login).

Everything here talks to 127.0.0.1 only and writes straight to session.json.
Nothing is sent to info-kierowca.pl, ntfy.sh, or anywhere else by this module.
"""
import base64
import contextlib
import json
import os
import socket
import struct
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from paths import CONFIG_DIR, SESSION_FILE

COOKIE_NAMES = {"__Secure-PUDOJT", "__Secure-PUDOJTMD"}
DOMAIN_SUFFIX = "info-kierowca.pl"


class TargetNotFoundError(RuntimeError):
    """Raised when an explicitly requested CDP page target is unavailable."""


class StaleTargetError(TargetNotFoundError):
    """Raised when a target that was selected earlier has since disappeared."""


@dataclass(frozen=True)
class PageTarget:
    """Stable metadata for one debuggable Chrome page target.

    Keep this object (especially ``id``), rather than re-selecting a tab by
    position after a navigation opens another tab.
    """

    id: str
    url: str
    title: str
    websocket_url: str
    type: str = "page"

    @classmethod
    def from_cdp(cls, raw):
        return cls(
            id=raw.get("id", ""), url=raw.get("url", ""),
            title=raw.get("title", ""), websocket_url=raw.get("webSocketDebuggerUrl", ""),
            type=raw.get("type", ""),
        )


def ws_handshake(sock, host, path):
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Chrome closed the connection during handshake")
        resp += chunk
    if b" 101 " not in resp.split(b"\r\n", 1)[0]:
        raise ConnectionError(f"WebSocket handshake failed: {resp[:200]!r}")


def ws_send_text(sock, text):
    ws_send_frame(sock, 0x1, text.encode())


def ws_send_frame(sock, opcode, payload=b""):
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    length = len(payload)
    header = bytearray([0x80 | opcode])  # FIN + opcode
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    sock.sendall(bytes(header) + mask + masked)


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Chrome closed the connection")
        buf += chunk
    return buf


def ws_recv_message(sock):
    """Read one full (possibly fragmented) WebSocket message, ignoring pings."""
    parts = []
    while True:
        first2 = _recv_exact(sock, 2)
        fin = first2[0] & 0x80
        opcode = first2[0] & 0x0F
        length = first2[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        payload = _recv_exact(sock, length) if length else b""
        if opcode == 0x9:  # ping -> pong, then keep waiting
            ws_send_frame(sock, 0xA, payload)
            continue
        if opcode == 0xA:  # pong -- not message data, keep waiting
            continue
        if opcode == 0x8:  # close -- payload is a status code, not JSON
            raise ConnectionError("Chrome closed the websocket")
        parts.append(payload)
        if fin:
            break
    return b"".join(parts).decode()


def cdp_call(sock, req_id, method, params=None):
    ws_send_text(sock, json.dumps({"id": req_id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws_recv_message(sock))
        if msg.get("id") == req_id:
            if "error" in msg:
                raise RuntimeError(f"{method} failed: {msg['error']}")
            return msg.get("result", {})
        # else: an unrelated event fired in the meantime — keep reading


def wait_for_debug_port(host, port, timeout=15):
    """Poll /json/version until Chrome's debug port answers, or raise TimeoutError."""
    url = f"http://{host}:{port}/json/version"
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read())
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise TimeoutError(f"Chrome debug port never came up at {url}: {last_err}")


def browser_ws_url(host, port):
    """Websocket URL of the browser-level debugger target."""
    with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=5) as resp:
        return json.loads(resp.read())["webSocketDebuggerUrl"]


def list_page_targets(host, port):
    """Return metadata for every current page target, in Chrome's order."""
    with urllib.request.urlopen(f"http://{host}:{port}/json", timeout=5) as resp:
        raw_targets = json.loads(resp.read())
    return [PageTarget.from_cdp(raw) for raw in raw_targets if raw.get("type") == "page"]


def get_page_target(host, port, target_id):
    """Return ``target_id`` if it still exists, else raise StaleTargetError."""
    for target in list_page_targets(host, port):
        if target.id == target_id:
            return target
    raise StaleTargetError(f"CDP page target {target_id!r} no longer exists")


def find_page_target(host, port, *, target_id=None, host_match=None, url_match=None, title_match=None):
    """Find one page target by explicit ID or URL/host/title criteria.

    A supplied criterion is mandatory.  This deliberately never falls back to
    another page: a changed tab order must not redirect browser automation.
    """
    if not any((target_id, host_match, url_match, title_match)):
        raise ValueError("Specify target_id, host_match, url_match, or title_match")
    for target in list_page_targets(host, port):
        if target_id and target.id != target_id:
            continue
        parsed_host = (urlparse(target.url).hostname or "").lower()
        expected_host = host_match.lower() if host_match else ""
        if host_match and parsed_host != expected_host and not parsed_host.endswith("." + expected_host):
            continue
        if url_match and url_match.lower() not in target.url.lower():
            continue
        if title_match and title_match.lower() not in target.title.lower():
            continue
        return target
    criteria = ", ".join(
        f"{name}={value!r}" for name, value in (
            ("target_id", target_id), ("host_match", host_match),
            ("url_match", url_match), ("title_match", title_match),
        ) if value
    )
    raise TargetNotFoundError(f"No CDP page target matches {criteria}")


def page_ws_url(host, port, *, target_id=None, host_match=None, url_match=None, title_match=None):
    """Return a requested page target's socket URL, or None with no pages.

    The no-criteria form remains for legacy single-tab callers only.  Any
    requested match is strict and raises TargetNotFoundError if absent.
    """
    if any((target_id, host_match, url_match, title_match)):
        return find_page_target(
            host, port, target_id=target_id, host_match=host_match,
            url_match=url_match, title_match=title_match,
        ).websocket_url
    pages = list_page_targets(host, port)
    return pages[0].websocket_url if pages else None


@contextlib.contextmanager
def cdp_socket(ws_url):
    """Connected, handshaken websocket to `ws_url`, closed on exit."""
    parsed = urlparse(ws_url.replace("ws://", "http://"))
    sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
    try:
        ws_handshake(sock, f"{parsed.hostname}:{parsed.port}", parsed.path)
        yield sock
    finally:
        sock.close()


def fetch_cookies(host, port):
    """Return the raw list of cookie dicts Chrome reports via Storage.getCookies."""
    with cdp_socket(browser_ws_url(host, port)) as sock:
        result = cdp_call(sock, 1, "Storage.getCookies")
    return result.get("cookies", [])


def set_cookies(host, port, cookies):
    """Inject `cookies` (name -> value) into Chrome's cookie jar for
    info-kierowca.pl via Storage.setCookies (browser-level, same target as
    fetch_cookies) — call before the profile's first navigation there so the
    site sees an already-authenticated session instead of a login page.

    httpOnly is deliberately False: confirmed live that the site's own
    frontend reads these cookies via `document.cookie` to decide its logged-
    in UI state (no `/jwt/refresh` call happens on page load), so an
    httpOnly copy is invisible to it and it renders as logged out even
    though the cookie is still sent correctly on every request.
    """
    cookie_params = [
        {
            "name": name,
            "value": value,
            "domain": DOMAIN_SUFFIX,
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
        }
        for name, value in cookies.items()
    ]
    with cdp_socket(browser_ws_url(host, port)) as sock:
        cdp_call(sock, 1, "Storage.setCookies", {"cookies": cookie_params})


def create_page_target(
    host, port, url="about:blank", *, registration_timeout=1.5,
    poll_interval=0.05, monotonic=None, sleep=None,
):
    """Create and return a new page target without depending on tab order.

    Chrome can acknowledge ``Target.createTarget`` just before the new page
    appears in ``/json``.  Poll only for the returned target ID for a short,
    bounded interval; never substitute another page while registration is in
    flight.  The clock and sleeper are injectable for deterministic tests.
    """
    with cdp_socket(browser_ws_url(host, port)) as sock:
        result = cdp_call(sock, 1, "Target.createTarget", {"url": url})
    target_id = result.get("targetId")
    if not target_id:
        raise RuntimeError("Chrome did not return a target ID for the new tab")
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    deadline = monotonic() + max(0, registration_timeout)
    while True:
        try:
            return get_page_target(host, port, target_id)
        except StaleTargetError:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TargetNotFoundError(
                    f"Created CDP page target {target_id!r} did not register "
                    f"within {registration_timeout:g} seconds"
                )
            sleep(min(max(0, poll_interval), remaining))


def _target_id(target):
    return target.id if isinstance(target, PageTarget) else target


def navigate_target(host, port, target, url, script=None):
    """Optionally inject a script then navigate exactly ``target``.

    Target existence is checked immediately before opening its websocket.  A
    closed tab therefore fails loudly instead of selecting a replacement tab.
    """
    current = get_page_target(host, port, _target_id(target))
    with cdp_socket(current.websocket_url) as sock:
        cdp_call(sock, 1, "Page.enable")
        if script:
            cdp_call(sock, 2, "Page.addScriptToEvaluateOnNewDocument", {"source": script})
        cdp_call(sock, 3, "Page.navigate", {"url": url})


def evaluate_in_target(host, port, target, expression):
    """Evaluate JavaScript in exactly ``target`` and return its JSON value."""
    current = get_page_target(host, port, _target_id(target))
    with cdp_socket(current.websocket_url) as sock:
        result = cdp_call(
            sock, 1, "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
    return result.get("result", {}).get("value")


def bring_target_to_front(host, port, target):
    """Focus exactly ``target``; raise if it has disappeared."""
    current = get_page_target(host, port, _target_id(target))
    with cdp_socket(current.websocket_url) as sock:
        cdp_call(sock, 1, "Page.bringToFront")


def navigate(host, port, url, target=None):
    """Navigate ``target`` or, for legacy callers, the current first page."""
    if target is not None:
        return navigate_target(host, port, target, url)
    ws_url = page_ws_url(host, port)
    if ws_url is None:
        raise TargetNotFoundError("No page target found to navigate")
    with cdp_socket(ws_url) as sock:
        cdp_call(sock, 1, "Page.navigate", {"url": url})


def evaluate_in_page(host, port, expression, target=None, **match):
    """Run a JS expression in the first open page/tab and return its value.

    Unlike fetch_cookies (which talks to the browser-level debugger target,
    fine for the browser-scoped Storage.getCookies), Runtime.evaluate needs
    a specific page target's own websocket — so this queries /json for the
    open tabs first.
    """
    if target is not None:
        return evaluate_in_target(host, port, target, expression)
    ws_url = page_ws_url(host, port, **match)
    if ws_url is None:
        return None
    with cdp_socket(ws_url) as sock:
        result = cdp_call(
            sock, 1, "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
    return result.get("result", {}).get("value")


def inject_and_navigate(host, port, url, script, target=None):
    """Register `script` to run on every future document in the first open
    page/tab, then navigate it to `url`. `script=None` skips the injection
    and just navigates (see navigate()).

    Page.addScriptToEvaluateOnNewDocument runs before any of a document's
    own scripts — including across cross-origin navigations within the same
    target — so a script registered here is already watching the DOM from
    the very first paint of `url` (and every redirect after it), instead of
    only reacting after our own next poll tick.
    """
    if target is not None:
        return navigate_target(host, port, target, url, script=script)
    ws_url = page_ws_url(host, port)
    if ws_url is None:
        raise TargetNotFoundError("No page target found to navigate")
    with cdp_socket(ws_url) as sock:
        cdp_call(sock, 1, "Page.enable")
        if script:
            cdp_call(sock, 2, "Page.addScriptToEvaluateOnNewDocument", {"source": script})
        cdp_call(sock, 3, "Page.navigate", {"url": url})


def extract_info_kierowca_cookies(raw_cookies, all_cookies=False):
    cookies = {}
    for c in raw_cookies:
        domain = c.get("domain", "").lstrip(".")
        if domain != DOMAIN_SUFFIX and not domain.endswith("." + DOMAIN_SUFFIX):
            continue
        if not all_cookies and c["name"] not in COOKIE_NAMES:
            continue
        cookies[c["name"]] = c["value"]
    return cookies


def write_session_file(cookies):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"cookies": cookies, "captured_at": time.time()}, f, indent=2)
    tmp.replace(SESSION_FILE)
    SESSION_FILE.chmod(0o600)
