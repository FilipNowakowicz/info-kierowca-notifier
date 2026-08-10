import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cdp_client
import open_logged_in_browser as browser
import reschedule_transaction as rt


def card(status="Potwierdzona", exam="Egzamin praktyczny", date="10/08/2026", time="12:30"):
    return {"status": status, "exam_type": exam, "date": date, "time": time, "attributes": {}}


TARGET = {"exam_type": "Egzamin praktyczny", "date": "10/08/2026", "time": "12:30"}
OLD = card(date="20/08/2026", time="09:00")


class BookingClassificationTests(unittest.TestCase):
    def test_exact_target_in_one_confirmed_card_is_success(self):
        self.assertEqual(rt.classify_cards([card()], TARGET, [OLD]), rt.VERIFIED_SUCCESS)

    def test_cancelled_target_and_different_confirmed_card_is_not_success(self):
        cards = [card(status="Anulowana"), OLD]
        self.assertNotEqual(rt.classify_cards(cards, TARGET, [OLD]), rt.VERIFIED_SUCCESS)

    def test_target_fields_split_across_cards_is_not_success(self):
        cards = [card(exam="Egzamin teoretyczny"), card(date="11/08/2026")]
        self.assertNotEqual(rt.classify_cards(cards, TARGET, [OLD]), rt.VERIFIED_SUCCESS)

    def test_two_matching_cards_are_unknown(self):
        self.assertEqual(rt.classify_cards([card(), card()], TARGET, [OLD]), rt.UNKNOWN)

    def test_missing_or_already_target_baseline_cannot_verify_success(self):
        self.assertEqual(rt.classify_cards([card()], TARGET, []), rt.UNKNOWN)
        self.assertEqual(rt.classify_cards([card()], TARGET, [card()]), rt.UNKNOWN)

    def test_old_and_target_both_active_is_unknown(self):
        self.assertEqual(rt.classify_cards([OLD, card()], TARGET, [OLD]), rt.UNKNOWN)

    def test_old_booking_still_active_is_verified_unchanged(self):
        self.assertEqual(rt.classify_cards([OLD], TARGET, [OLD]), rt.VERIFIED_UNCHANGED)


class DiagnosticSecurityTests(unittest.TestCase):
    def test_urls_and_unmistakable_secrets_are_removed(self):
        secret = "UNMISTAKABLESECRET0123456789ABCDEF"
        value = {"url": "https://info-kierowca.pl/cases?token=" + secret + "#x",
                 "cookie": secret, "headers": {"Authorization": secret},
                 "note": "PESEL 44051401458 token " + secret}
        clean = rt.sanitize(value)
        clean["url"] = rt.sanitize_url(value["url"])
        encoded = json.dumps(clean)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("44051401458", encoded)
        self.assertNotIn("?", clean["url"])
        self.assertNotIn("#", clean["url"])

    def test_snapshot_script_never_reads_input_values(self):
        self.assertNotIn(".value", rt.PAGE_SNAPSHOT_JS)
        self.assertIn("type:e.type", rt.PAGE_SNAPSHOT_JS)

    def test_json_valid_and_posix_permissions_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            location = Path(tmp) / "diagnostics"
            with mock.patch.object(rt, "RESCHEDULE_DIAGNOSTICS_DIR", location):
                recorder = rt.DiagnosticRecorder(TARGET, [OLD], "abc123")
                recorder.state("SUBMIT_CURRENT_FINAL_BUTTON")
                path = recorder.save()
                json.loads(path.read_text())
                if os.name == "posix":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(location.stat().st_mode & 0o777, 0o700)


class NetworkMetadataTests(unittest.TestCase):
    def setUp(self):
        self.clock = mock.Mock(return_value=12.5)
        self.observer = cdp_client.NetworkObserver("h", 1, "transaction", monotonic=self.clock)
        self.observer.started = 10

    def test_get_post_response_redirect_failure_are_metadata_only(self):
        secret = "UNMISTAKABLE_NETWORK_TOKEN_987654321"
        self.observer.process_message({"method": "Network.requestWillBeSent", "params": {
            "requestId": "1", "type": "XHR", "request": {"method": "POST",
            "url": "https://info-kierowca.pl/bknd/reservation?token=" + secret,
            "headers": {"Cookie": secret}, "postData": secret}}})
        self.observer.process_message({"method": "Network.responseReceived", "params": {
            "requestId": "1", "type": "XHR", "response": {"status": 204,
            "url": "https://info-kierowca.pl/bknd/reservation?x=1", "headers": {"Set-Cookie": secret}}}})
        self.observer.process_message({"method": "Network.loadingFailed", "params": {
            "requestId": "1", "errorText": "net::ERR_FAILED"}})
        encoded = json.dumps(self.observer.events)
        self.assertIn("/bknd/reservation", encoded)
        self.assertIn("204", encoded)
        self.assertIn("failed", encoded)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("postData", encoded)
        self.assertNotIn("headers", encoded)
        self.assertNotIn("?", encoded)

    def test_foreign_origin_is_ignored(self):
        self.observer.process_message({"method": "Network.requestWillBeSent", "params": {
            "requestId": "x", "request": {"method": "GET", "url": "https://evil.test/a"}}})
        self.assertEqual(self.observer.events, [])

    def test_get_redirect_4xx_5xx_and_completion(self):
        for rid, status in (("4", 404), ("5", 503)):
            self.observer.process_message({"method": "Network.requestWillBeSent", "params": {
                "requestId": rid, "type": "Document", "request": {"method": "GET",
                "url": "https://info-kierowca.pl/old?private=1"},
                "redirectResponse": {"status": 302}}})
            self.observer.process_message({"method": "Network.responseReceived", "params": {
                "requestId": rid, "type": "Document", "response": {"status": status,
                "url": "https://info-kierowca.pl/new#private"}}})
            self.observer.process_message({"method": "Network.loadingFinished", "params": {
                "requestId": rid}})
        encoded = json.dumps(self.observer.events)
        self.assertIn('"method": "GET"', encoded)
        self.assertIn('"redirect_status": 302', encoded)
        self.assertIn('"status": 404', encoded)
        self.assertIn('"status": 503', encoded)
        self.assertIn('"event": "finished"', encoded)
        self.assertNotIn("private", encoded)


class SeparateTargetFlowTests(unittest.TestCase):
    @mock.patch.object(rt, "capture_booking_cards")
    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "navigate_target")
    @mock.patch.object(cdp_client, "create_page_target")
    def test_delayed_success_uses_separate_verifier(self, create, navigate, page, cards):
        transaction = cdp_client.PageTarget("tx", "https://info-kierowca.pl/summary", "", "ws://tx")
        verifier = cdp_client.PageTarget("verify", "about:blank", "", "ws://verify")
        create.return_value = verifier
        page.return_value = {"url": "https://info-kierowca.pl/summary", "buttons": [], "forms": [], "dialogs": []}
        cards.side_effect = [[OLD], [OLD], [card()]]
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "tx1")
        recorder.data["post_submit_pages"].append(page.return_value)
        clock = mock.Mock(side_effect=[0, 0, 0, 1, 1, 2, 2, 3])
        result = rt.run_post_submit("h", 1, transaction, recorder, timeout=10, interval=0,
                                    monotonic=clock, sleep=lambda _: None)
        self.assertEqual(result, rt.VERIFIED_SUCCESS)
        navigate.assert_called_once_with("h", 1, verifier, "https://info-kierowca.pl/cases")
        self.assertNotIn(transaction, [call.args[2] for call in navigate.call_args_list])
        self.assertTrue(all(call.args[2] == verifier for call in cards.call_args_list))

    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "create_page_target", side_effect=RuntimeError("gone"))
    def test_verifier_unavailable_never_navigates_transaction(self, create, page):
        transaction = cdp_client.PageTarget("tx", "", "", "ws://tx")
        page.return_value = {"url": "https://info-kierowca.pl/summary", "buttons": [], "forms": [], "dialogs": []}
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "tx2")
        with mock.patch.object(cdp_client, "navigate_target") as navigate:
            result = rt.run_post_submit("h", 1, transaction, recorder, timeout=-1)
        self.assertEqual(result, rt.UNKNOWN)
        navigate.assert_not_called()

    def test_new_stable_control_looks_like_further_confirmation(self):
        before = {"url": "https://info-kierowca.pl/summary", "buttons": [{"text": "Submit", "disabled": False}]}
        after = {"url": "https://info-kierowca.pl/next", "buttons": [{"text": "Confirm again", "disabled": False}], "forms": [{}]}
        self.assertTrue(rt.looks_like_further_confirmation(after, before))

    @mock.patch.object(rt, "capture_booking_cards", return_value=[OLD])
    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "navigate_target")
    @mock.patch.object(cdp_client, "create_page_target")
    def test_transaction_disappearance_is_unknown(self, create, navigate, page, cards):
        transaction = cdp_client.PageTarget("tx", "", "", "ws://tx")
        create.return_value = cdp_client.PageTarget("verify", "", "", "ws://verify")
        stable = {"url": "https://info-kierowca.pl/summary", "buttons": [], "forms": [], "dialogs": []}
        page.side_effect = [stable, cdp_client.StaleTargetError("closed")]
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "tx3")
        recorder.data["post_submit_pages"].append(stable)
        result = rt.run_post_submit("h", 1, transaction, recorder, timeout=2, interval=0,
                                    monotonic=mock.Mock(side_effect=[0, 0, 0, 1]), sleep=lambda _: None)
        self.assertEqual(result, rt.UNKNOWN)

    @mock.patch.object(rt, "capture_booking_cards", side_effect=cdp_client.StaleTargetError("verifier closed"))
    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "navigate_target")
    @mock.patch.object(cdp_client, "create_page_target")
    def test_verifier_disappearance_does_not_touch_transaction(self, create, navigate, page, cards):
        transaction = cdp_client.PageTarget("tx", "", "", "ws://tx")
        verifier = cdp_client.PageTarget("verify", "", "", "ws://verify")
        create.return_value = verifier
        stable = {"url": "https://info-kierowca.pl/summary", "buttons": [], "forms": [], "dialogs": []}
        page.return_value = stable
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "tx4")
        recorder.data["post_submit_pages"].append(stable)
        result = rt.run_post_submit("h", 1, transaction, recorder, timeout=0, interval=0,
                                    monotonic=mock.Mock(side_effect=[0, 0, 0, 1]), sleep=lambda _: None)
        self.assertEqual(result, rt.UNKNOWN)
        navigate.assert_called_once_with("h", 1, verifier, "https://info-kierowca.pl/cases")
        self.assertGreaterEqual(page.call_count, 2)


class StateMutationTests(unittest.TestCase):
    def test_current_slot_date_changes_only_for_verified_success(self):
        with mock.patch.object(browser, "update_current_slot_date") as update:
            for outcome in (rt.UNKNOWN, rt.VERIFIED_UNCHANGED, rt.NEEDS_FURTHER_CONFIRMATION):
                self.assertFalse(browser.update_slot_date_for_outcome(outcome, "2026-08-10"))
            update.assert_not_called()
            self.assertTrue(browser.update_slot_date_for_outcome(rt.VERIFIED_SUCCESS, "2026-08-10"))
            update.assert_called_once_with("2026-08-10")

    def test_submit_attempt_always_activates_cooldown_with_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cooldown"
            with mock.patch.object(browser, "RESCHEDULE_CONFIRM_COOLDOWN_FILE", path):
                browser.record_confirm_cooldown(rt.UNKNOWN)
                payload = json.loads(path.read_text())
                self.assertEqual(payload["outcome"], rt.UNKNOWN)
                self.assertIn("attempted_at", payload)


if __name__ == "__main__":
    unittest.main()
