"""Cooperative control protocol for the long-running QR relogin helper.

The lock identifies the helper, while the restart request lets that helper
shut down its own Chrome process. The dashboard never sends a signal to a PID
read from disk: if the helper cannot acknowledge the request, a replacement is
not started against the same profile/debug port.
"""

import json
import os
import secrets
from dataclasses import dataclass


LOCK_VERSION = 1


@dataclass(frozen=True)
class ReloginOwner:
    pid: int
    token: str


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


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


def process_alive(pid, probe=None):
    probe = probe or os.kill
    try:
        probe(pid, 0)
        return True
    except OSError:
        return False


def lock_pid(path):
    owner = read_lock(path)
    return owner.pid if owner else read_legacy_pid(path)


def write_restart_request(path, owner):
    _atomic_write(path, {"version": LOCK_VERSION, "token": owner.token})


def restart_requested(path, owner):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("version") == LOCK_VERSION and secrets.compare_digest(
            str(payload.get("token", "")), owner.token
        )
    except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
        return False


def clear_if_owned(path, owner):
    if read_lock(path) == owner:
        path.unlink(missing_ok=True)
