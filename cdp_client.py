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
from urllib.parse import urlparse

from paths import CONFIG_DIR, SESSION_FILE

COOKIE_NAMES = {"__Secure-PUDOJT", "__Secure-PUDOJTMD"}
DOMAIN_SUFFIX = "info-kierowca.pl"


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


def page_ws_url(host, port):
    """Websocket URL of the first open page/tab, or None if there isn't one.

    Browser-scoped calls (Storage.*) can use browser_ws_url, but Page.* and
    Runtime.* need a specific page target's own socket.
    """
    with urllib.request.urlopen(f"http://{host}:{port}/json", timeout=5) as resp:
        targets = json.loads(resp.read())
    pages = [t for t in targets if t.get("type") == "page"]
    return pages[0]["webSocketDebuggerUrl"] if pages else None


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


def navigate(host, port, url):
    """Navigate the first open page/tab to `url`. No script injection —
    see inject_and_navigate for the login-auto-click variant that needs one.
    """
    inject_and_navigate(host, port, url, script=None)


def evaluate_in_page(host, port, expression):
    """Run a JS expression in the first open page/tab and return its value.

    Unlike fetch_cookies (which talks to the browser-level debugger target,
    fine for the browser-scoped Storage.getCookies), Runtime.evaluate needs
    a specific page target's own websocket — so this queries /json for the
    open tabs first.
    """
    ws_url = page_ws_url(host, port)
    if ws_url is None:
        return None
    with cdp_socket(ws_url) as sock:
        result = cdp_call(
            sock, 1, "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
    return result.get("result", {}).get("value")


def inject_and_navigate(host, port, url, script):
    """Register `script` to run on every future document in the first open
    page/tab, then navigate it to `url`. `script=None` skips the injection
    and just navigates (see navigate()).

    Page.addScriptToEvaluateOnNewDocument runs before any of a document's
    own scripts — including across cross-origin navigations within the same
    target — so a script registered here is already watching the DOM from
    the very first paint of `url` (and every redirect after it), instead of
    only reacting after our own next poll tick.
    """
    ws_url = page_ws_url(host, port)
    if ws_url is None:
        raise RuntimeError("No page target found to navigate")
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
        json.dump({"cookies": cookies}, f, indent=2)
    tmp.replace(SESSION_FILE)
    SESSION_FILE.chmod(0o600)
