"""The two local HTTP surfaces must not be drivable by another web page.

Covers web.guard's Host/Origin/Content-Type checks as wired into both
app.AppHandler and web.server.Handler, plus the escaping/validation of
ntfy_topic, which is the one config value that reaches the settings page as
markup rather than as JSON.
"""
import http.client
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import info_kierowca_notifier.app as app
from info_kierowca_notifier.web import guard
from info_kierowca_notifier.web import server as dashboard_server

GOOD_HOST = f"127.0.0.1:{app.PORT}"
GOOD_ORIGIN = f"http://127.0.0.1:{app.PORT}"
JSON_CT = "application/json"

# Every path app.AppHandler.do_POST dispatches. A new mutating endpoint added
# without a guard should fail test_every_post_endpoint_rejects_a_foreign_host.
POST_PATHS = [
    "/setup", "/login-start", "/shutdown", "/manual-login", "/relogin-now",
    "/relogin-restart", "/pause", "/resume", "/test-push", "/reset-account",
    "/pair-google-messages", "/test-google-messages",
]


def make_handler(handler_cls, path, headers):
    """A handler instance with no socket behind it: __init__ is skipped (it
    would start reading a real request), and _send just records."""
    handler = handler_cls.__new__(handler_cls)
    handler.path = path
    handler.headers = http.client.HTTPMessage()
    for key, value in headers.items():
        if value is not None:
            handler.headers[key] = value
    handler.sent = []
    handler._send = lambda code, body, content_type=None: handler.sent.append(
        (code, body, content_type)
    )
    handler._send_json = lambda code, obj: handler.sent.append(
        (code, json.dumps(obj).encode(), "application/json")
    )
    return handler


def post_handler(path="/pause", host=GOOD_HOST, origin=GOOD_ORIGIN, content_type=JSON_CT,
                 handler_cls=app.AppHandler):
    return make_handler(handler_cls, path, {
        "Host": host, "Origin": origin, "Content-Type": content_type,
    })


class GuardPrimitiveTests(unittest.TestCase):
    def test_only_loopback_host_headers_are_accepted(self):
        for host in ("127.0.0.1", "localhost", "[::1]"):
            self.assertTrue(guard.host_allowed(f"{host}:8787", 8787), host)
        self.assertTrue(guard.host_allowed("LOCALHOST:8787", 8787))
        for host in ("evil.example:8787", "127.0.0.1:9999", "127.0.0.1", "", None,
                     "127.0.0.1.evil.example:8787"):
            self.assertFalse(guard.host_allowed(host, 8787), host)

    def test_absent_origin_is_allowed_but_a_foreign_one_is_not(self):
        # A browser always sends Origin on POST, so absence means a non-browser
        # client (curl, the app's own already_running() probe).
        self.assertTrue(guard.origin_allowed(None, 8787))
        self.assertTrue(guard.origin_allowed("", 8787))
        self.assertTrue(guard.origin_allowed("http://localhost:8787", 8787))
        for origin in ("https://evil.example", "http://evil.example:8787", "null",
                       "http://127.0.0.1:9999", "https://127.0.0.1:8787"):
            self.assertFalse(guard.origin_allowed(origin, 8787), origin)

    def test_only_json_content_types_are_accepted(self):
        self.assertTrue(guard.content_type_is_json("application/json"))
        self.assertTrue(guard.content_type_is_json("Application/JSON; charset=utf-8"))
        # The three CORS-safelisted types are exactly what a cross-site fetch
        # can send with no preflight - none of them may be accepted.
        for value in ("text/plain", "application/x-www-form-urlencoded",
                      "multipart/form-data", "", None):
            self.assertFalse(guard.content_type_is_json(value), value)


class AppPostGuardTests(unittest.TestCase):
    def assert_rejected(self, handler, code):
        self.assertEqual(len(handler.sent), 1)
        self.assertEqual(handler.sent[0][0], code)

    def test_foreign_host_is_rejected(self):
        handler = post_handler(host="evil.example:8787")
        self.assertFalse(handler.guard_post())
        self.assert_rejected(handler, 403)

    def test_foreign_origin_is_rejected(self):
        handler = post_handler(origin="https://evil.example")
        self.assertFalse(handler.guard_post())
        self.assert_rejected(handler, 403)

    def test_opaque_null_origin_is_rejected(self):
        handler = post_handler(origin="null")
        self.assertFalse(handler.guard_post())
        self.assert_rejected(handler, 403)

    def test_simple_request_content_types_are_rejected(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", None):
            handler = post_handler(content_type=content_type)
            self.assertFalse(handler.guard_post())
            self.assert_rejected(handler, 415)

    def test_same_origin_json_post_passes(self):
        for origin in (GOOD_ORIGIN, f"http://localhost:{app.PORT}", None):
            handler = post_handler(origin=origin, content_type="application/json; charset=utf-8")
            self.assertTrue(handler.guard_post(), origin)
            self.assertEqual(handler.sent, [])

    def test_every_post_endpoint_rejects_a_foreign_host(self):
        for path in POST_PATHS:
            handler = post_handler(path=path, host="evil.example:8787")
            with patch.object(app.AppHandler, "_handle_setup") as setup, \
                    patch.object(app, "os") as os_module:
                handler.do_POST()
            setup.assert_not_called()
            os_module._exit.assert_not_called()  # /shutdown must not fire
            self.assert_rejected(handler, 403)

    def test_a_guarded_post_still_reaches_its_handler(self):
        handler = post_handler(path="/setup")
        with patch.object(app.AppHandler, "_handle_setup") as setup:
            handler.do_POST()
        setup.assert_called_once()


class AppGetGuardTests(unittest.TestCase):
    def test_rebinding_host_cannot_read_settings_or_status(self):
        for path in ("/", "/settings", "/setup", "/status.json", "/login-status"):
            handler = make_handler(app.AppHandler, path, {"Host": "evil.example:8787"})
            handler.do_GET()
            self.assertEqual(handler.sent[0][0], 403, path)
            self.assertNotIn(b"ntfy_topic", handler.sent[0][1])

    def test_loopback_host_still_gets_the_settings_page(self):
        missing = Path("/nonexistent-ikw-test/config.json")
        for host in (GOOD_HOST, f"localhost:{app.PORT}"):
            handler = make_handler(app.AppHandler, "/settings", {"Host": host})
            with patch.object(app.notifier, "CONFIG_FILE", missing):
                handler.do_GET()
            self.assertEqual(handler.sent[0][0], 200, host)
            self.assertIn(b'id="ntfy_topic"', handler.sent[0][1])


class DashboardGetGuardTests(unittest.TestCase):
    def test_rebinding_host_cannot_read_booking_history(self):
        handler = make_handler(dashboard_server.Handler, "/status.json",
                               {"Host": "evil.example:8787"})
        handler.do_GET()
        self.assertEqual(handler.sent[0][0], 403)

    def test_loopback_host_still_reads_status(self):
        handler = make_handler(dashboard_server.Handler, "/status.json", {"Host": GOOD_HOST})
        missing = Path("/nonexistent-ikw-test/status.json")
        with patch.object(dashboard_server, "STATUS_FILE", missing):
            handler.do_GET()
        self.assertEqual(handler.sent[0][0], 200)
        self.assertEqual(handler.sent[0][1], dashboard_server.EMPTY_STATUS)


class NtfyTopicTests(unittest.TestCase):
    def payload(self, topic):
        return {
            "profile_number": "123", "ntfy_topic": topic, "organization_ids": [1],
            "exam_types": ["Theoretical"], "category": 5,
            "current_slot_date": "2026-09-14",
        }

    def test_generated_topic_matches_the_accepted_charset(self):
        self.assertRegex(app.generate_ntfy_topic(), app.NTFY_TOPIC_PATTERN)

    def test_topics_outside_ntfys_charset_are_rejected(self):
        for topic in ('"><script>alert(1)</script>', "a b", "../secret", "a/b",
                      "topic!", "x" * 65):
            with self.assertRaises(ValueError, msg=topic):
                app.build_config(self.payload(topic))

    def test_plain_topics_are_accepted(self):
        for topic in ("topic", "ik-Abc_123-XYZ", "x" * 64):
            self.assertEqual(app.build_config(self.payload(topic))["ntfy_topic"], topic)

    def test_a_stored_hostile_topic_cannot_break_out_of_the_settings_page(self):
        # A config.json predating the validation above (or hand-edited) is the
        # realistic source of such a value - the page must still be inert.
        hostile = '"><script>alert(1)</script>'
        page = app.render_wizard({"ntfy_topic": hostile}).decode()
        self.assertNotIn(hostile, page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("</script>", page.split("const NTFY_TOPIC =")[1].split("\n")[0])
        self.assertIn("ntfyInput.value = NTFY_TOPIC;", page)

    def test_test_push_refuses_a_topic_outside_the_charset(self):
        handler = post_handler(path="/test-push")
        handler._read_json_body = lambda: {"topic": "../admin"}
        with patch.object(app.notifier, "push_ntfy") as push:
            handler._handle_test_push()
        push.assert_not_called()
        self.assertEqual(handler.sent[0][0], 400)


if __name__ == "__main__":
    unittest.main()
