import io
import logging
import unittest
from info_kierowca_notifier.auth import providers as auth_providers
from info_kierowca_notifier.auth.providers import AuthenticationFailure, PZState, ProfilZaufanyProvider
from info_kierowca_notifier.auth.sms import SMSResult


class FakeClock:
    def __init__(self): self.value = 1000.0
    def monotonic(self): return self.value
    def time(self): return 1_800_000_000 + self.value
    def sleep(self, seconds): self.value += seconds


class FakeBrowser:
    def __init__(self, **outcomes):
        self.outcomes = outcomes; self.submitted = []; self.opened = False
    def closed(self): return self.outcomes.get("closed", False)
    def open_login(self): self.opened = True
    def _get(self, name, default):
        value = self.outcomes.get(name, default)
        if isinstance(value, list): return value.pop(0) if value else None
        if isinstance(value, Exception): raise value
        return value
    def select_identity_provider(self): return self._get("identity", "clicked")
    def select_profil_zaufany(self): return self._get("chooser", "clicked")
    def credential_form_status(self): return self._get("form", "ready")
    def submit_credentials(self, username, password): self.submitted.append(("credentials", username, password))
    def challenge_status(self): return self._get("challenge", "sms")
    def enter_otp(self, code): self.submitted.append(("otp_entered", code))
    def confirm_otp(self, code): self.submitted.append(("otp", code))
    def redirect_status(self): return self._get("redirect", "redirected")
    def session_cookies(self): return self._get("cookies", {"a": "b"})


class FakeSMS:
    def __init__(self, result=None, results=None):
        self.result = result or SMSResult("found", "87654321", 1_800_001_000)
        self.results = (list(results) if results is not None
                        else [SMSResult("no_current_otp")])
    def get_latest_pz_code(self, after, used, tolerance_seconds=30):
        if self.results:
            return self.results.pop(0)
        return self.result


class PZAuthenticationTests(unittest.TestCase):
    def provider(self, browser=None, sms=None, **kwargs):
        clock = FakeClock()
        timeouts = {state: 1 for state in auth_providers.DEFAULT_TIMEOUTS}
        overall_timeout = kwargs.pop("overall_timeout", 20)
        return ProfilZaufanyProvider(browser or FakeBrowser(), sms or FakeSMS(), "person", "SUPER_SECRET_PZ_PASSWORD_123",
            monotonic=clock.monotonic, wall_clock=clock.time, sleep=clock.sleep,
            timeouts=timeouts, overall_timeout=overall_timeout, logger=lambda _m: None, **kwargs)

    def reason(self, browser=None, sms=None, **kwargs):
        with self.assertRaises(AuthenticationFailure) as caught:
            self.provider(browser, sms, **kwargs).authenticate()
        return caught.exception.reason

    def test_happy_path(self):
        provider = self.provider()
        self.assertEqual(provider.authenticate(), {"a": "b"})
        self.assertEqual(provider.state, PZState.SUCCESS)
        self.assertTrue(provider.browser.opened)

    def test_codes_visible_before_credential_submission_are_not_reused(self):
        old = SMSResult("found", "11111111", 1_800_000_998)
        old_two = SMSResult("found", "22222222", 1_800_000_999)
        fresh = SMSResult("found", "87654321", 1_800_001_000)
        provider = self.provider(sms=FakeSMS(results=[
            old_two, old, SMSResult("no_current_otp"), fresh,
        ]))
        provider.authenticate()
        self.assertIn(("otp", "87654321"), provider.browser.submitted)
        self.assertNotIn(("otp", "11111111"), provider.browser.submitted)
        self.assertNotIn(("otp", "22222222"), provider.browser.submitted)

    def test_snapshot_waits_for_messages_conversation_to_finish_switching(self):
        old = SMSResult("found", "11111111", 1_800_000_999)
        fresh = SMSResult("found", "87654321", 1_800_001_000)
        provider = self.provider(sms=FakeSMS(results=[
            SMSResult("switching"), old, SMSResult("no_current_otp"), fresh,
        ]))
        provider.authenticate()
        self.assertIn(("otp", "87654321"), provider.browser.submitted)
        self.assertNotIn(("otp", "11111111"), provider.browser.submitted)

    def test_sms_wait_uses_credential_submission_timestamp_and_conservative_tolerance(self):
        class RecordingSMS(FakeSMS):
            def __init__(self):
                super().__init__()
                self.calls = []
            def get_latest_pz_code(self, after, used, tolerance_seconds=30):
                self.calls.append((after, tolerance_seconds))
                return super().get_latest_pz_code(after, used, tolerance_seconds)

        sms = RecordingSMS()
        self.provider(sms=sms).authenticate()
        self.assertEqual(
            sms.calls[-1][1], auth_providers.PZ_SMS_ATTEMPT_TOLERANCE_SECONDS
        )
        self.assertEqual(sms.calls[-1][0], 1_800_001_000)

    def test_unexpected_messages_origin_is_target_lost(self):
        self.assertEqual(
            self.reason(sms=FakeSMS(SMSResult("unexpected_messages_origin"))),
            "messages_target_lost",
        )

    def test_otp_submission_has_bounded_settlement_delay(self):
        provider = self.provider(otp_settle_delay=5)
        provider.authenticate()
        self.assertGreaterEqual(provider.monotonic(), 1005)

    def test_developer_otp_confirmation_pause_is_fake_clock_bounded(self):
        provider = self.provider(otp_confirm_delay=20, overall_timeout=40)
        provider.authenticate()
        self.assertGreaterEqual(provider.monotonic(), 1025)

    def test_identity_chooser_timeout(self): self.assertEqual(self.reason(FakeBrowser(identity=None)), "identity_provider_timeout")
    def test_profil_chooser_timeout(self): self.assertEqual(self.reason(FakeBrowser(chooser=None)), "profil_zaufany_chooser_timeout")
    def test_credential_form_timeout(self): self.assertEqual(self.reason(FakeBrowser(form=None)), "credential_form_timeout")
    def test_wrong_credentials(self): self.assertEqual(self.reason(FakeBrowser(challenge="invalid_credentials")), "invalid_credentials")
    def test_transaction_expired(self): self.assertEqual(self.reason(FakeBrowser(form="expired")), "auth_transaction_expired")
    def test_sms_challenge_timeout(self): self.assertEqual(self.reason(FakeBrowser(challenge=None)), "sms_challenge_timeout")
    def test_messages_unavailable(self): self.assertEqual(self.reason(sms=FakeSMS(SMSResult("not_paired"))), "sms_provider_unavailable")
    def test_messages_target_lost(self): self.assertEqual(self.reason(sms=FakeSMS(SMSResult("messages_target_lost"))), "messages_target_lost")
    def test_sms_timeout(self): self.assertEqual(self.reason(sms=FakeSMS(SMSResult("no_current_otp"))), "sms_timeout")
    def test_stale_sms(self): self.assertEqual(self.reason(sms=FakeSMS(SMSResult("stale_otp"))), "stale_sms")
    def test_otp_rejection(self): self.assertEqual(self.reason(FakeBrowser(redirect="otp_rejected")), "otp_rejected")
    def test_inactive_profil_zaufany(self):
        self.assertEqual(
            self.reason(FakeBrowser(redirect="no_valid_profile")),
            "profil_zaufany_inactive",
        )
    def test_auth_target_disappears(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        self.assertEqual(self.reason(FakeBrowser(form=cdp_client.StaleTargetError("gone"))), "auth_target_lost")
    def test_browser_closing(self): self.assertEqual(self.reason(FakeBrowser(closed=True)), "browser_closed")
    def test_redirect_timeout(self): self.assertEqual(self.reason(FakeBrowser(redirect=None)), "auth_redirect_timeout")
    def test_session_capture_timeout(self): self.assertEqual(self.reason(FakeBrowser(cookies=None)), "session_capture_timeout")
    def test_global_deadline(self):
        self.assertEqual(self.reason(FakeBrowser(identity=None), overall_timeout=0.5), "global_deadline")

    def test_cooperative_restart_cancels_without_hanging(self):
        provider = self.provider(FakeBrowser(identity=None)); provider.cancelled = lambda: True
        with self.assertRaises(AuthenticationFailure) as caught: provider.authenticate()
        self.assertEqual(caught.exception.reason, "restart_requested")

    def test_secrets_are_not_logged(self):
        stream = io.StringIO(); logger = logging.getLogger("pz-secret-test"); logger.handlers = [logging.StreamHandler(stream)]; logger.setLevel(logging.INFO)
        provider = self.provider()
        provider.logger = logger.info
        provider.authenticate()
        output = stream.getvalue()
        self.assertNotIn("SUPER_SECRET_PZ_PASSWORD_123", output)
        self.assertNotIn("87654321", output)


class OriginTests(unittest.TestCase):
    def test_allowed_login_hosts(self):
        self.assertTrue(auth_providers.allowed_pz_origin("https://login.gov.pl/x"))
        self.assertTrue(auth_providers.allowed_pz_origin("https://podmiotyzewnetrzne.login.gov.pl/x"))
        self.assertTrue(auth_providers.allowed_pz_origin("https://pz.gov.pl/ui/au/login"))
    def test_rejects_unexpected_origin(self):
        self.assertFalse(auth_providers.allowed_pz_origin("https://evil.example/login.gov.pl"))
        self.assertFalse(auth_providers.allowed_pz_origin("http://login.gov.pl/x"))
        self.assertFalse(auth_providers.allowed_pz_origin("https://notpz.gov.pl/x"))

    def test_credentials_and_otp_are_never_injected_on_unexpected_origin(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget("auth", "https://unrelated.example/", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), "https://info-kierowca.pl/login")
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.call_function_in_target") as call:
            self.assertRaises(AuthenticationFailure, browser.submit_credentials, "user", "secret")
            self.assertRaises(AuthenticationFailure, browser.enter_otp, "87654321")
            call.assert_not_called()

    def test_identity_provider_uses_live_gov_card_on_explicit_auth_target(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget("auth", "https://info-kierowca.pl/login", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), target.url)
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.call_function_in_target", return_value={"clicked": True}) as call:
            self.assertEqual(browser.select_identity_provider(), {"clicked": True})
        self.assertEqual(call.call_args.args[2].id, "auth")
        self.assertIn("mat-card.auth-card", call.call_args.args[3])
        self.assertIn("login.gov.pl", call.call_args.args[3])

    def test_credential_script_verifies_exact_values_and_scopes_submit_to_form(self):
        self.assertIn("u.value !== username", auth_providers.SUBMIT_CREDENTIALS_FUNCTION)
        self.assertIn("p.value !== password", auth_providers.SUBMIT_CREDENTIALS_FUNCTION)
        self.assertIn("var root=form || document", auth_providers.SUBMIT_CREDENTIALS_FUNCTION)

    def test_credentials_are_typed_into_the_retained_auth_target(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, call, patch
        target = cdp_client.PageTarget("auth", "https://pz.gov.pl/ui/au/login", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), target.url)
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.call_function_in_target",
                   side_effect=["ready", "ready", "submitted"]), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.insert_text_in_target") as insert:
            browser.submit_credentials("FilipNowakowicz", "secret")
        self.assertEqual(insert.call_args_list, [
            call("h", 1, target, "FilipNowakowicz"),
            call("h", 1, target, "secret"),
        ])

    def test_otp_is_typed_and_verified_in_the_retained_auth_target(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget("auth", "https://pz.gov.pl/ui/au/login", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), target.url)
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.call_function_in_target",
                   side_effect=["ready", "ready", "submitted"]), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.insert_text_in_target") as insert:
            browser.enter_otp("87654321")
            browser.confirm_otp("87654321")
        insert.assert_called_once_with("h", 1, target, "87654321")
        self.assertIn("i.value !== code", auth_providers.SUBMIT_OTP_FUNCTION)

    def test_host_suffix_matching_rejects_lookalike_hosts(self):
        matches = auth_providers._host_matches_suffix
        self.assertTrue(matches("info-kierowca.pl", "info-kierowca.pl"))
        self.assertTrue(matches("app.info-kierowca.pl", "info-kierowca.pl"))
        self.assertFalse(matches("evilinfo-kierowca.pl", "info-kierowca.pl"))
        self.assertFalse(matches("info-kierowca.pl.evil.example", "info-kierowca.pl"))

    def test_redirect_status_rejects_lookalike_info_kierowca_host(self):
        """A bare hostname.endswith("info-kierowca.pl") (the pre-fix check)
        would also match "evilinfo-kierowca.pl" and report "redirected" —
        i.e. authentication success — for a domain that was never
        info-kierowca.pl at all, before allowed_auth_redirect() ever got a
        chance to reject it."""
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget("auth", "https://evilinfo-kierowca.pl/", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), target.url)
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target):
            self.assertRaises(AuthenticationFailure, browser.redirect_status)

    def test_redirect_status_accepts_genuine_info_kierowca_subdomain(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget("auth", "https://app.info-kierowca.pl/dashboard", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), target.url)
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target):
            self.assertEqual(browser.redirect_status(), "redirected")

    def test_live_no_profile_alert_url_is_classified_without_waiting(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget(
            "auth",
            "https://pz.gov.pl/ui/au/alert?case=pzip-wk-login-no-profile&processId=safe",
            "", "ws",
        )
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), target.url)
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.call_function_in_target") as evaluate:
            self.assertEqual(browser.redirect_status(), "no_valid_profile")
        evaluate.assert_not_called()

    def test_profil_chooser_waits_during_allowed_cross_origin_navigation(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget("auth", "https://info-kierowca.pl/login", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), target.url)
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.call_function_in_target") as call:
            self.assertIsNone(browser.select_profil_zaufany())
            call.assert_not_called()

    def test_identity_provider_waits_for_initial_target_navigation(self):
        from info_kierowca_notifier.browser import cdp as cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget("auth", "chrome://newtab/", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser(
            "h", 1, target, Mock(), "https://info-kierowca.pl/login"
        )
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=target), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.call_function_in_target") as call:
            self.assertIsNone(browser.select_identity_provider())
            call.assert_not_called()

        empty_target = cdp_client.PageTarget("auth", "", "", "ws")
        with patch("info_kierowca_notifier.auth.providers.cdp_client.get_page_target", return_value=empty_target), \
             patch("info_kierowca_notifier.auth.providers.cdp_client.call_function_in_target") as call:
            self.assertIsNone(browser.select_identity_provider())
            call.assert_not_called()
