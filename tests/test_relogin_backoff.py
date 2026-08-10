import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from info_kierowca_notifier.auth import launch as auth_launch
from info_kierowca_notifier.auth.relogin_backoff import RetryBackoff


class FakeClock:
    def __init__(self, now=1000):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RetryBackoffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "backoff.json"
        self.clock = FakeClock()
        self.backoff = RetryBackoff(
            self.path, clock=self.clock, initial_delay=10, max_delay=40, max_state_age=100
        )

    def test_first_failure_starts_short_cooldown(self):
        self.assertEqual(self.backoff.record_failure("browser_closed"), 10)
        self.assertEqual(self.backoff.cooldown_remaining(), 10)
        self.assertEqual(self.backoff.state()["last_failure_reason"], "browser_closed")

    def test_repeated_failures_escalate_and_cap(self):
        self.assertEqual(self.backoff.record_failure("failed"), 10)
        self.clock.advance(10)
        self.assertEqual(self.backoff.record_failure("failed"), 20)
        self.clock.advance(20)
        self.assertEqual(self.backoff.record_failure("failed"), 40)
        self.clock.advance(40)
        self.assertEqual(self.backoff.record_failure("failed"), 40)

    def test_success_resets_persisted_state(self):
        self.backoff.record_failure("failed")
        self.assertTrue(self.path.exists())
        self.backoff.record_success()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.backoff.cooldown_remaining(), 0)

    def test_manual_attempt_bypasses_backoff(self):
        self.backoff.record_failure("failed")
        self.assertEqual(self.backoff.cooldown_remaining(), 10)
        self.assertEqual(self.backoff.cooldown_remaining(manual=True), 0)

    def test_state_survives_restart(self):
        self.backoff.record_failure("failed")
        restarted = RetryBackoff(
            self.path, clock=self.clock, initial_delay=10, max_delay=40, max_state_age=100
        )
        self.assertEqual(restarted.cooldown_remaining(), 10)

    def test_corrupt_stale_or_future_state_is_discarded(self):
        self.path.write_text("not json", encoding="utf-8")
        self.assertEqual(self.backoff.cooldown_remaining(), 0)
        self.assertFalse(self.path.exists())
        self.path.write_text(json.dumps({
            "version": 1, "failure_count": 1, "last_failure_at": 800,
            "next_attempt_at": 810,
        }), encoding="utf-8")
        self.assertEqual(self.backoff.cooldown_remaining(), 0)
        self.path.write_text(json.dumps({
            "version": 1, "failure_count": 1, "last_failure_at": 2000,
            "next_attempt_at": 2010,
        }), encoding="utf-8")
        self.assertEqual(self.backoff.cooldown_remaining(), 0)


class TriggerAutoRefreshBackoffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_path = Path(self.directory.name) / "relogin-backoff.json"
        self.logger = Mock()
        self.path_patch = patch.object(auth_launch, "RELOGIN_BACKOFF_FILE", self.state_path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.log_patch = patch.object(
            auth_launch, "AUTO_REFRESH_LOG_FILE", Path(self.directory.name) / "auto-refresh.log"
        )
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.lock_path = Path(self.directory.name) / "auto-refresh.lock"
        self.lock_patch = patch.object(auth_launch, "AUTO_REFRESH_LOCK", self.lock_path)
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)

    def test_automatic_attempt_stops_during_persisted_cooldown(self):
        RetryBackoff(self.state_path).record_failure("authentication_timeout")
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available") as available:
            outcome = auth_launch.trigger_auto_refresh(self.logger, {})
        self.assertEqual(outcome, auth_launch.TRIGGER_BACKOFF_ACTIVE)
        available.assert_not_called()

    def test_manual_attempt_bypasses_persisted_cooldown(self):
        RetryBackoff(self.state_path).record_failure("authentication_timeout")
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=False):
            outcome = auth_launch.trigger_auto_refresh(self.logger, {}, force=True)
        self.assertEqual(outcome, auth_launch.TRIGGER_NO_BROWSER)

    def test_automatic_browser_failure_is_persisted(self):
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=False):
            outcome = auth_launch.trigger_auto_refresh(self.logger, {})
        self.assertEqual(outcome, auth_launch.TRIGGER_NO_BROWSER)
        state = RetryBackoff(self.state_path).state()
        self.assertEqual(state["failure_count"], 1)
        self.assertEqual(state["last_failure_reason"], "browser_unavailable")

    def test_automatic_launch_marks_helper_and_records_launch_failure(self):
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch.object(
            auth_launch, "AUTO_REFRESH_SCRIPT", Path(__file__)
        ), patch("info_kierowca_notifier.auth.launch.shutil.which", return_value=None), patch(
            "info_kierowca_notifier.auth.launch.subprocess.Popen", side_effect=OSError("cannot launch")
        ):
            outcome = auth_launch.trigger_auto_refresh(self.logger, {})
        self.assertEqual(outcome, auth_launch.TRIGGER_LAUNCH_FAILED)
        self.assertEqual(
            RetryBackoff(self.state_path).state()["last_failure_reason"], "launch_failed"
        )

    def test_automatic_launch_passes_internal_automatic_marker(self):
        process = Mock()
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch.object(
            auth_launch, "AUTO_REFRESH_SCRIPT", Path(__file__)
        ), patch("info_kierowca_notifier.auth.launch.shutil.which", return_value=None), patch(
            "info_kierowca_notifier.auth.launch.subprocess.Popen", return_value=process
        ) as popen:
            outcome = auth_launch.trigger_auto_refresh(self.logger, {})
        self.assertEqual(outcome, auth_launch.TRIGGER_LAUNCHED)
        self.assertIn("--automatic", popen.call_args.args[0])

    def test_manual_launch_has_explicit_outcome(self):
        process = Mock()
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch.object(
            auth_launch, "AUTO_REFRESH_SCRIPT", Path(__file__)
        ), patch("info_kierowca_notifier.auth.launch.subprocess.Popen", return_value=process):
            outcome = auth_launch.trigger_auto_refresh(self.logger, {}, force=True)
        self.assertEqual(outcome, auth_launch.TRIGGER_MANUAL_RETRY_LAUNCHED)

    def test_manual_retry_never_terminates_live_relogin(self):
        self.lock_path.write_text("123", encoding="utf-8")
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", return_value=True
        ) as kill, patch("info_kierowca_notifier.auth.launch.subprocess.Popen") as popen:
            outcome = auth_launch.trigger_auto_refresh(self.logger, {}, force=True)
        self.assertEqual(outcome, auth_launch.TRIGGER_ALREADY_RUNNING)
        kill.assert_called_once_with(123)
        popen.assert_not_called()
        self.assertTrue(self.lock_path.exists())

    def test_manual_retry_removes_dead_lock_before_launching(self):
        self.lock_path.write_text("123", encoding="utf-8")
        process = Mock()
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch.object(
            auth_launch, "AUTO_REFRESH_SCRIPT", Path(__file__)
        ), patch("info_kierowca_notifier.auth.launch.shutil.which", return_value=None), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", return_value=False
        ), patch("info_kierowca_notifier.auth.launch.subprocess.Popen", return_value=process):
            outcome = auth_launch.trigger_auto_refresh(self.logger, {}, force=True)
        self.assertEqual(outcome, auth_launch.TRIGGER_MANUAL_RETRY_LAUNCHED)
        self.assertFalse(self.lock_path.exists())

    def test_manual_retry_removes_malformed_lock_before_launching(self):
        self.lock_path.write_text("not-a-pid", encoding="utf-8")
        process = Mock()
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch.object(
            auth_launch, "AUTO_REFRESH_SCRIPT", Path(__file__)
        ), patch("info_kierowca_notifier.auth.launch.shutil.which", return_value=None), patch(
            "info_kierowca_notifier.auth.launch.subprocess.Popen", return_value=process
        ):
            outcome = auth_launch.trigger_auto_refresh(self.logger, {}, force=True)
        self.assertEqual(outcome, auth_launch.TRIGGER_MANUAL_RETRY_LAUNCHED)
        self.assertFalse(self.lock_path.exists())


if __name__ == "__main__":
    unittest.main()
