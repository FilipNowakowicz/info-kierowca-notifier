import io
import unittest
import urllib.error
from unittest import mock

from info_kierowca_notifier import client


class Headers:
    def __init__(self, values):
        self.values = values

    def get_all(self, name):
        return self.values if name == "Set-Cookie" else None


class SessionCookieTests(unittest.TestCase):
    def test_cookie_header_preserves_existing_cookie_mapping(self):
        session = {"cookies": {"first": "one", "second": "two"}}
        self.assertEqual(client.cookie_header(session), "first=one; second=two")

    def test_set_cookie_updates_and_adds_values(self):
        session = {"cookies": {"session": "old"}}
        client.parse_set_cookies(
            Headers(["session=new; Path=/", "refresh=value; Secure"]), session
        )
        self.assertEqual(session["cookies"], {"session": "new", "refresh": "value"})

    def test_empty_and_expired_set_cookie_values_are_deleted(self):
        session = {"cookies": {"empty": "old", "aged": "old", "expired": "old"}}
        client.parse_set_cookies(
            Headers([
                "empty=; Path=/",
                "aged=value; Max-Age=0; Path=/",
                "expired=value; Expires=Thu, 01 Jan 1970 00:00:00 GMT",
            ]),
            session,
        )
        self.assertEqual(session["cookies"], {})

    def test_missing_headers_leave_session_unchanged(self):
        session = {"cookies": {"session": "value"}}
        client.parse_set_cookies(None, session)
        self.assertEqual(session["cookies"], {"session": "value"})


class CookieOriginScopeTests(unittest.TestCase):
    """A redirect that survives past tls_transport._CookieSafeRedirectHandler
    (see test_tls_transport.py) can still be a compromised/malicious host
    responding with its own Set-Cookie headers. parse_set_cookies() must
    ignore those unless the response's own final URL is actually on
    info-kierowca.pl."""

    def test_own_domain_response_url_is_merged(self):
        session = {"cookies": {}}
        client.parse_set_cookies(
            Headers(["session=value; Path=/"]),
            session,
            "https://info-kierowca.pl/bknd/auth/api/v1/jwt/refresh",
        )
        self.assertEqual(session["cookies"], {"session": "value"})

    def test_subdomain_response_url_is_merged(self):
        session = {"cookies": {}}
        client.parse_set_cookies(
            Headers(["session=value; Path=/"]), session, "https://api.info-kierowca.pl/x",
        )
        self.assertEqual(session["cookies"], {"session": "value"})

    def test_foreign_domain_response_url_is_ignored(self):
        session = {"cookies": {"existing": "kept"}}
        client.parse_set_cookies(
            Headers(["session=stolen; Path=/"]), session, "https://evil.example/steal",
        )
        self.assertEqual(session["cookies"], {"existing": "kept"})

    def test_lookalike_domain_response_url_is_ignored(self):
        session = {"cookies": {}}
        client.parse_set_cookies(
            Headers(["session=stolen; Path=/"]), session,
            "https://evilinfo-kierowca.pl/x",
        )
        self.assertEqual(session["cookies"], {})

    def test_no_response_url_falls_back_to_unscoped_behavior(self):
        """response_url is optional: callers that don't pass it (or pass
        None) keep the pre-existing behavior."""
        session = {"cookies": {}}
        client.parse_set_cookies(Headers(["session=value; Path=/"]), session)
        self.assertEqual(session["cookies"], {"session": "value"})


class DoRequestNetworkErrorTests(unittest.TestCase):
    """A slow or dropped response must surface as status None (the quiet
    network_error outcome), not escape as an exception that crashes a check."""

    def request_with(self, urlopen):
        with mock.patch.object(client.tls_transport, "urlopen", side_effect=urlopen):
            return client.do_request("https://info-kierowca.pl/x", {"cookies": {}})

    def test_timeout_waiting_for_the_response_is_a_network_error(self):
        status, body, headers = self.request_with(TimeoutError("timed out"))
        self.assertIsNone(status)
        self.assertEqual(body, b"timed out")
        self.assertIsNone(headers)

    def test_timeout_while_reading_the_body_is_a_network_error(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.side_effect = TimeoutError("read timed out")
        status, _body, _headers = self.request_with(lambda *_a, **_k: response)
        self.assertIsNone(status)

    def test_dropped_connection_is_a_network_error(self):
        status, _body, _headers = self.request_with(ConnectionResetError("reset by peer"))
        self.assertIsNone(status)

    def test_http_errors_still_return_their_status(self):
        error = urllib.error.HTTPError(
            "https://info-kierowca.pl/x", 429, "Too Many Requests", {}, io.BytesIO(b"slow down")
        )
        status, body, _headers = self.request_with(error)
        self.assertEqual(status, 429)
        self.assertEqual(body, b"slow down")


if __name__ == "__main__":
    unittest.main()
