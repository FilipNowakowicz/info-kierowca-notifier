import unittest
from unittest.mock import patch

from info_kierowca_notifier.auth import session as auth_session
from info_kierowca_notifier.auth.relogin_backoff import MAX_CONSECUTIVE_AUTOMATIC_FAILURES


class FakeBackoff:
    """Stands in for RetryBackoff -- only consecutive_failures() is read."""

    def __init__(self, count):
        self._count = count

    def consecutive_failures(self):
        return self._count


class MaybeNotifyReloginPausedTests(unittest.TestCase):
    """auth.session._maybe_notify_relogin_paused: the one-time "automatic
    Profil Zaufany relogin has been paused" alert fired when
    auth.launch.trigger_auto_refresh()'s MAX_CONSECUTIVE_AUTOMATIC_FAILURES
    gate is first crossed (see launch.py's own tests for the gate itself)."""

    def test_fires_exactly_on_the_threshold_crossing_for_profil_zaufany(self):
        with patch.object(auth_session, "notify_desktop") as desktop, \
                patch.object(auth_session, "push_ntfy") as push:
            auth_session._maybe_notify_relogin_paused(
                FakeBackoff(MAX_CONSECUTIVE_AUTOMATIC_FAILURES), "profil_zaufany"
            )
        desktop.assert_called_once()
        push.assert_called_once()

    def test_does_not_fire_below_the_threshold(self):
        with patch.object(auth_session, "notify_desktop") as desktop, \
                patch.object(auth_session, "push_ntfy") as push:
            auth_session._maybe_notify_relogin_paused(
                FakeBackoff(MAX_CONSECUTIVE_AUTOMATIC_FAILURES - 1), "profil_zaufany"
            )
        desktop.assert_not_called()
        push.assert_not_called()

    def test_does_not_fire_again_past_the_threshold(self):
        # Only fires exactly at the crossing -- see the function's own
        # docstring for why this can't double-fire in practice (no further
        # automatic attempt can run once trigger_auto_refresh() is paused).
        with patch.object(auth_session, "notify_desktop") as desktop, \
                patch.object(auth_session, "push_ntfy") as push:
            auth_session._maybe_notify_relogin_paused(
                FakeBackoff(MAX_CONSECUTIVE_AUTOMATIC_FAILURES + 1), "profil_zaufany"
            )
        desktop.assert_not_called()
        push.assert_not_called()

    def test_never_fires_for_mobywatel(self):
        # A QR scan never submits a password or a one-time code, so
        # mobywatel carries none of the account-lockout risk this gate
        # exists for -- and trigger_auto_refresh() never gates it either.
        with patch.object(auth_session, "notify_desktop") as desktop, \
                patch.object(auth_session, "push_ntfy") as push:
            auth_session._maybe_notify_relogin_paused(
                FakeBackoff(MAX_CONSECUTIVE_AUTOMATIC_FAILURES), "mobywatel"
            )
        desktop.assert_not_called()
        push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
