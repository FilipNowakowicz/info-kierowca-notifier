"""Small, persisted exponential backoff for unattended retry loops.

The state deliberately contains only timestamps and a reason.  It is safe to
share between a detached relogin helper and the notifier that launches it, and
is intentionally generic so another authentication flow can reuse it later.
"""
import json
import os
import time
from pathlib import Path


class RetryBackoff:
    """Persist failures and decide whether an automatic attempt may start."""

    VERSION = 1
    INITIAL_DELAY_SECONDS = 60
    MAX_DELAY_SECONDS = 3600
    MAX_STATE_AGE_SECONDS = 7 * 24 * 3600

    def __init__(self, path, *, clock=time.time, initial_delay=INITIAL_DELAY_SECONDS,
                 max_delay=MAX_DELAY_SECONDS, max_state_age=MAX_STATE_AGE_SECONDS):
        self.path = Path(path)
        self.clock = clock
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.max_state_age = max_state_age

    def _load(self):
        """Return validated state, discarding malformed or stale state safely."""
        try:
            now = self.clock()
            with self.path.open(encoding="utf-8") as f:
                state = json.load(f)
            count = state["failure_count"]
            last_failure_at = state["last_failure_at"]
            next_attempt_at = state["next_attempt_at"]
            if (state.get("version") != self.VERSION or isinstance(count, bool)
                    or not isinstance(count, int) or count < 1
                    or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                               for value in (last_failure_at, next_attempt_at))
                    or next_attempt_at < last_failure_at
                    or last_failure_at > now + self.max_delay
                    or now - last_failure_at > self.max_state_age
                    or next_attempt_at - last_failure_at > self.max_delay):
                raise ValueError("invalid or stale backoff state")
            return state
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self.clear()
            return None

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as f:
                json.dump(state, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def state(self):
        """Return a copy of the valid state, if an automatic retry is delayed."""
        state = self._load()
        return dict(state) if state else None

    def cooldown_remaining(self, *, manual=False):
        """Seconds remaining before an automatic retry; manual attempts bypass it."""
        if manual:
            return 0
        state = self._load()
        if not state:
            return 0
        return max(0, state["next_attempt_at"] - self.clock())

    def record_failure(self, reason):
        """Record one automatic failure and return its cooldown in seconds."""
        previous = self._load()
        count = (previous["failure_count"] if previous else 0) + 1
        delay = min(self.initial_delay * (2 ** (count - 1)), self.max_delay)
        now = self.clock()
        self._save({
            "version": self.VERSION,
            "failure_count": count,
            "last_failure_at": now,
            "next_attempt_at": now + delay,
            "last_failure_reason": str(reason),
        })
        return delay

    def record_success(self):
        """A completed relogin makes the next automatic retry immediately eligible."""
        self.clear()

    def clear(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
