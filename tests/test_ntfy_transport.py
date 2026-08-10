import ssl
import urllib.error
import unittest
from unittest import mock

import info_kierowca_notifier.app as app
from info_kierowca_notifier.auth import session as auto_refresh_session
from info_kierowca_notifier import ntfy_transport
from info_kierowca_notifier import notifier
from info_kierowca_notifier.booking import reschedule as open_logged_in_browser
from info_kierowca_notifier import tls_transport


class _Response:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class NtfyTransportTests(unittest.TestCase):
    def test_success_requires_a_2xx_response(self):
        with mock.patch("info_kierowca_notifier.ntfy_transport.tls_transport.urlopen", return_value=_Response(201)) as urlopen:
            outcome = ntfy_transport.push_ntfy("private-topic", "Title", "Body", priority="urgent")

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.http_status, 201)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Title"), "Title")
        self.assertEqual(request.get_header("Priority"), "urgent")

    def test_non_2xx_response_is_not_a_success(self):
        with mock.patch("info_kierowca_notifier.ntfy_transport.tls_transport.urlopen", return_value=_Response(503)):
            outcome = ntfy_transport.push_ntfy("private-topic", "Title", "Body")

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.kind, ntfy_transport.HTTP_ERROR)
        self.assertEqual(outcome.http_status, 503)

    def test_http_error_has_safe_status_only(self):
        error = urllib.error.HTTPError("https://ntfy.sh/private-topic", 401, "no", {}, None)
        with mock.patch("info_kierowca_notifier.ntfy_transport.tls_transport.urlopen", side_effect=error):
            outcome = ntfy_transport.push_ntfy("private-topic", "Title", "Body")

        self.assertEqual(outcome.kind, ntfy_transport.HTTP_ERROR)
        self.assertEqual(outcome.detail, "ntfy returned HTTP 401.")
        self.assertNotIn("private-topic", outcome.detail)

    def test_network_failure_is_distinguished_without_leaking_topic(self):
        failure = urllib.error.URLError("https://ntfy.sh/private-topic refused connection")
        with mock.patch("info_kierowca_notifier.ntfy_transport.tls_transport.urlopen", side_effect=failure):
            outcome = ntfy_transport.push_ntfy("private-topic", "Title", "Body")

        self.assertEqual(outcome.kind, ntfy_transport.NETWORK_ERROR)
        self.assertNotIn("private-topic", outcome.detail)

    def test_tls_verification_failure_is_distinguished(self):
        failure = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        with mock.patch("info_kierowca_notifier.ntfy_transport.tls_transport.urlopen", side_effect=failure):
            outcome = ntfy_transport.push_ntfy("private-topic", "Title", "Body")

        self.assertEqual(outcome.kind, ntfy_transport.TLS_ERROR)
        self.assertIn("certificate verification", outcome.detail)

    def test_tls_configuration_failure_is_actionable(self):
        with mock.patch(
            "info_kierowca_notifier.ntfy_transport.tls_transport.urlopen",
            side_effect=tls_transport.TLSConfigurationError("/private/ca.pem"),
        ):
            outcome = ntfy_transport.push_ntfy("private-topic", "Title", "Body")

        self.assertEqual(outcome.kind, ntfy_transport.TLS_CONFIGURATION_ERROR)
        self.assertNotIn("/private/ca.pem", outcome.detail)

    def test_notifier_logs_safe_structured_failure(self):
        logger = mock.Mock()
        with mock.patch(
            "info_kierowca_notifier.notifier.ntfy_transport.push_ntfy",
            return_value=ntfy_transport.NotificationOutcome(ntfy_transport.NETWORK_ERROR, "safe detail"),
        ):
            outcome = notifier.push_ntfy(logger, "private-topic", "Title", "PKK 123")

        self.assertEqual(outcome.kind, ntfy_transport.NETWORK_ERROR)
        logged = " ".join(str(value) for call in logger.info.call_args_list for value in call.args)
        self.assertNotIn("private-topic", logged)
        self.assertNotIn("PKK 123", logged)

    def test_other_notification_callers_delegate_to_shared_transport(self):
        success = ntfy_transport.NotificationOutcome(ntfy_transport.SUCCESS, "ok", 200)
        with mock.patch("info_kierowca_notifier.auth.session.ntfy_transport.push_ntfy", return_value=success) as refresh_push:
            config_file = mock.Mock()
            config_file.read_text.return_value = '{"ntfy_topic": "topic"}'
            with mock.patch("info_kierowca_notifier.auth.session.CONFIG_FILE", config_file):
                self.assertIs(auto_refresh_session.push_ntfy("Title", "Body"), success)
        refresh_push.assert_called_once()

        with mock.patch("info_kierowca_notifier.booking.reschedule.ntfy_transport.push_ntfy", return_value=success) as browser_push:
            self.assertIs(open_logged_in_browser.push_ntfy("topic", "Title", "Body"), success)
        browser_push.assert_called_once()


class TestPushApiTests(unittest.TestCase):
    def _handler(self, payload):
        handler = object.__new__(app.AppHandler)
        handler._read_json_body = mock.Mock(return_value=payload)
        handler._send_json = mock.Mock()
        return handler

    def test_api_reports_success_only_after_delivery(self):
        handler = self._handler({"topic": "private-topic"})
        with mock.patch(
            "info_kierowca_notifier.app.notifier.push_ntfy",
            return_value=ntfy_transport.NotificationOutcome(ntfy_transport.SUCCESS, "accepted", 200),
        ):
            handler._handle_test_push()

        handler._send_json.assert_called_once_with(200, {"ok": True})

    def test_api_propagates_actionable_push_failure(self):
        handler = self._handler({"topic": "private-topic"})
        outcome = ntfy_transport.NotificationOutcome(
            ntfy_transport.TLS_ERROR, "TLS certificate verification failed. Check your system trust settings."
        )
        with mock.patch("info_kierowca_notifier.app.notifier.push_ntfy", return_value=outcome):
            handler._handle_test_push()

        handler._send_json.assert_called_once_with(
            502, {"ok": False, "error": outcome.detail, "reason": ntfy_transport.TLS_ERROR}
        )


if __name__ == "__main__":
    unittest.main()
