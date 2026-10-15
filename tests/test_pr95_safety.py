"""Regression coverage for the fixes layered on top of the v2.4.x reservation
and rate-limit work: proactive search-quota gating, a pause wait that can't
block forever, and wizard clicks that never retry after a lost CDP response."""
import json
import logging
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from info_kierowca_notifier import client, notifier
from info_kierowca_notifier.booking import launch as booking_launch
from info_kierowca_notifier.booking import reservation


CONFIG = {
    "login_method": "mobywatel",
    "search_mode": "multi",
    "organization_ids": [26],
    "category": 5,
    "profile_number": "PKK-1",
    "exam_types": ["Theoretical"],
    "ntfy_topic": "",
    "current_slot_date": "2099-01-01",
    "search_start_date": "",
}


class RunCheckHarness(unittest.TestCase):
    def run_check(self, dash_status, search_response):
        calls = []

        def request(url, _session, method="GET", json_body=None):
            calls.append(url)
            if method == "GET":
                return 204, b"", {}
            return search_response

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps(CONFIG))
            (root / "session.json").write_text(json.dumps({"cookies": {}}))
            with mock.patch.object(notifier, "CONFIG_FILE", root / "config.json"), \
                 mock.patch.object(notifier, "SESSION_FILE", root / "session.json"), \
                 mock.patch.object(notifier, "PAUSE_FILE", root / "paused"), \
                 mock.patch.object(notifier, "save_status"), \
                 mock.patch.object(client, "do_request", side_effect=request), \
                 mock.patch.object(notifier, "build_search_organization_ids", return_value=[26] * 5), \
                 mock.patch.object(booking_launch, "trigger_open_browser"), \
                 mock.patch.object(notifier, "push_ntfy"):
                notifier.run_check(logging.getLogger("test"), dash_status)
        return [url for url in calls if url != client.REFRESH_URL]


class SearchQuotaTests(RunCheckHarness):
    def test_full_local_quota_skips_the_search_request(self):
        now = time.time()
        recent = [now - 3000 + i for i in range(notifier.RATE_LIMIT_MAX_SEARCHES)]
        status = {"successful_search_times": recent, "outcome": "no_slot"}

        searches = self.run_check(status, (200, b"[]", {}))

        self.assertEqual(searches, [])
        self.assertEqual(status["outcome"], "rate_limited")
        self.assertAlmostEqual(
            status["rate_limit_until"],
            recent[0] + notifier.RATE_LIMIT_WINDOW_SECONDS + notifier.RATE_LIMIT_SAFETY_SECONDS,
        )

    def test_searches_older_than_the_window_do_not_count(self):
        now = time.time()
        stale = [now - 4000 + i for i in range(notifier.RATE_LIMIT_MAX_SEARCHES)]
        status = {"successful_search_times": stale}

        searches = self.run_check(status, (200, b"[]", {}))

        self.assertEqual(searches, [client.SEARCH_URL])
        self.assertEqual(status["outcome"], "no_slot")

    def test_server_reporting_zero_remaining_holds_off_until_its_reset(self):
        reset = time.time() + 1200
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(reset))}
        status = {}

        self.run_check(status, (200, b"[]", headers))

        self.assertEqual(status["outcome"], "no_slot")
        self.assertAlmostEqual(
            status["rate_limit_until"], int(reset) + notifier.RATE_LIMIT_SAFETY_SECONDS
        )

    def test_remaining_quota_leaves_scheduling_alone(self):
        headers = {"x-ratelimit-remaining": "4", "x-ratelimit-reset": str(int(time.time() + 1200))}
        status = {}

        self.run_check(status, (200, b"[]", headers))

        self.assertIsNone(status.get("rate_limit_until"))


class PauseWaitTests(unittest.TestCase):
    def test_standalone_loop_resumes_without_a_wake_event(self):
        """The standalone --loop has no Resume handler to set wake_event, so
        removing the pause file alone must be enough to resume."""
        stop_event = threading.Event()
        checks = []
        paused_until = time.monotonic() + 0.2

        def fake_run_check(_logger, _dash_status):
            checks.append(time.monotonic())
            if len(checks) == 2:
                stop_event.set()

        with mock.patch.object(notifier, "run_check", side_effect=fake_run_check), \
             mock.patch.object(notifier, "configured_poll_interval", return_value=0.05), \
             mock.patch.object(notifier, "is_paused", side_effect=lambda: time.monotonic() < paused_until), \
             mock.patch.object(notifier, "PAUSE_RECHECK_SECONDS", 0.02), \
             mock.patch.object(notifier, "save_status"):
            worker = threading.Thread(
                target=notifier.loop,
                args=(logging.getLogger("test"), {}),
                kwargs={"stop_event": stop_event},
                daemon=True,
            )
            worker.start()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive(), "loop() stayed blocked after the pause file was removed")
        self.assertEqual(len(checks), 2)


class WizardClickRetryTests(unittest.TestCase):
    """Steps 2, 3 and 4 all label their button "Zapisz i przejdź dalej", and
    step 4's creates the reservation. A step-3 click whose CDP response was
    lost may already have advanced the page, so retrying it could click
    step 4's button instead."""

    def click(self, outcomes, strict):
        evaluate = mock.Mock(side_effect=outcomes)
        with mock.patch.object(reservation.cdp_client, "evaluate_in_page", evaluate), \
             mock.patch.object(reservation.time, "sleep"):
            result = reservation.wait_and_click_enabled(
                "127.0.0.1", 9555, "Zapisz i przejdź dalej", timeout=5, strict=strict
            )
        return result, evaluate.call_count

    def test_strict_click_never_retries_after_a_lost_response(self):
        result, calls = self.click([socket.timeout(), {"clicked": True}], strict=True)
        self.assertFalse(result)
        self.assertEqual(calls, 1)

    def test_strict_click_still_retries_a_clean_not_found(self):
        result, calls = self.click(
            [{"clicked": False, "reason": "not_found"}, {"clicked": True}], strict=True
        )
        self.assertTrue(result)
        self.assertEqual(calls, 2)

    def test_auto_select_flow_uses_strict_clicks_for_every_wizard_step(self):
        strict_flags = []

        def fake_click(_host, _port, text, timeout=20, target=None, strict=False):
            strict_flags.append((text, strict))
            return True

        target = json.dumps({
            "word": "WORD Test", "exam_type": "Theoretical",
            "datetime": "2099-01-01T10:00:00", "organization_id": 26,
        })
        with mock.patch.object(reservation, "wait_and_expand_date", return_value=True), \
             mock.patch.object(reservation, "wait_and_select_slot", return_value=True), \
             mock.patch.object(reservation, "wait_and_click_enabled", side_effect=fake_click), \
             mock.patch.object(reservation, "wait_for_page_text", return_value=True), \
             mock.patch.object(reservation, "wait_and_click_radio_selector", return_value=True), \
             mock.patch.object(reservation, "summary_matches_target", return_value=True), \
             mock.patch.object(reservation, "click_confirm_once") as final_click:
            reservation.try_select_target_slot("127.0.0.1", 9555, target, confirm=False)

        self.assertEqual(len(strict_flags), 3)
        self.assertTrue(all(strict for _text, strict in strict_flags), strict_flags)
        final_click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
