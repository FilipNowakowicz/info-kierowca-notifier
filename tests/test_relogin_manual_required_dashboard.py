"""notifier.run_check() surfacing auth_launch.automatic_relogin_paused() on
the dashboard every tick, independent of that tick's own outcome -- see
run_check()'s own comment on dash_status["relogin_manual_required"]."""
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from info_kierowca_notifier import client, notifier
from info_kierowca_notifier.auth import launch as auth_launch
from info_kierowca_notifier.auth.relogin_backoff import (
    MAX_CONSECUTIVE_AUTOMATIC_FAILURES,
    RetryBackoff,
)
from info_kierowca_notifier.booking import launch as booking_launch


def config_dict(**overrides):
    # run_check() only reads this back via load_json() -- no validation
    # happens on the read path, so this is written directly rather than
    # through app.build_config() (which has its own, unrelated
    # Profil-Zaufany-credential requirements not relevant here).
    value = {
        "login_method": "profil_zaufany",
        "profile_number": "PKK-1",
        "ntfy_topic": "test-topic",
        "organization_ids": [26],
        "exam_types": ["Theoretical"],
        "category": 5,
        "current_slot_date": "2026-09-30",
        "poll_interval_seconds": 60,
        "earliest_slot_hour": 0,
        "latest_slot_hour": 24,
    }
    value.update(overrides)
    return value


class ReloginManualRequiredDashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.config_file = root / "config.json"
        self.session_file = root / "session.json"
        self.pause_file = root / "pause"
        self.backoff_file = root / "relogin-backoff.json"
        self.backoff_patch = patch.object(auth_launch, "RELOGIN_BACKOFF_FILE", self.backoff_file)
        self.backoff_patch.start()
        self.addCleanup(self.backoff_patch.stop)

    def _run_check(self, status=None):
        status = status if status is not None else {"paused": False}
        with patch.object(notifier, "CONFIG_FILE", self.config_file), \
                patch.object(notifier, "SESSION_FILE", self.session_file), \
                patch.object(notifier, "PAUSE_FILE", self.pause_file), \
                patch.object(booking_launch, "trigger_open_browser"), \
                patch.object(notifier, "push_ntfy"), \
                patch.object(auth_launch, "trigger_auto_refresh") as trigger:
            notifier.run_check(logging.getLogger("test"), status)
        return status, trigger

    def test_flag_is_true_once_the_cap_is_reached_even_with_no_session_file(self):
        self.config_file.write_text(json.dumps(config_dict()))
        backoff = RetryBackoff(self.backoff_file)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES):
            backoff.record_failure("invalid_credentials")
        status, _trigger = self._run_check()
        self.assertTrue(status["relogin_manual_required"])

    def test_flag_is_false_below_the_cap(self):
        self.config_file.write_text(json.dumps(config_dict()))
        backoff = RetryBackoff(self.backoff_file)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES - 1):
            backoff.record_failure("invalid_credentials")
        status, _trigger = self._run_check()
        self.assertFalse(status["relogin_manual_required"])

    def test_flag_stays_accurate_through_an_unrelated_network_error_tick(self):
        # A transient network blip must not clear (or fail to clear) this --
        # it's derived fresh from persisted backoff state every tick, not
        # latched from whichever branch happened to run this time.
        self.config_file.write_text(json.dumps(config_dict()))
        self.session_file.write_text(json.dumps({"captured_at": 0}))
        backoff = RetryBackoff(self.backoff_file)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES):
            backoff.record_failure("invalid_credentials")
        with patch.object(client, "do_request", return_value=(None, b"", {})):
            status, _trigger = self._run_check()
        self.assertEqual(status["outcome"], "network_error")
        self.assertTrue(status["relogin_manual_required"])

    def test_flag_clears_once_a_successful_relogin_is_recorded(self):
        self.config_file.write_text(json.dumps(config_dict()))
        backoff = RetryBackoff(self.backoff_file)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES):
            backoff.record_failure("invalid_credentials")
        status, _trigger = self._run_check()
        self.assertTrue(status["relogin_manual_required"])
        backoff.record_success()
        status, _trigger = self._run_check(status)
        self.assertFalse(status["relogin_manual_required"])

    def test_flag_is_always_false_for_mobywatel(self):
        self.config_file.write_text(json.dumps(config_dict(login_method="mobywatel")))
        backoff = RetryBackoff(self.backoff_file)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES + 2):
            backoff.record_failure("authentication_timeout")
        status, _trigger = self._run_check()
        self.assertFalse(status["relogin_manual_required"])


if __name__ == "__main__":
    unittest.main()
