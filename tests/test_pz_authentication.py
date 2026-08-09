import io
import logging
import unittest
import auth_providers
from auth_providers import AuthenticationFailure, PZState, ProfilZaufanyProvider
from sms_provider import SMSResult


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
    def submit_otp(self, code): self.submitted.append(("otp", code))
    def redirect_status(self): return self._get("redirect", "redirected")
    def session_cookies(self): return self._get("cookies", {"a": "b"})


class FakeSMS:
    def __init__(self, result=None): self.result = result or SMSResult("found", "87654321", 1_800_001_000)
    def get_latest_pz_code(self, after, used): return self.result


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
    def test_auth_target_disappears(self):
        import cdp_client
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
    def test_rejects_unexpected_origin(self):
        self.assertFalse(auth_providers.allowed_pz_origin("https://evil.example/login.gov.pl"))
        self.assertFalse(auth_providers.allowed_pz_origin("http://login.gov.pl/x"))

    def test_credentials_and_otp_are_never_injected_on_unexpected_origin(self):
        import cdp_client
        from unittest.mock import Mock, patch
        target = cdp_client.PageTarget("auth", "https://unrelated.example/", "", "ws")
        browser = auth_providers.CDPProfilZaufanyBrowser("h", 1, target, Mock(), "https://info-kierowca.pl/login")
        with patch("auth_providers.cdp_client.get_page_target", return_value=target), \
             patch("auth_providers.cdp_client.call_function_in_target") as call:
            self.assertRaises(AuthenticationFailure, browser.submit_credentials, "user", "secret")
            self.assertRaises(AuthenticationFailure, browser.submit_otp, "87654321")
            call.assert_not_called()
