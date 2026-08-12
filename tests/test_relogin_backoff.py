import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from info_kierowca_notifier.auth import launch as auth_launch
from info_kierowca_notifier.auth.relogin_backoff import (
    MAX_CONSECUTIVE_AUTOMATIC_FAILURES,
    RetryBackoff,
)


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

    def test_consecutive_failures_tracks_count_and_resets_on_success(self):
        self.assertEqual(self.backoff.consecutive_failures(), 0)
        self.backoff.record_failure("failed")
        self.assertEqual(self.backoff.consecutive_failures(), 1)
        self.clock.advance(10)
        self.backoff.record_failure("failed")
        self.assertEqual(self.backoff.consecutive_failures(), 2)
        self.backoff.record_success()
        self.assertEqual(self.backoff.consecutive_failures(), 0)

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

    def test_automatic_profil_zaufany_attempt_stops_after_consecutive_failure_cap(self):
        backoff = RetryBackoff(self.state_path)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES):
            backoff.record_failure("invalid_credentials")
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available") as available:
            outcome = auth_launch.trigger_auto_refresh(
                self.logger, {"login_method": "profil_zaufany"}
            )
        self.assertEqual(outcome, auth_launch.TRIGGER_MANUAL_REQUIRED)
        available.assert_not_called()

    def test_automatic_mobywatel_attempt_is_never_gated_by_the_failure_cap(self):
        backoff = RetryBackoff(self.state_path)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES + 2):
            backoff.record_failure("authentication_timeout")
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=False):
            outcome = auth_launch.trigger_auto_refresh(
                self.logger, {"login_method": "mobywatel"}
            )
        # Still gated by the ordinary time-based cooldown, just never by the
        # consecutive-failure cap that's specific to Profil Zaufany.
        self.assertEqual(outcome, auth_launch.TRIGGER_BACKOFF_ACTIVE)

    def test_manual_retry_bypasses_the_consecutive_failure_cap(self):
        backoff = RetryBackoff(self.state_path)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES):
            backoff.record_failure("invalid_credentials")
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=False):
            outcome = auth_launch.trigger_auto_refresh(
                self.logger, {"login_method": "profil_zaufany"}, force=True
            )
        self.assertEqual(outcome, auth_launch.TRIGGER_NO_BROWSER)

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


class AutomaticReloginPausedTests(unittest.TestCase):
    """auth_launch.automatic_relogin_paused() -- the standalone, side-effect-
    free check notifier.run_check() calls every tick to drive the
    dashboard's "manual retry required" state, independent of whether that
    tick's own check happens to call trigger_auto_refresh() at all."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_path = Path(self.directory.name) / "relogin-backoff.json"
        self.path_patch = patch.object(auth_launch, "RELOGIN_BACKOFF_FILE", self.state_path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_false_below_the_cap(self):
        backoff = RetryBackoff(self.state_path)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES - 1):
            backoff.record_failure("invalid_credentials")
        self.assertFalse(
            auth_launch.automatic_relogin_paused({"login_method": "profil_zaufany"})
        )

    def test_true_at_and_past_the_cap(self):
        backoff = RetryBackoff(self.state_path)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES):
            backoff.record_failure("invalid_credentials")
        self.assertTrue(
            auth_launch.automatic_relogin_paused({"login_method": "profil_zaufany"})
        )

    def test_false_for_mobywatel_regardless_of_failure_count(self):
        backoff = RetryBackoff(self.state_path)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES + 2):
            backoff.record_failure("authentication_timeout")
        self.assertFalse(
            auth_launch.automatic_relogin_paused({"login_method": "mobywatel"})
        )

    def test_clears_the_moment_a_success_is_recorded(self):
        backoff = RetryBackoff(self.state_path)
        for _ in range(MAX_CONSECUTIVE_AUTOMATIC_FAILURES):
            backoff.record_failure("invalid_credentials")
        config = {"login_method": "profil_zaufany"}
        self.assertTrue(auth_launch.automatic_relogin_paused(config))
        backoff.record_success()
        self.assertFalse(auth_launch.automatic_relogin_paused(config))


if __name__ == "__main__":
    unittest.main()
