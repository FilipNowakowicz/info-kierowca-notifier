import contextlib
import json
import unittest
from unittest.mock import patch

import cdp_client


TARGETS = [
    {"id": "ikw", "type": "page", "url": "https://info-kierowca.pl/cases", "title": "Info", "webSocketDebuggerUrl": "ws://localhost/ikw"},
    {"id": "other", "type": "page", "url": "https://example.test", "title": "Unrelated", "webSocketDebuggerUrl": "ws://localhost/other"},
    {"id": "gov", "type": "page", "url": "https://login.gov.pl/auth", "title": "Login", "webSocketDebuggerUrl": "ws://localhost/gov"},
    {"id": "messages", "type": "page", "url": "https://messages.google.com/web", "title": "Google Messages", "webSocketDebuggerUrl": "ws://localhost/messages"},
]


class FakeResponse:
    def __init__(self, value): self.value = value
    def read(self): return json.dumps(self.value).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False


class CdpTargetTests(unittest.TestCase):
    def setUp(self):
        self.targets = list(TARGETS)
        self.urlopen = patch("cdp_client.urllib.request.urlopen", side_effect=self._urlopen)
        self.urlopen.start()
        self.addCleanup(self.urlopen.stop)

    def _urlopen(self, url, timeout=5):
        return FakeResponse(self.targets)

    def test_selects_each_explicit_target_not_first_tab(self):
        self.assertEqual(cdp_client.find_page_target("h", 1, host_match="info-kierowca.pl").id, "ikw")
        self.assertEqual(cdp_client.find_page_target("h", 1, host_match="gov.pl").id, "gov")
        self.assertEqual(cdp_client.find_page_target("h", 1, url_match="messages.google.com").id, "messages")
        self.assertEqual(cdp_client.find_page_target("h", 1, title_match="Unrelated").id, "other")

    def test_missing_requested_target_never_falls_back_to_first(self):
        with self.assertRaises(cdp_client.TargetNotFoundError):
            cdp_client.page_ws_url("h", 1, host_match="missing.gov.pl")

    def test_stale_target_fails_safely_before_socket_is_opened(self):
        target = cdp_client.find_page_target("h", 1, target_id="ikw")
        self.targets = [x for x in self.targets if x["id"] != "ikw"]
        with patch("cdp_client.cdp_socket") as socket:
            with self.assertRaises(cdp_client.StaleTargetError):
                cdp_client.evaluate_in_target("h", 1, target, "document.title")
            socket.assert_not_called()

    def test_target_actions_use_selected_websocket_only(self):
        calls = []
        @contextlib.contextmanager
        def fake_socket(url):
            calls.append(url)
            yield object()
        with patch("cdp_client.cdp_socket", fake_socket), patch(
            "cdp_client.cdp_call", return_value={"result": {"value": "ok"}}
        ):
            target = cdp_client.find_page_target("h", 1, target_id="gov")
            self.assertEqual(cdp_client.evaluate_in_target("h", 1, target, "location.host"), "ok")
            cdp_client.navigate_target("h", 1, target, "https://login.gov.pl/next")
            cdp_client.bring_target_to_front("h", 1, target)
        self.assertEqual(calls, ["ws://localhost/gov"] * 3)

