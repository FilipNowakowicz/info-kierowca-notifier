import contextlib
import json
import unittest
from unittest.mock import patch

from info_kierowca_notifier.browser import cdp as cdp_client


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
        self.urlopen = patch("info_kierowca_notifier.browser.cdp.urllib.request.urlopen", side_effect=self._urlopen)
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

    def test_host_matching_obeys_domain_boundaries(self):
        self.targets.append({
            "id": "lookalike", "type": "page", "url": "https://notgov.pl", "title": "Bad",
            "webSocketDebuggerUrl": "ws://localhost/lookalike",
        })
        self.assertEqual(cdp_client.find_page_target("h", 1, host_match="gov.pl").id, "gov")
        self.targets = [x for x in self.targets if x["id"] != "gov"]
        with self.assertRaises(cdp_client.TargetNotFoundError):
            cdp_client.find_page_target("h", 1, host_match="gov.pl")

    def test_create_page_target_returns_created_target_metadata(self):
        created = {
            "id": "new", "type": "page", "url": "about:blank", "title": "",
            "webSocketDebuggerUrl": "ws://localhost/new",
        }
        self.targets.append(created)
        @contextlib.contextmanager
        def fake_socket(url):
            self.assertEqual(url, "ws://browser")
            yield object()
        with patch("info_kierowca_notifier.browser.cdp.browser_ws_url", return_value="ws://browser"), patch(
            "info_kierowca_notifier.browser.cdp.cdp_socket", fake_socket
        ), patch("info_kierowca_notifier.browser.cdp.cdp_call", return_value={"targetId": "new"}):
            target = cdp_client.create_page_target("h", 1)
        self.assertEqual(target, cdp_client.PageTarget.from_cdp(created))

    def _create_with_registration_sequence(self, target_lists, *, timeout=1.5):
        created_id = "created-exact-id"
        calls = []
        clock = [0.0]

        @contextlib.contextmanager
        def fake_socket(url):
            self.assertEqual(url, "ws://browser")
            yield object()

        def list_targets(host, port):
            calls.append([target.id for target in target_lists[0]])
            current = target_lists.pop(0) if len(target_lists) > 1 else target_lists[0]
            return current

        def sleep(delay):
            clock[0] += delay

        with patch("info_kierowca_notifier.browser.cdp.browser_ws_url", return_value="ws://browser"), patch(
            "info_kierowca_notifier.browser.cdp.cdp_socket", fake_socket
        ), patch("info_kierowca_notifier.browser.cdp.cdp_call", return_value={"targetId": created_id}), patch(
            "info_kierowca_notifier.browser.cdp.list_page_targets", side_effect=list_targets
        ):
            target = cdp_client.create_page_target(
                "h", 1, registration_timeout=timeout, poll_interval=0.25,
                monotonic=lambda: clock[0], sleep=sleep,
            )
        return target, calls

    def test_create_page_target_tolerates_delayed_registration(self):
        unrelated = cdp_client.PageTarget("other", "about:blank", "", "ws://other")
        created = cdp_client.PageTarget(
            "created-exact-id", "about:blank", "", "ws://created"
        )
        target, calls = self._create_with_registration_sequence(
            [[unrelated], [unrelated], [unrelated, created]]
        )
        self.assertEqual(target, created)
        self.assertEqual(len(calls), 3)

    def test_create_page_target_never_returns_unrelated_tab_while_waiting(self):
        unrelated = cdp_client.PageTarget("other", "about:blank", "", "ws://other")
        created = cdp_client.PageTarget(
            "created-exact-id", "about:blank", "", "ws://created"
        )
        target, calls = self._create_with_registration_sequence(
            [[unrelated], [cdp_client.PageTarget("new-other", "", "", "ws://new")], [created]]
        )
        self.assertEqual(target.id, "created-exact-id")
        self.assertEqual(calls[0], ["other"])
        self.assertEqual(calls[1], ["new-other"])

    def test_create_page_target_times_out_when_exact_id_never_appears(self):
        unrelated = cdp_client.PageTarget("other", "about:blank", "", "ws://other")
        with self.assertRaisesRegex(
            cdp_client.TargetNotFoundError, "created-exact-id.*did not register"
        ):
            self._create_with_registration_sequence([[unrelated]], timeout=0.5)

    def test_stale_target_fails_safely_before_socket_is_opened(self):
        target = cdp_client.find_page_target("h", 1, target_id="ikw")
        self.targets = [x for x in self.targets if x["id"] != "ikw"]
        with patch("info_kierowca_notifier.browser.cdp.cdp_socket") as socket:
            with self.assertRaises(cdp_client.StaleTargetError):
                cdp_client.evaluate_in_target("h", 1, target, "document.title")
            socket.assert_not_called()

    def test_target_actions_use_selected_websocket_only(self):
        calls = []
        @contextlib.contextmanager
        def fake_socket(url):
            calls.append(url)
            yield object()
        with patch("info_kierowca_notifier.browser.cdp.cdp_socket", fake_socket), patch(
            "info_kierowca_notifier.browser.cdp.cdp_call", return_value={"result": {"value": "ok"}}
        ):
            target = cdp_client.find_page_target("h", 1, target_id="gov")
            self.assertEqual(cdp_client.evaluate_in_target("h", 1, target, "location.host"), "ok")
            cdp_client.navigate_target("h", 1, target, "https://login.gov.pl/next")
            cdp_client.bring_target_to_front("h", 1, target)
        self.assertEqual(calls, ["ws://localhost/gov"] * 3)

    def test_navigation_context_loss_is_classified_for_bounded_retry(self):
        @contextlib.contextmanager
        def fake_socket(_url):
            yield object()

        responses = [
            {"result": {"objectId": "old-context"}},
            RuntimeError("Runtime.callFunctionOn failed: Cannot find context with specified id"),
        ]

        def call(*_args, **_kwargs):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch("info_kierowca_notifier.browser.cdp.cdp_socket", fake_socket), patch(
            "info_kierowca_notifier.browser.cdp.cdp_call", side_effect=call
        ):
            target = cdp_client.find_page_target("h", 1, target_id="gov")
            with self.assertRaises(cdp_client.ExecutionContextLostError):
                cdp_client.call_function_in_target("h", 1, target, "function(){return true}")
