import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from info_kierowca_notifier.auth import session as auto_refresh_session


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class AttachedChromeTests(unittest.TestCase):
    def attached(self, clock):
        return auto_refresh_session.AttachedChrome(
            "127.0.0.1", 9333, monotonic=clock.monotonic, sleep=clock.sleep
        )

    def test_wait_returns_when_browser_is_already_gone(self):
        clock = Clock()
        with patch("info_kierowca_notifier.auth.session.cdp_client.browser_ws_url",
                   side_effect=ConnectionError):
            self.assertEqual(self.attached(clock).wait(timeout=5), 0)
        self.assertEqual(clock.now, 0)

    def test_wait_polls_until_browser_disappears(self):
        clock = Clock()
        with patch("info_kierowca_notifier.auth.session.cdp_client.browser_ws_url",
                   side_effect=["ws://live", "ws://live", ConnectionError()]):
            self.assertEqual(self.attached(clock).wait(timeout=5), 0)
        self.assertGreaterEqual(clock.now, 0.2)

    def test_wait_raises_timeout_while_browser_remains_live(self):
        clock = Clock()
        with patch("info_kierowca_notifier.auth.session.cdp_client.browser_ws_url",
                   return_value="ws://live"):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.attached(clock).wait(timeout=0.25)
        self.assertGreaterEqual(clock.now, 0.25)

    def test_timeout_does_not_claim_browser_shutdown(self):
        clock = Clock()
        chrome = self.attached(clock)
        released = Mock()
        with patch("info_kierowca_notifier.auth.session.cdp_client.browser_ws_url",
                   return_value="ws://live"):
            with self.assertRaises(subprocess.TimeoutExpired):
                chrome.wait(timeout=0.2)
            released.assert_not_called()

    def test_profile_hardening_uses_0700_on_posix(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            chmod = Mock()
            auto_refresh_session.ensure_private_profile_dir(
                profile, platform="linux", chmod=chmod
            )
        chmod.assert_called_once_with(profile, 0o700)

    def test_profile_hardening_skips_chmod_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            chmod = Mock()
            auto_refresh_session.ensure_private_profile_dir(
                profile, platform="win32", chmod=chmod
            )
        chmod.assert_not_called()

    def test_launch_arguments_bind_debugging_to_loopback(self):
        args = auto_refresh_session.chrome_debugging_args(9333, Path("profile"))
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertIn("--remote-debugging-port=9333", args)

    def test_pairing_reuses_live_profile_owner_instead_of_launching_another(self):
        target = Mock()
        provider = Mock()
        provider.find_or_create_target.return_value = target
        with patch("info_kierowca_notifier.auth.session.cdp_client.wait_for_debug_port"), \
             patch("info_kierowca_notifier.auth.session.sms_provider.GoogleMessagesWebProvider",
                   return_value=provider), \
             patch("info_kierowca_notifier.auth.session.cdp_client.bring_target_to_front"), \
             patch("info_kierowca_notifier.auth.session.subprocess.Popen") as launch:
            self.assertIs(auto_refresh_session.open_google_messages_pairing(), target)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
