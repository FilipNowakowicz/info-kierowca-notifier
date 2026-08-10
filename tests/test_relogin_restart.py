import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from info_kierowca_notifier.auth import session as auto_refresh_session
from info_kierowca_notifier.auth import launch as auth_launch
from info_kierowca_notifier.auth import relogin_control


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.on_sleep = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        if self.on_sleep:
            self.on_sleep()
        self.now += seconds


class ReloginRestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.lock = root / "auto-refresh.lock"
        self.request = root / "auto-refresh.restart"
        self.logger = Mock()
        self.lock_patch = patch.object(auth_launch, "AUTO_REFRESH_LOCK", self.lock)
        self.request_patch = patch.object(
            auth_launch, "AUTO_REFRESH_RESTART_REQUEST", self.request
        )
        self.lock_patch.start()
        self.request_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.addCleanup(self.request_patch.stop)

    def restart(self, clock=None):
        clock = clock or FakeClock()
        return auth_launch.restart_auto_refresh(
            self.logger, {}, timeout=0.3, clock=clock, wall_clock=clock,
            sleeper=clock.sleep
        )

    def test_normal_retry_preserves_active_login(self):
        owner = relogin_control.ReloginOwner(123, "known-owner")
        relogin_control.write_lock(self.lock, owner)
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", return_value=True
        ), patch("info_kierowca_notifier.auth.launch.subprocess.Popen") as popen:
            outcome = auth_launch.trigger_auto_refresh(self.logger, {}, force=True)
        self.assertEqual(outcome, auth_launch.TRIGGER_ALREADY_RUNNING)
        popen.assert_not_called()
        self.assertEqual(relogin_control.read_lock(self.lock), owner)

    def test_explicit_restart_waits_for_known_helper_then_launches(self):
        owner = relogin_control.ReloginOwner(123, "known-owner")
        relogin_control.write_lock(self.lock, owner)
        clock = FakeClock()
        request_seen = []
        alive = [True]

        def acknowledge():
            request_seen.append(
                relogin_control.restart_requested(self.request, owner, clock=clock)
            )
            self.lock.unlink(missing_ok=True)
            alive[0] = False

        clock.on_sleep = acknowledge
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", side_effect=lambda _pid: alive[0]
        ), patch(
            "info_kierowca_notifier.auth.launch.trigger_auto_refresh",
            return_value=auth_launch.TRIGGER_MANUAL_RETRY_LAUNCHED,
        ) as launch:
            outcome = self.restart(clock)
        self.assertEqual(outcome, auth_launch.TRIGGER_RESTART_LAUNCHED)
        self.assertEqual(request_seen, [True])
        launch.assert_called_once_with(self.logger, {}, force=True, notify_phone=False)
        self.assertFalse(self.request.exists())

    def test_dead_lock_is_cleaned_before_restart_launch(self):
        relogin_control.write_lock(
            self.lock, relogin_control.ReloginOwner(123, "dead-owner")
        )
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", return_value=False
        ), patch(
            "info_kierowca_notifier.auth.launch.trigger_auto_refresh",
            return_value=auth_launch.TRIGGER_MANUAL_RETRY_LAUNCHED,
        ) as launch:
            outcome = self.restart()
        self.assertEqual(outcome, auth_launch.TRIGGER_RESTART_LAUNCHED)
        self.assertFalse(self.lock.exists())
        launch.assert_called_once()

    def test_malformed_lock_is_cleaned_before_restart_launch(self):
        self.lock.write_text("not a lock", encoding="utf-8")
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.trigger_auto_refresh",
            return_value=auth_launch.TRIGGER_MANUAL_RETRY_LAUNCHED,
        ) as launch:
            outcome = self.restart()
        self.assertEqual(outcome, auth_launch.TRIGGER_RESTART_LAUNCHED)
        self.assertFalse(self.lock.exists())
        launch.assert_called_once()

    def test_failed_graceful_shutdown_never_starts_second_browser(self):
        relogin_control.write_lock(
            self.lock, relogin_control.ReloginOwner(123, "stuck-owner")
        )
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", return_value=True
        ), patch("info_kierowca_notifier.auth.launch.trigger_auto_refresh") as launch:
            outcome = self.restart()
        self.assertEqual(outcome, auth_launch.TRIGGER_SHUTDOWN_FAILED)
        launch.assert_not_called()
        self.assertTrue(self.lock.exists())
        self.assertFalse(self.request.exists())

    def test_live_legacy_pid_lock_is_not_used_as_kill_authority(self):
        self.lock.write_text("123", encoding="utf-8")
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", return_value=True
        ), patch("info_kierowca_notifier.auth.launch.trigger_auto_refresh") as launch:
            outcome = self.restart()
        self.assertEqual(outcome, auth_launch.TRIGGER_RESTART_UNAVAILABLE)
        launch.assert_not_called()

    def test_helper_only_accepts_its_own_restart_token(self):
        owner = relogin_control.ReloginOwner(123, "right-token")
        other = relogin_control.ReloginOwner(456, "wrong-token")
        clock = FakeClock()
        relogin_control.write_restart_request(
            self.request, owner, issued_at=clock(), expires_at=clock() + 10
        )
        self.assertTrue(relogin_control.restart_requested(self.request, owner, clock=clock))
        self.assertFalse(relogin_control.restart_requested(self.request, other, clock=clock))

    def test_expired_or_malformed_restart_request_is_rejected(self):
        owner = relogin_control.ReloginOwner(123, "owner")
        clock = FakeClock()
        relogin_control.write_restart_request(
            self.request, owner, issued_at=clock() - 1, expires_at=clock()
        )
        self.assertFalse(relogin_control.restart_requested(self.request, owner, clock=clock))
        relogin_control.write_restart_request(
            self.request, owner, issued_at=clock() + 1, expires_at=clock() + 10
        )
        self.assertFalse(relogin_control.restart_requested(self.request, owner, clock=clock))
        self.request.write_text("not json", encoding="utf-8")
        self.assertFalse(relogin_control.restart_requested(self.request, owner, clock=clock))

    def test_helper_recovering_after_timeout_cannot_honor_old_request(self):
        owner = relogin_control.ReloginOwner(123, "stuck-owner")
        relogin_control.write_lock(self.lock, owner)
        clock = FakeClock()
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", return_value=True
        ):
            self.assertEqual(self.restart(clock), auth_launch.TRIGGER_SHUTDOWN_FAILED)
        self.assertFalse(relogin_control.restart_requested(self.request, owner, clock=clock))

    def test_replacement_launch_failure_leaves_no_actionable_request(self):
        owner = relogin_control.ReloginOwner(123, "departing-owner")
        relogin_control.write_lock(self.lock, owner)
        clock = FakeClock()
        alive = [True]

        def acknowledge():
            self.lock.unlink(missing_ok=True)
            alive[0] = False

        clock.on_sleep = acknowledge
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", side_effect=lambda _pid: alive[0]
        ), patch(
            "info_kierowca_notifier.auth.launch.trigger_auto_refresh", return_value=auth_launch.TRIGGER_LAUNCH_FAILED
        ):
            self.assertEqual(self.restart(clock), auth_launch.TRIGGER_LAUNCH_FAILED)
        self.assertFalse(self.request.exists())

    def test_owner_change_cancels_old_request_and_does_not_launch(self):
        owner = relogin_control.ReloginOwner(123, "old-owner")
        replacement = relogin_control.ReloginOwner(456, "new-owner")
        relogin_control.write_lock(self.lock, owner)
        clock = FakeClock()
        clock.on_sleep = lambda: relogin_control.write_lock(self.lock, replacement)
        with patch("info_kierowca_notifier.auth.launch.chrome.chrome_available", return_value=True), patch(
            "info_kierowca_notifier.auth.launch.relogin_control.process_alive", return_value=True
        ), patch("info_kierowca_notifier.auth.launch.trigger_auto_refresh") as launch:
            outcome = self.restart(clock)
        self.assertEqual(outcome, auth_launch.TRIGGER_SHUTDOWN_FAILED)
        self.assertFalse(self.request.exists())
        launch.assert_not_called()

    def test_old_request_cannot_affect_new_helper(self):
        clock = FakeClock()
        old = relogin_control.ReloginOwner(123, "old-owner")
        new = relogin_control.ReloginOwner(456, "new-owner")
        relogin_control.write_restart_request(
            self.request, old, issued_at=clock(), expires_at=clock() + 10
        )
        self.assertFalse(relogin_control.restart_requested(self.request, new, clock=clock))
        with patch.object(auto_refresh_session, "LOCK_FILE", self.lock), patch.object(
            auto_refresh_session, "RESTART_REQUEST_FILE", self.request
        ):
            self.assertTrue(auto_refresh_session.acquire_lock(new))
        self.assertFalse(self.request.exists())

    def test_cookie_wait_honors_restart_before_browser_work(self):
        chrome = Mock()
        with self.assertRaises(auto_refresh_session.ReloginRestartRequested), patch(
            "info_kierowca_notifier.auth.session.cdp_client.fetch_cookies"
        ) as fetch:
            auto_refresh_session.wait_for_cookies(
                "127.0.0.1", 9333, None, chrome, should_restart=lambda: True
            )
        fetch.assert_not_called()
        chrome.poll.assert_not_called()

    def test_browser_forced_kill_is_waited_for_before_lock_release(self):
        source = Path(auto_refresh_session.__file__).read_text(encoding="utf-8")
        self.assertIn("not args.keep_open or restart_shutdown", source)
        self.assertIn("chrome_proc.kill()\n                chrome_proc.wait(timeout=2)", source)

    def test_dashboard_flow_exposes_deliberate_localized_restart(self):
        repository = Path(__file__).resolve().parents[1]
        package = repository / "src" / "info_kierowca_notifier"
        app_source = (package / "app.py").read_text(encoding="utf-8")
        template_source = (package / "web" / "templates.py").read_text(encoding="utf-8")
        localization_source = (package / "web" / "localization.py").read_text(encoding="utf-8")
        self.assertIn('self.path == "/relogin-restart"', app_source)
        self.assertIn("data.action === 'already_running'", template_source)
        self.assertIn("fetch('/relogin-restart'", template_source)
        self.assertIn("restartData.action !== 'restart_launched'", template_source)
        self.assertIn("A QR login is already open. Close it and restart login?", localization_source)


if __name__ == "__main__":
    unittest.main()
