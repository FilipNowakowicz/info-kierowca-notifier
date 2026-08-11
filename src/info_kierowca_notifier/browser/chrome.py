"""Shared Chrome discovery, lifecycle, and launch helpers."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from info_kierowca_notifier.browser import cdp as cdp_client

class AttachedChrome:
    """Process-like owner for a pairing browser already on our dedicated port."""
    def __init__(self, host, port, *, monotonic=None, sleep=None):
        self.host, self.port = host, port
        self.monotonic = monotonic or time.monotonic
        self.sleep = sleep or time.sleep
    def poll(self):
        try:
            cdp_client.browser_ws_url(self.host, self.port)
            return None
        except Exception:
            return 0
    def terminate(self):
        try: cdp_client.close_browser(self.host, self.port)
        except Exception: pass
    def wait(self, timeout=None):
        deadline = None if timeout is None else self.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and self.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(
                    f"Chrome CDP endpoint {self.host}:{self.port}", timeout
                )
            delay = 0.1 if deadline is None else min(
                0.1, max(0, deadline - self.monotonic())
            )
            self.sleep(delay)
        return 0
    def kill(self): self.terminate()


def ensure_private_profile_dir(path, *, platform=None, chmod=None):
    """Create only the dedicated profile and make it owner-only on POSIX."""
    path.mkdir(parents=True, exist_ok=True)
    platform = sys.platform if platform is None else platform
    if platform != "win32":
        (chmod or os.chmod)(path, 0o700)
    return path


def chrome_debugging_args(port, profile_dir):
    return [
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        # Chrome/Edge 111+ reject a DevTools websocket handshake whose Origin
        # header isn't on an allow-list (a DNS-rebinding defense). This
        # project's own handshake (browser/cdp.py's ws_handshake) never sends
        # an Origin header, so it shouldn't trip that check either way — but
        # this flag is the documented, upstream-recommended fix for CDP
        # clients hitting exactly this wall, it's a no-op when it isn't
        # needed, and a newer bundled Chrome/Edge on one machine (e.g.
        # Windows) than another (e.g. Linux) is exactly the kind of drift
        # that would make this bite on one OS and not another. Cheap
        # insurance against a version-dependent CDP connection failure that
        # can't be reproduced without the exact browser build that has it.
        "--remote-allow-origins=*",
    ]

# Edge is Chromium-based and supports the same --remote-debugging-port CDP
# flag, so it's included as a fallback — it's preinstalled on all Windows
# machines, unlike Chrome, which matters for a "no setup needed" install.
CHROME_CANDIDATES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "msedge", "microsoft-edge", "microsoft-edge-stable",
]
CHROME_MAC_PATH = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
# CHROME_MAC_PATH only covers the common system-wide install location — the
# same class of gap CHROME_WIN_PATHS had on Windows before the registry
# lookup below existed, and by the same reasoning it would miss Chrome
# installed to ~/Applications (no-admin-rights install) or anywhere else.
# _chrome_from_macos_spotlight() is checked first for exactly that reason.
# UNVERIFIED as of 2026-07-22 — written without a live Mac to test on;
# CHROME_MAC_PATH remains as a fallback for the common case if this doesn't
# pan out or mdfind is unavailable/disabled.
# CHROME_CANDIDATES' PATH-based names ("google-chrome" etc.) are a Linux/Mac
# convention — a Windows Chrome install never puts chrome.exe on PATH under
# any of those names, so without these explicit paths find_chrome() always
# fell through to EDGE_WIN_PATHS below even on a machine with Chrome
# installed (confirmed live: Edge opened instead of the user's own Chrome).
# %LOCALAPPDATA% covers the common non-admin/per-user install; the two
# Program Files paths cover a machine-wide install (matching EDGE_WIN_PATHS'
# own x86/64 pair).
CHROME_WIN_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]
EDGE_WIN_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def _chrome_from_windows_registry():
    """Look up Chrome's install path via the "App Paths" registry key —
    the same mechanism Windows itself uses to resolve a bare "chrome.exe"
    (e.g. from the Run dialog or `start chrome`). Every normal Chrome
    installer (per-user or per-machine) writes this key regardless of which
    drive/folder it installed to, so it's more robust than guessing fixed
    paths like CHROME_WIN_PATHS above — those only cover the default
    locations and silently miss anything installed elsewhere. winreg only
    exists on Windows; the ImportError there makes this a clean no-op on
    Linux/Mac.
    """
    try:
        import winreg
    except ImportError:
        return None
    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                path, _ = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        if path and Path(path).exists():
            return path
    return None

def _chrome_from_macos_spotlight():
    """macOS analog of _chrome_from_windows_registry() above: `mdfind`
    (Spotlight) looks Chrome up by bundle identifier, which finds it
    regardless of install location — /Applications, ~/Applications, or
    anywhere else — unlike the fixed CHROME_MAC_PATH guess. Returns None on
    any failure (wrong OS, mdfind missing/disabled, nothing indexed) so
    CHROME_MAC_PATH stays a working fallback for the common case.

    UNVERIFIED as of 2026-07-22 — written without a live Mac to test on.
    """
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.check_output(
            ["mdfind", "kMDItemCFBundleIdentifier == 'com.google.Chrome'"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
    except Exception:
        return None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        binary = Path(line) / "Contents" / "MacOS" / "Google Chrome"
        if binary.exists():
            return str(binary)
    return None


def find_chrome():
    for name in CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    macos_path = _chrome_from_macos_spotlight()
    if macos_path:
        return macos_path
    if CHROME_MAC_PATH.exists():
        return str(CHROME_MAC_PATH)
    registry_path = _chrome_from_windows_registry()
    if registry_path:
        return registry_path
    for path in CHROME_WIN_PATHS:
        if path.exists():
            return str(path)
    for path in EDGE_WIN_PATHS:
        if path.exists():
            return str(path)
    raise SystemExit("Couldn't find a Chrome/Chromium/Edge binary on PATH.")


def chrome_available():
    """Whether find_chrome() would succeed, without raising. Used by
    auth.launch.trigger_auto_refresh()/booking.launch.trigger_open_browser() to detect a
    missing Chromium browser (e.g. a Mac with only Safari installed)
    synchronously, before spawning the detached subprocess whose own
    find_chrome() failure would otherwise be invisible — its stdout/stderr
    go to DEVNULL since the launch is fire-and-forget.
    """
    try:
        find_chrome()
        return True
    except SystemExit:
        return False

