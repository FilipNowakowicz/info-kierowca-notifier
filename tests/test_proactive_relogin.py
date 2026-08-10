import unittest

from info_kierowca_notifier import notifier


class ProactiveReloginTests(unittest.TestCase):
    def config(self, method="profil_zaufany", enabled=True):
        return {"login_method": method, "auto_refresh_chrome": enabled}

    def test_pz_relogin_starts_at_five_minute_boundary(self):
        captured_at = 1_000
        expires_at = captured_at + notifier.SESSION_ESTIMATED_LIFETIME_SECONDS
        self.assertFalse(notifier.should_proactively_relogin(
            self.config(), captured_at,
            now=expires_at - notifier.PZ_PROACTIVE_RELOGIN_LEAD_SECONDS - 1,
        ))
        self.assertTrue(notifier.should_proactively_relogin(
            self.config(), captured_at,
            now=expires_at - notifier.PZ_PROACTIVE_RELOGIN_LEAD_SECONDS,
        ))
        self.assertTrue(notifier.should_proactively_relogin(
            self.config(), captured_at, now=expires_at - 1,
        ))

    def test_expired_session_uses_existing_api_failure_path(self):
        captured_at = 1_000
        expires_at = captured_at + notifier.SESSION_ESTIMATED_LIFETIME_SECONDS
        self.assertFalse(notifier.should_proactively_relogin(
            self.config(), captured_at, now=expires_at,
        ))

    def test_mobywatel_and_disabled_automation_do_not_start_early(self):
        captured_at = 1_000
        now = captured_at + notifier.SESSION_ESTIMATED_LIFETIME_SECONDS - 60
        self.assertFalse(notifier.should_proactively_relogin(
            self.config(method="mobywatel"), captured_at, now=now,
        ))
        self.assertFalse(notifier.should_proactively_relogin(
            self.config(enabled=False), captured_at, now=now,
        ))

    def test_missing_or_invalid_capture_time_does_not_start_early(self):
        for captured_at in (None, True, "1000"):
            with self.subTest(captured_at=captured_at):
                self.assertFalse(notifier.should_proactively_relogin(
                    self.config(), captured_at, now=1_000,
                ))


if __name__ == "__main__":
    unittest.main()
