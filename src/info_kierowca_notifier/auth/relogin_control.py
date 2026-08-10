"""Cooperative control protocol for the long-running QR relogin helper.

The lock identifies the helper, while the restart request lets that helper
shut down its own Chrome process. The dashboard never sends a signal to a PID
read from disk: if the helper cannot acknowledge the request, a replacement is
not started against the same profile/debug port.
"""

import json
import os
import secrets
import tempfile
import time
import threading
import ctypes
from ctypes import wintypes
from dataclasses import dataclass


LOCK_VERSION = 1
RESTART_REQUEST_VERSION = 1

_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87
_RESTART_REQUEST_LOCK = threading.Lock()


@dataclass(frozen=True)
class ReloginOwner:
    pid: int
    token: str


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = path.with_name(os.path.basename(temporary_name))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def new_owner(pid=None):
    return ReloginOwner(pid or os.getpid(), secrets.token_urlsafe(24))


def write_lock(path, owner):
    _atomic_write(path, {"version": LOCK_VERSION, "pid": owner.pid, "token": owner.token})


def read_lock(path):
    """Return a validated cooperative owner, or ``None`` for old/bad locks."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != LOCK_VERSION:
            return None
        pid = payload.get("pid")
        token = payload.get("token")
        if not isinstance(pid, int) or pid <= 0 or not isinstance(token, str) or not token:
            return None
        return ReloginOwner(pid, token)
    except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
        return None


def read_legacy_pid(path):
    """Read the pre-JSON lock format solely for liveness compatibility."""
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        return pid if pid > 0 else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def _posix_process_alive(pid, probe=os.kill):
    try:
        probe(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        # Some alternate probes raise a plain OSError rather than the more
        # specific subclasses. ESRCH means absent; EPERM means present but
        # owned by another user.
        if error.errno == 3:  # errno.ESRCH, kept literal for Windows imports
            return False
        if error.errno == 1:  # errno.EPERM
            return True
        return False


def _load_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _windows_process_alive(pid, kernel32=None, last_error=None):
    """Query PID state without signaling it or obtaining termination rights.

    Access-denied and unexpected query failures are treated conservatively as
    alive: they do not justify removing a concurrency lock or starting another
    browser. ERROR_INVALID_PARAMETER is the documented result for a PID that
    does not identify an openable process (including an already-exited PID).
    """
    kernel32 = kernel32 or _load_kernel32()
    last_error = last_error or ctypes.get_last_error
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        error = last_error()
        if error == _ERROR_INVALID_PARAMETER:
            return False
        return True
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == _WAIT_OBJECT_0:
            return False
        # WAIT_TIMEOUT is the normal live result. WAIT_FAILED and any unknown
        # value remain conservative because this function gates concurrency.
        return True
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid):
    """Return whether something is alive at *pid*, never whether it is ours."""
    if os.name == "nt":
        return _windows_process_alive(pid)
    return _posix_process_alive(pid)


def lock_pid(path):
    owner = read_lock(path)
    return owner.pid if owner else read_legacy_pid(path)


def write_restart_request(path, owner, *, issued_at, expires_at):
    with _RESTART_REQUEST_LOCK:
        _atomic_write(
            path,
            {
                "version": RESTART_REQUEST_VERSION,
                "token": owner.token,
                "issued_at": issued_at,
                "expires_at": expires_at,
            },
        )


def restart_requested(path, owner, *, clock=time.time):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        issued_at = payload.get("issued_at")
        expires_at = payload.get("expires_at")
        now = clock()
        return (
            payload.get("version") == RESTART_REQUEST_VERSION
            and isinstance(issued_at, (int, float))
            and not isinstance(issued_at, bool)
            and isinstance(expires_at, (int, float))
            and not isinstance(expires_at, bool)
            and issued_at <= now < expires_at
            and issued_at < expires_at
            and secrets.compare_digest(str(payload.get("token", "")), owner.token)
        )
    except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
        return False


def clear_restart_request_if_owned(path, owner):
    """Cancel only the request for *owner*, never a newer owner's request."""
    with _RESTART_REQUEST_LOCK:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") == RESTART_REQUEST_VERSION and secrets.compare_digest(
                str(payload.get("token", "")), owner.token
            ):
                path.unlink(missing_ok=True)
                return True
        except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
            pass
    return False


def clear_if_owned(path, owner):
    if read_lock(path) == owner:
        path.unlink(missing_ok=True)
