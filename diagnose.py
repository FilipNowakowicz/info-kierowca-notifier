#!/usr/bin/env python3
"""Standalone one-shot diagnostic for the Windows Chrome/Edge auto-login flow.

Not part of the running app — this exists purely so a single run on a
problem machine produces one plain-text report to send back, instead of a
slow back-and-forth of "what does X look like on your machine". Built into
its own console exe (see .github/workflows/diagnostic.yml) so it needs no
Python install on the target machine.

Reuses auto_refresh_session.py/cdp_client.py/paths.py rather than
duplicating their logic, so this diagnoses the exact same code path the
real app runs — not a simplified stand-in that could behave differently.

Deliberately never prints session.json/config.json *contents* — only
existence/size/mtime — since those can hold session cookies and a PKK
number. Everything else here (paths, process lists, page titles/URLs, short
page-text snippets) is safe to paste back into a chat.
"""
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

import auto_refresh_session as ars
import cdp_client
import paths

REPORT_LINES = []


def log(msg=""):
    print(msg)
    REPORT_LINES.append(str(msg))


def section(title):
    log()
    log("=" * 70)
    log(title)
    log("=" * 70)


def safe(fn, default="<error>"):
    try:
        return fn()
    except Exception as e:
        return f"{default}: {e!r}"


def tasklist(image_name):
    """Best-effort list of (name, pid) for running processes matching
    image_name. Windows-only tool; empty list on any failure (wrong OS,
    tasklist missing, etc.) rather than raising — this is purely
    informational, used to spot an orphaned chrome.exe/msedge.exe still
    holding a profile-dir lock from a previous crashed/killed run.
    """
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image_name}"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip().strip('"')
        if not line or "No tasks" in line:
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def pid_alive(pid):
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"PID eq {pid}"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return "unknown"
    return "yes" if str(pid) in out else "no"


def inspect_profile_dir(label, path):
    section(f"Profile dir: {label} ({path})")
    if not path.exists():
        log("does not exist (nothing to inspect)")
        return
    log(f"exists, contents (first 20): {sorted(p.name for p in path.iterdir())[:20]}")
    lock = path / "SingletonLock"
    if lock.exists():
        target = safe(lambda: lock.read_text(errors="replace"), default="<unreadable>")
        log(f"SingletonLock present -> {target!r}")
        pid = None
        for tok in str(target).replace("\\", "-").split("-"):
            if tok.isdigit():
                pid = tok
        if pid:
            log(f"  referenced pid {pid} currently running: {pid_alive(pid)}")
    else:
        log("no SingletonLock file")


def report_chrome_resolution():
    section("Chrome/Edge candidate resolution")
    for name in ars.CHROME_CANDIDATES:
        log(f"PATH candidate {name!r}: {shutil.which(name)}")
    log(f"CHROME_MAC_PATH exists: {ars.CHROME_MAC_PATH.exists()}")
    try:
        import winreg
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
        for hive_name, hive in (
            ("HKCU", winreg.HKEY_CURRENT_USER), ("HKLM", winreg.HKEY_LOCAL_MACHINE)
        ):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    val, _ = winreg.QueryValueEx(key, None)
                log(f"registry {hive_name}: {val!r} (exists on disk: {Path(val).exists()})")
            except OSError as e:
                log(f"registry {hive_name}: not found ({e!r})")
    except ImportError:
        log("winreg not available (not on Windows)")
    for p in ars.CHROME_WIN_PATHS:
        log(f"CHROME_WIN_PATHS candidate: {p} exists={p.exists()}")
    for p in ars.EDGE_WIN_PATHS:
        log(f"EDGE_WIN_PATHS candidate: {p} exists={p.exists()}")
    resolved = safe(ars.find_chrome, default="find_chrome() FAILED")
    log(f"--> find_chrome() resolved to: {resolved}")


def report_processes():
    section("Running browser processes (orphan/lock check)")
    for image in ("chrome.exe", "msedge.exe"):
        rows = tasklist(image)
        if not rows:
            log(f"{image}: none running")
        else:
            for name, pid in rows:
                log(f"{image}: pid={pid}")


def report_state_files():
    section("State files (existence/size only — never contents)")
    for label, fp in [
        ("config.json", paths.CONFIG_FILE),
        ("session.json", paths.SESSION_FILE),
        ("status.json", paths.STATUS_FILE),
        ("auto-refresh.log", paths.AUTO_REFRESH_LOG_FILE),
        ("reschedule.log", paths.RESCHEDULE_LOG_FILE),
        ("notifier.log", paths.LOG_FILE),
        ("auto-refresh.lock", paths.AUTO_REFRESH_LOCK),
    ]:
        if fp.exists():
            st = fp.stat()
            log(f"{label}: exists, {st.st_size} bytes, mtime={datetime.fromtimestamp(st.st_mtime)}")
        else:
            log(f"{label}: missing")


def live_test(profile_dir, port, label, duration=20):
    try:
        chrome = ars.find_chrome()
    except SystemExit as e:
        log(f"[{label}] find_chrome() failed: {e}")
        return
    profile_dir.mkdir(parents=True, exist_ok=True)
    log(f"[{label}] launching {chrome!r} with profile {profile_dir} on port {port}")
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=460,760",
            "--app=about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    exit_code = proc.poll()
    log(
        f"[{label}] process state 1s after launch: "
        f"{'still running' if exit_code is None else f'EXITED IMMEDIATELY (code {exit_code})'}"
    )
    if exit_code is not None:
        return
    try:
        cdp_client.wait_for_debug_port("127.0.0.1", port, timeout=15)
        log(f"[{label}] debug port came up")
    except Exception as e:
        log(f"[{label}] debug port never came up: {e!r}")
        proc.terminate()
        return
    try:
        cdp_client.inject_and_navigate(
            "127.0.0.1", port, ars.DEFAULT_URL, ars.AUTO_CLICK_OBSERVER_JS
        )
        log(f"[{label}] navigation + click-observer injected")
    except Exception as e:
        log(f"[{label}] inject_and_navigate failed: {e!r}")
        proc.terminate()
        return

    deadline = time.monotonic() + duration
    last_state = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log(f"[{label}] browser process exited early (code {proc.returncode}) during wait")
            break
        clicked = ars.try_auto_click("127.0.0.1", port)
        state = safe(lambda: cdp_client.evaluate_in_page(
            "127.0.0.1", port,
            "({title: document.title, url: location.href, "
            "text: (document.body && document.body.innerText || '').slice(0, 300)})",
        ))
        if state != last_state:
            log(f"[{label}] page state: {state}")
            last_state = state
        if clicked:
            log(f"[{label}] auto-clicked: {clicked!r}")
        try:
            cookies = cdp_client.extract_info_kierowca_cookies(
                cdp_client.fetch_cookies("127.0.0.1", port)
            )
            if cdp_client.COOKIE_NAMES <= cookies.keys():
                log(f"[{label}] session cookies detected (login succeeded) — not logging their values")
                break
        except Exception:
            pass
        time.sleep(1)

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    log(f"[{label}] test finished")


def run_live_tests():
    section("LIVE TEST 1/2: real profile dir (same one the app uses)")
    log(
        "This reproduces exactly what the real app does, including any stale "
        "lock/corruption already in that folder. If a QR code appears, feel free "
        "to actually scan it — succeeding just ends the test early and nothing "
        "sensitive gets written to this report."
    )
    live_test(ars.PROFILE_DIR, port=9444, label="real profile")

    section("LIVE TEST 2/2: brand-new temp profile dir")
    log("Isolates whether a fresh, never-used profile behaves differently.")
    tmp = Path(tempfile.mkdtemp(prefix="ikw-diag-"))
    live_test(tmp, port=9445, label="fresh temp profile")


def write_report():
    out_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    out_path = out_dir / f"ikw-diagnostic-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    try:
        out_path.write_text("\n".join(REPORT_LINES), encoding="utf-8")
        print(f"\nReport written to: {out_path}")
        print("Send this file back — it contains no cookies/secrets, only paths,")
        print("process/browser diagnostics, and short page-state snippets.")
    except Exception as e:
        print(f"\nCouldn't write report file ({e!r}) — copy the console output above instead.")


def main():
    section("info-kierowca-notifier Windows diagnostic")
    log(f"generated: {datetime.now().isoformat()}")
    log(f"platform: {safe(platform.platform)}")
    log(f"machine: {safe(platform.machine)}")
    log(f"python: {sys.version}")
    log(f"frozen: {getattr(sys, 'frozen', False)}")

    report_chrome_resolution()
    report_processes()
    inspect_profile_dir("auto-refresh (login/QR)", ars.PROFILE_DIR)
    try:
        import open_logged_in_browser as olb
        inspect_profile_dir("open-logged-in-browser (reschedule)", olb.PROFILE_DIR)
    except Exception as e:
        log(f"could not inspect open_logged_in_browser profile dir: {e!r}")
    report_state_files()

    if paths.AUTO_REFRESH_LOCK.exists():
        section("SKIPPING live browser test")
        log(
            "auto-refresh.lock exists — a real relogin looks like it's already in "
            "progress (or a stale lock from one). Close the main app / delete the "
            "lock file, then rerun this diagnostic for the live test."
        )
    else:
        run_live_tests()

    write_report()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("UNCAUGHT ERROR:")
        log(traceback.format_exc())
        write_report()
    if getattr(sys, "frozen", False):
        input("\nPress Enter to close this window...")
