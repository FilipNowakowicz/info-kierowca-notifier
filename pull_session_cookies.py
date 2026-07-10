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
"""
import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

CONFIG_DIR = Path.home() / ".config" / "info-kierowca-notifier"
SESSION_FILE = CONFIG_DIR / "session.json"
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
    payload = text.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    length = len(payload)
    header = bytearray([0x81])  # FIN + text frame opcode
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
            continue
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument(
        "--all", action="store_true",
        help="Write every cookie found for info-kierowca.pl, not just the two known ones",
    )
    args = parser.parse_args()

    version_url = f"http://{args.host}:{args.port}/json/version"
    try:
        with urllib.request.urlopen(version_url, timeout=5) as resp:
            info = json.loads(resp.read())
    except Exception as e:
        raise SystemExit(
            f"Couldn't reach Chrome's debug port at {version_url} ({e}).\n"
            "Quit Chrome completely, then relaunch it with "
            f"--remote-debugging-port={args.port} and try again."
        )

    ws_url = info["webSocketDebuggerUrl"]
    parsed = urlparse(ws_url.replace("ws://", "http://"))

    sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
    try:
        ws_handshake(sock, f"{parsed.hostname}:{parsed.port}", parsed.path)
        result = cdp_call(sock, 1, "Storage.getCookies")
    finally:
        sock.close()

    cookies = {}
    for c in result.get("cookies", []):
        domain = c.get("domain", "").lstrip(".")
        if domain != DOMAIN_SUFFIX and not domain.endswith("." + DOMAIN_SUFFIX):
            continue
        if not args.all and c["name"] not in COOKIE_NAMES:
            continue
        cookies[c["name"]] = c["value"]

    missing = COOKIE_NAMES - cookies.keys()
    if missing:
        raise SystemExit(
            f"Found cookies for {DOMAIN_SUFFIX} but missing {sorted(missing)} — "
            "make sure you're logged in to info-kierowca.pl in this Chrome profile."
        )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"cookies": cookies}, f, indent=2)
    tmp.replace(SESSION_FILE)
    SESSION_FILE.chmod(0o600)

    print(f"Wrote {len(cookies)} cookie(s) to {SESSION_FILE}: {sorted(cookies.keys())}")


if __name__ == "__main__":
    main()
