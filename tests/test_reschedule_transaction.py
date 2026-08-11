import contextlib
import datetime
import inspect
import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from info_kierowca_notifier.browser import cdp as cdp_client
from info_kierowca_notifier.booking import launch as booking_launch
from info_kierowca_notifier.booking import reschedule as browser
from info_kierowca_notifier.booking import transaction as rt


def card(status="Potwierdzona", exam="Egzamin praktyczny", date="10/08/2026", time="12:30"):
    return {"status": status, "exam_type": exam, "date": date, "time": time, "attributes": {}}


TARGET = {"exam_type": "Egzamin praktyczny", "date": "10/08/2026", "time": "12:30"}
OLD = card(date="20/08/2026", time="09:00")
# A second, unrelated active booking — theory alongside practical is the
# ordinary case for anyone still taking both exams.
THEORY = card(exam="Egzamin teoretyczny", date="05/09/2026", time="08:00")
CENTER = "WORD Warszawa M/E Odolany"
OTHER_CENTER = "WORD Warszawa M/E Bemowo"


class BookingClassificationTests(unittest.TestCase):
    def test_status_classification_is_exact_and_fail_closed(self):
        for status in ("Potwierdzona", "Aktywna", "Confirmed", "Active"):
            with self.subTest(status=status):
                self.assertTrue(rt.active(card(status=status)))
        for status in ("Anulowana", "Nieaktywna", "Cancelled", "Canceled", "Inactive", "Unknown"):
            with self.subTest(status=status):
                self.assertFalse(rt.active(card(status=status)))

    def test_inner_word_inactive_statuses_never_match_active_statuses(self):
        self.assertFalse(rt.active(card(status="Nieaktywna")))
        self.assertFalse(rt.active(card(status="Inactive")))
        self.assertNotEqual("nieaktywna", "aktywna")
        self.assertNotEqual("inactive", "active")

    def test_inactive_target_card_can_never_verify_success(self):
        for status in ("Nieaktywna", "Inactive"):
            with self.subTest(status=status):
                self.assertNotEqual(
                    rt.classify_cards([card(status=status)], TARGET, [OLD]),
                    rt.VERIFIED_SUCCESS,
                )

    def test_booking_parser_status_pattern_is_word_bounded(self):
        self.assertIn(r"\b(?:Potwierdzona", rt.BOOKING_CARDS_JS)
        self.assertIn("Nieaktywna|Aktywna", rt.BOOKING_CARDS_JS)
        self.assertIn("Inactive|Active", rt.BOOKING_CARDS_JS)

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

    def test_two_active_bookings_can_still_verify_the_target(self):
        # Regression: classify_cards() used to demand exactly one active
        # booking account-wide, so a theory + practical holder could never
        # reach VERIFIED_SUCCESS — current_slot_date then stayed stale, which
        # re-arms is_urgent() and can hand a later cycle another real confirm
        # click for a slot no better than the one just booked.
        self.assertEqual(
            rt.classify_cards([card(), THEORY], TARGET, [OLD, THEORY]),
            rt.VERIFIED_SUCCESS,
        )

    def test_two_unchanged_bookings_are_verified_unchanged(self):
        self.assertEqual(
            rt.classify_cards([OLD, THEORY], TARGET, [OLD, THEORY]),
            rt.VERIFIED_UNCHANGED,
        )

    def test_target_added_alongside_every_old_booking_is_unknown(self):
        # Looks like an extra booking rather than a moved one.
        self.assertEqual(
            rt.classify_cards([OLD, THEORY, card()], TARGET, [OLD, THEORY]),
            rt.UNKNOWN,
        )

    def test_target_already_active_in_the_baseline_is_never_success(self):
        self.assertEqual(
            rt.classify_cards([card(), THEORY], TARGET, [card(), THEORY]),
            rt.UNKNOWN,
        )

    def test_matching_card_at_a_different_centre_is_not_the_target(self):
        target = dict(TARGET, center=CENTER)
        wrong = dict(card(), center=OTHER_CENTER)
        right = dict(card(), center=CENTER)
        self.assertFalse(rt.matches_target(wrong, target))
        self.assertTrue(rt.matches_target(right, target))
        self.assertNotEqual(rt.classify_cards([wrong], target, [OLD]), rt.VERIFIED_SUCCESS)
        self.assertEqual(rt.classify_cards([right], target, [OLD]), rt.VERIFIED_SUCCESS)

    def test_card_without_a_readable_centre_still_matches(self):
        # BOOKING_CARDS_JS scrapes the centre out of free text; a card that
        # simply doesn't repeat it must not become permanently unverifiable.
        self.assertTrue(rt.matches_target(card(), dict(TARGET, center=CENTER)))

    def test_centre_comparison_tolerates_accents_punctuation_and_prefixes(self):
        self.assertTrue(rt.centers_compatible("WORD Warszawa", CENTER))
        self.assertTrue(rt.centers_compatible("word warszawa m.e. odolany", CENTER))
        self.assertFalse(rt.centers_compatible(OTHER_CENTER, CENTER))
        self.assertFalse(rt.center_conflict("", CENTER))
        self.assertFalse(rt.center_conflict(CENTER, ""))
        self.assertTrue(rt.center_conflict(OTHER_CENTER, CENTER))
        self.assertEqual(rt.normalize_center("MORD Kraków"), "mord krakow")

    def test_transaction_target_centre_is_read_under_any_spelling(self):
        self.assertEqual(rt.target_center({"center": CENTER}), CENTER)
        self.assertEqual(rt.target_center({"word_id": CENTER}), CENTER)
        self.assertEqual(rt.target_center({"word": CENTER}), CENTER)
        self.assertEqual(rt.target_center({}), "")

    def test_exam_label_case_and_whitespace_are_normalized_but_datetime_is_exact(self):
        formatted = card(exam="  EGZAMIN   PRAKTYCZNY ")
        self.assertEqual(rt.classify_cards([formatted], TARGET, [OLD]), rt.VERIFIED_SUCCESS)
        self.assertEqual(rt.classify_cards([card(time="12:31")], TARGET, [OLD]), rt.UNKNOWN)


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

    def test_candidate_extractor_is_bounded_structural_metadata_only(self):
        source = rt.BOOKING_DIAGNOSTIC_CANDIDATES_JS
        self.assertIn("out.length>=25", source)
        self.assertIn("statuses", source)
        self.assertNotIn("status_tokens", source)
        self.assertIn("exam_types", source)
        self.assertIn("text_length", source)
        self.assertIn("grandparent", source)
        self.assertNotIn("outerHTML", source)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("text:text", source)
        self.assertNotIn(".value", source)

    @mock.patch.object(cdp_client, "evaluate_in_page")
    def test_candidate_capture_identifies_structure_and_redacts_secrets(self, evaluate):
        secret = "UNMISTAKABLE_CANDIDATE_SECRET_123456789"
        evaluate.return_value = [{
            "tag": "div", "role": "group", "class_name": "reservation-tile " + secret,
            "test_id": "booking-card", "statuses": ["Potwierdzona"],
            "session_token": secret,
            "dates": ["10/08/2026"], "times": ["12:30"],
            "exam_types": ["Egzamin praktyczny"], "text_length": 138,
            "parent": {"tag": "section", "class_name": "case-wrapper"},
        }]
        candidates = rt.capture_booking_diagnostic_candidates("h", 1, "target")
        encoded = json.dumps(candidates)
        self.assertEqual(candidates[0]["tag"], "div")
        self.assertEqual(candidates[0]["parent"]["class_name"], "case-wrapper")
        self.assertEqual(candidates[0]["statuses"], ["Potwierdzona"])
        self.assertNotIn("session_token", candidates[0])
        self.assertNotIn(secret, encoded)
        self.assertNotIn("html", encoded.casefold())

    def test_diagnostic_candidates_cannot_influence_card_classification(self):
        recorder = rt.DiagnosticRecorder(
            TARGET, [OLD], "candidate-only",
            baseline_booking_candidates=[{
                "statuses": ["Potwierdzona"], "dates": [TARGET["date"]],
                "times": [TARGET["time"]], "exam_types": [TARGET["exam_type"]],
            }],
        )
        self.assertTrue(recorder.data["baseline_booking_candidates"])
        self.assertEqual(rt.classify_cards([], TARGET, [OLD]), rt.UNKNOWN)

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

    def test_serialized_candidates_keep_statuses_but_remove_token_fields(self):
        secret = "UNMISTAKABLE_SAVED_TOKEN_SECRET_123456789"
        candidate = {
            "tag": "div", "statuses": ["Potwierdzona"],
            "session_token": secret, "secret_note": secret,
        }
        with tempfile.TemporaryDirectory() as tmp:
            location = Path(tmp) / "diagnostics"
            with mock.patch.object(rt, "RESCHEDULE_DIAGNOSTICS_DIR", location):
                recorder = rt.DiagnosticRecorder(
                    TARGET, [OLD], "saved-statuses",
                    baseline_booking_candidates=[candidate],
                )
                recorder.data["verification_attempts"].append({
                    "diagnostic_candidates": [candidate], "result": rt.UNKNOWN,
                })
                payload = json.loads(recorder.save().read_text())

        baseline_candidate = payload["baseline_booking_candidates"][0]
        verification_candidate = payload["verification_attempts"][0][
            "diagnostic_candidates"
        ][0]
        self.assertEqual(baseline_candidate["statuses"], ["Potwierdzona"])
        self.assertEqual(verification_candidate["statuses"], ["Potwierdzona"])
        self.assertNotIn("session_token", baseline_candidate)
        self.assertNotIn("secret_note", baseline_candidate)
        self.assertNotIn(secret, json.dumps(payload))


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

    def test_stop_unblocks_socket_before_joining_reader(self):
        order = []
        sock = mock.Mock()
        sock.shutdown.side_effect = lambda *_: order.append("shutdown")
        thread = mock.Mock()
        thread.join.side_effect = lambda **_: order.append("join")
        self.observer._sock = sock
        self.observer._thread = thread
        self.observer._manager = mock.Mock()
        self.observer.stop()
        self.assertEqual(order[:2], ["shutdown", "join"])


class StableBaselineTests(unittest.TestCase):
    class Clock:
        def __init__(self):
            self.now = 0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    @mock.patch.object(rt, "capture_booking_diagnostic_candidates", return_value=[{"tag": "div"}])
    @mock.patch.object(rt, "capture_booking_cards")
    def test_partial_render_then_stable_complete_cards(self, cards, candidates):
        complete = [OLD, card(status="Anulowana")]
        cards.side_effect = [[OLD], complete, complete]
        clock = self.Clock()
        result, diagnostic = rt.capture_stable_booking_baseline(
            "h", 1, "target", timeout=4, interval=0.5,
            monotonic=clock.monotonic, sleep=clock.sleep,
        )
        self.assertEqual(result, complete)
        self.assertEqual(diagnostic, [{"tag": "div"}])
        self.assertEqual(cards.call_count, 3)

    @mock.patch.object(rt, "capture_booking_diagnostic_candidates", return_value=[])
    @mock.patch.object(rt, "capture_booking_cards")
    def test_changing_cards_are_not_immediately_stable(self, cards, candidates):
        cards.side_effect = [[OLD], [card()], [OLD], [OLD]]
        clock = self.Clock()
        result, _ = rt.capture_stable_booking_baseline(
            "h", 1, "target", timeout=4, interval=0.5,
            monotonic=clock.monotonic, sleep=clock.sleep,
        )
        self.assertEqual(result, [OLD])
        self.assertEqual(cards.call_count, 4)

    @mock.patch.object(rt, "capture_booking_diagnostic_candidates", return_value=[{"tag": "section"}])
    @mock.patch.object(rt, "capture_booking_cards", return_value=[])
    def test_timeout_returns_no_cards_but_retains_diagnostics(self, cards, candidates):
        clock = self.Clock()
        result, diagnostic = rt.capture_stable_booking_baseline(
            "h", 1, "target", timeout=1, interval=0.5,
            monotonic=clock.monotonic, sleep=clock.sleep,
        )
        self.assertEqual(result, [])
        self.assertEqual(diagnostic, [{"tag": "section"}])


class UnchangedEvidenceTests(unittest.TestCase):
    def resolve(self, sequence, elapsed):
        consecutive = 0
        ready = False
        for result, at in zip(sequence, elapsed):
            if result == rt.VERIFIED_SUCCESS:
                return rt.VERIFIED_SUCCESS
            consecutive, ready = rt.update_unchanged_evidence(result, at, consecutive)
        return rt.VERIFIED_UNCHANGED if ready else rt.UNKNOWN

    def test_three_stable_unchanged_observations_after_grace(self):
        result = self.resolve([rt.VERIFIED_UNCHANGED] * 3, [0, 2, 4])
        self.assertEqual(result, rt.VERIFIED_UNCHANGED)

    def test_temporary_unchanged_then_unknown_is_unknown(self):
        result = self.resolve(
            [rt.VERIFIED_UNCHANGED, rt.UNKNOWN, rt.UNKNOWN], [2, 4, 6]
        )
        self.assertEqual(result, rt.UNKNOWN)

    def test_interrupted_unchanged_streak_requires_fresh_complete_streak(self):
        result = self.resolve([
            rt.VERIFIED_UNCHANGED, rt.VERIFIED_UNCHANGED, rt.UNKNOWN,
            rt.VERIFIED_UNCHANGED, rt.VERIFIED_UNCHANGED,
        ], [0, 2, 4, 6, 8])
        self.assertEqual(result, rt.UNKNOWN)

    def test_success_after_unchanged_has_priority(self):
        result = self.resolve([
            rt.VERIFIED_UNCHANGED, rt.VERIFIED_UNCHANGED, rt.VERIFIED_SUCCESS,
        ], [0, 2, 4])
        self.assertEqual(result, rt.VERIFIED_SUCCESS)


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

    @mock.patch.object(rt, "capture_booking_diagnostic_candidates", return_value=[{"tag": "div"}])
    @mock.patch.object(rt, "capture_booking_cards", return_value=[])
    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "navigate_target")
    @mock.patch.object(cdp_client, "create_page_target")
    def test_diagnostic_candidates_are_recorded_when_card_parser_finds_nothing(
            self, create, navigate, page, cards, candidates):
        transaction = cdp_client.PageTarget("tx", "", "", "ws://tx")
        create.return_value = cdp_client.PageTarget("verify", "", "", "ws://verify")
        stable = {"url": "https://info-kierowca.pl/summary", "buttons": [], "forms": [], "dialogs": []}
        page.return_value = stable
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "candidate-trace")
        recorder.data["post_submit_pages"].append(stable)
        result = rt.run_post_submit(
            "h", 1, transaction, recorder, timeout=0, interval=0,
            monotonic=mock.Mock(side_effect=[0, 0, 0, 1]), sleep=lambda _: None,
        )
        self.assertEqual(result, rt.UNKNOWN)
        self.assertEqual(
            recorder.data["verification_attempts"][0]["diagnostic_candidates"],
            [{"tag": "div"}],
        )

    @mock.patch.object(rt, "capture_booking_diagnostic_candidates", return_value=[])
    @mock.patch.object(rt, "capture_booking_cards", return_value=[OLD])
    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "navigate_target")
    @mock.patch.object(cdp_client, "create_page_target")
    def test_further_confirmation_overrides_stable_unchanged(
            self, create, navigate, page, cards, candidates):
        transaction = cdp_client.PageTarget("tx", "", "", "ws://tx")
        create.return_value = cdp_client.PageTarget("verify", "", "", "ws://verify")
        before = {"url": "https://info-kierowca.pl/summary", "buttons": [], "forms": [], "dialogs": []}
        after = {"url": "https://info-kierowca.pl/next", "buttons": [{"text": "Confirm again", "disabled": False}], "forms": [{}], "dialogs": []}
        page.side_effect = [after, after, after, after]
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "further-step")
        recorder.data["post_submit_pages"].append(before)
        result = rt.run_post_submit(
            "h", 1, transaction, recorder, timeout=4, interval=0,
            monotonic=mock.Mock(side_effect=[0, 0, 0, 2, 2, 4, 4, 6]),
            sleep=lambda _: None,
        )
        self.assertEqual(result, rt.NEEDS_FURTHER_CONFIRMATION)

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

    @mock.patch.object(rt, "capture_booking_diagnostic_candidates", return_value=[])
    @mock.patch.object(rt, "capture_booking_cards", return_value=[OLD])
    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "navigate_target")
    @mock.patch.object(cdp_client, "create_page_target")
    def test_transaction_disappearance_after_stable_unchanged_stays_unknown(
            self, create, navigate, page, cards, candidates):
        transaction = cdp_client.PageTarget("tx", "", "", "ws://tx")
        create.return_value = cdp_client.PageTarget("verify", "", "", "ws://verify")
        stable = {"url": "https://info-kierowca.pl/summary", "buttons": [],
                  "forms": [], "dialogs": []}
        page.side_effect = [
            stable, stable, stable, stable,
            cdp_client.StaleTargetError("transaction closed"),
        ]
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "tx-lost-after-unchanged")
        recorder.data["post_submit_pages"].append(stable)
        clock = mock.Mock(side_effect=[0, 0, 0, 2, 2, 4, 4, 6, 6])

        result = rt.run_post_submit(
            "h", 1, transaction, recorder, timeout=10, interval=0,
            monotonic=clock, sleep=lambda _: None,
        )

        self.assertEqual(result, rt.UNKNOWN)
        self.assertEqual(recorder.data["final_outcome"], rt.UNKNOWN)
        self.assertEqual(
            [attempt["result"] for attempt in recorder.data["verification_attempts"]],
            [rt.VERIFIED_UNCHANGED] * 3,
        )

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


class FakeConfirmPage:
    """Stands in for a page whose confirm button is clicked through CDP.

    Mirrors the real contract that makes the retry safe: the marker is set by
    the click expression itself, in the page, *before* the click — so it is
    already true even when we never get the response back.
    """

    def __init__(self, *, has_button=True, lose_click_response=False, probe_error=False):
        self.has_button = has_button
        self.lose_click_response = lose_click_response
        self.probe_error = probe_error
        self.marker = False
        self.clicks = 0

    def evaluate(self, host, port, js, target=None):
        if "__ikw_clickByText" in js:
            if self.marker:
                return {"clicked": False, "reason": "already_clicked"}
            if not self.has_button:
                return {"clicked": False, "reason": "not_found"}
            self.marker = True
            self.clicks += 1
            if self.lose_click_response:
                raise TimeoutError("socket timed out after the click ran")
            return {"clicked": True, "reason": "clicked", "matched_text": "Potwierdź"}
        if self.probe_error:
            raise TimeoutError("probe timed out")
        return self.marker


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ConfirmClickOnceTests(unittest.TestCase):
    def click(self, page, timeout=5):
        clock = Clock()
        with contextlib.redirect_stdout(io.StringIO()):
            return browser.click_confirm_once(
                "h", 1, browser.CONFIRM_SUMMARY_TEXT, timeout=timeout,
                evaluate=page.evaluate, sleep=clock.sleep, monotonic=clock.monotonic,
            )

    def test_a_normal_click_happens_exactly_once(self):
        page = FakeConfirmPage()
        self.assertEqual(self.click(page), browser.CONFIRM_CLICKED)
        self.assertEqual(page.clicks, 1)

    def test_a_lost_response_is_never_retried_into_a_second_submission(self):
        # cdp_call()'s socket has a 5s timeout and Runtime.evaluate runs to
        # completion in the page regardless of whether we read the reply, so a
        # timed-out confirm may well have submitted the reservation change.
        page = FakeConfirmPage(lose_click_response=True)
        self.assertEqual(self.click(page), browser.CONFIRM_ALREADY_CLICKED)
        self.assertEqual(page.clicks, 1)

    def test_a_failing_probe_never_falls_through_to_a_click(self):
        page = FakeConfirmPage(probe_error=True)
        self.assertIsNone(self.click(page))
        self.assertEqual(page.clicks, 0)

    def test_a_button_that_never_appears_submits_nothing(self):
        page = FakeConfirmPage(has_button=False)
        self.assertIsNone(self.click(page))
        self.assertEqual(page.clicks, 0)

    def test_the_marker_is_set_before_the_click_and_cleared_only_on_failure(self):
        js = browser.confirm_click_js(browser.CONFIRM_SUMMARY_TEXT)
        set_at = js.index("__ikw_confirmMarker('set')")
        click_at = js.index("__ikw_clickByText(text,")
        clear_at = js.index("__ikw_confirmMarker('clear')")
        self.assertLess(set_at, click_at)
        self.assertLess(click_at, clear_at)
        self.assertIn("sessionStorage", js)

    def test_the_confirm_button_does_not_go_through_the_retrying_poller(self):
        source = inspect.getsource(browser.try_select_target_slot)
        self.assertIn("click_confirm_once(host, port, CONFIRM_SUMMARY_TEXT", source)
        self.assertNotIn("wait_and_click_enabled(host, port, CONFIRM_SUMMARY_TEXT", source)


class VerificationRetryTests(unittest.TestCase):
    @mock.patch.object(rt, "capture_booking_diagnostic_candidates", return_value=[])
    @mock.patch.object(rt, "capture_booking_cards")
    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "navigate_target")
    @mock.patch.object(cdp_client, "create_page_target")
    def test_a_transient_error_does_not_disable_verification(
            self, create, navigate, page, cards, candidates):
        # The first verification attempt runs right after the verifier target
        # is created and pointed at /cases — the likeliest moment for a
        # destroyed execution context or a socket timeout. That used to clear
        # verification_target permanently, so a reschedule that really did
        # succeed could only ever report UNKNOWN.
        transaction = cdp_client.PageTarget("tx", "", "", "ws://tx")
        create.return_value = cdp_client.PageTarget("verify", "", "", "ws://verify")
        stable = {"url": "https://info-kierowca.pl/summary", "buttons": [], "forms": [], "dialogs": []}
        page.return_value = stable
        cards.side_effect = [RuntimeError("execution context destroyed"), [OLD], [card()]]
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "transient")
        recorder.data["post_submit_pages"].append(stable)
        with contextlib.redirect_stdout(io.StringIO()):
            result = rt.run_post_submit(
                "h", 1, transaction, recorder, timeout=2, interval=0,
                monotonic=mock.Mock(side_effect=[0, 0, 0, 1, 1, 2, 2, 3]),
                sleep=lambda _: None,
            )
        self.assertEqual(result, rt.VERIFIED_SUCCESS)
        self.assertEqual(cards.call_count, 3)
        states = [entry["state"] for entry in recorder.data["states"]]
        self.assertIn("verification_attempt_failed", states)
        self.assertNotIn("verification_target_lost", states)

    @mock.patch.object(rt, "capture_booking_diagnostic_candidates", return_value=[])
    @mock.patch.object(rt, "capture_booking_cards",
                       side_effect=cdp_client.StaleTargetError("verifier closed"))
    @mock.patch.object(rt, "capture_page")
    @mock.patch.object(cdp_client, "navigate_target")
    @mock.patch.object(cdp_client, "create_page_target")
    def test_a_target_that_is_gone_stops_verification(
            self, create, navigate, page, cards, candidates):
        transaction = cdp_client.PageTarget("tx", "", "", "ws://tx")
        create.return_value = cdp_client.PageTarget("verify", "", "", "ws://verify")
        stable = {"url": "https://info-kierowca.pl/summary", "buttons": [], "forms": [], "dialogs": []}
        page.return_value = stable
        recorder = rt.DiagnosticRecorder(TARGET, [OLD], "terminal")
        recorder.data["post_submit_pages"].append(stable)
        with contextlib.redirect_stdout(io.StringIO()):
            result = rt.run_post_submit(
                "h", 1, transaction, recorder, timeout=2, interval=0,
                monotonic=mock.Mock(side_effect=[0, 0, 0, 1, 1, 3]),
                sleep=lambda _: None,
            )
        self.assertEqual(result, rt.UNKNOWN)
        self.assertEqual(cards.call_count, 1)
        self.assertIn(
            "verification_target_lost",
            [entry["state"] for entry in recorder.data["states"]],
        )


class ConfirmPreconditionTests(unittest.TestCase):
    def test_only_a_strictly_earlier_date_may_be_confirmed(self):
        config = {"current_slot_date": "2026-08-20"}
        earlier = datetime.datetime.fromisoformat("2026-08-10T12:30:00")
        same_day = datetime.datetime.fromisoformat("2026-08-20T08:00:00")
        later = datetime.datetime.fromisoformat("2026-08-25T08:00:00")
        self.assertTrue(browser.target_beats_current_slot(earlier, config))
        self.assertFalse(browser.target_beats_current_slot(same_day, config))
        self.assertFalse(browser.target_beats_current_slot(later, config))

    def test_an_unusable_current_slot_date_fails_closed(self):
        target = datetime.datetime.fromisoformat("2026-08-10T12:30:00")
        for config in ({}, {"current_slot_date": ""}, {"current_slot_date": "not a date"}):
            with self.subTest(config=config):
                self.assertFalse(browser.target_beats_current_slot(target, config))

    def test_it_rereads_config_when_none_is_supplied(self):
        target = datetime.datetime.fromisoformat("2026-08-10T12:30:00")
        with mock.patch.object(browser, "read_config",
                               return_value={"current_slot_date": "2026-08-20"}) as read:
            self.assertTrue(browser.target_beats_current_slot(target))
        read.assert_called_once_with()


class OutcomeReportingTests(unittest.TestCase):
    def test_a_failed_diagnostic_save_never_stops_the_outcome_push(self):
        recorder = mock.Mock()
        recorder.save.side_effect = OSError("read-only file system")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(browser.save_diagnostic(recorder))
        self.assertIn("Couldn't save", out.getvalue())

    def test_push_notifications_never_carry_the_home_directory_path(self):
        path = Path.home() / ".local/state/info-kierowca-notifier/reschedule-diagnostics/x.json"
        reference = browser.diagnostic_reference(path)
        self.assertIn("x.json", reference)
        self.assertNotIn(str(Path.home()), reference)
        self.assertEqual(browser.diagnostic_reference(None), "no diagnostic file")


class ConfirmCooldownTests(unittest.TestCase):
    def test_an_unwritable_cooldown_reports_failure_instead_of_passing_silently(self):
        with mock.patch.object(browser, "write_private_json",
                               side_effect=OSError("no space left on device")):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertFalse(browser.record_confirm_cooldown())
        self.assertIn("NOT armed", out.getvalue())

    def test_an_abandoned_attempt_gives_the_gate_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cooldown"
            with mock.patch.object(browser, "RESCHEDULE_CONFIRM_COOLDOWN_FILE", path):
                self.assertTrue(browser.record_confirm_cooldown("LAUNCHED"))
                self.assertTrue(path.exists())
                self.assertTrue(browser.release_confirm_cooldown())
                self.assertFalse(path.exists())
                self.assertTrue(browser.release_confirm_cooldown())

    def test_state_files_are_owner_only_from_the_moment_they_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            browser.write_private_json(path, {"a": 1})
            self.assertEqual(json.loads(path.read_text()), {"a": 1})
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_a_failed_write_leaves_no_temporary_file_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            with mock.patch.object(json, "dumps", side_effect=ValueError("boom")):
                with self.assertRaises(ValueError):
                    browser.write_private_json(path, {"a": 1})
            self.assertEqual(list(Path(tmp).glob("*")), [])


class LaunchGateTests(unittest.TestCase):
    def _trigger(self, cooldown_armed=True, cooldown_active=False):
        """Run trigger_open_browser() with the browser and child process stubbed.

        Everything the assertions need is captured before the temp dir goes
        away, since the log file lives inside it.
        """
        config = {"auto_select_slot": True, "auto_confirm_reschedule": True}
        hit = {"word": CENTER, "exam_type": "Practice",
               "datetime": "2026-08-10T12:30:00", "places": 1}
        with mock.patch.object(booking_launch.chrome, "chrome_available", return_value=True), \
             mock.patch.object(booking_launch.urllib.request, "urlopen",
                               side_effect=OSError("nothing listening")), \
             mock.patch.object(booking_launch.open_logged_in_browser,
                               "record_confirm_cooldown",
                               return_value=cooldown_armed) as record, \
             mock.patch.object(booking_launch, "confirm_reschedule_cooldown_active",
                               return_value=cooldown_active), \
             mock.patch.object(booking_launch.subprocess, "Popen") as popen, \
             tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "reschedule.log"
            with mock.patch.object(booking_launch, "RESCHEDULE_LOG_FILE", log):
                outcome = booking_launch.trigger_open_browser(
                    logging.getLogger("test"), config, target_hit=hit
                )
            return {
                "outcome": outcome,
                "cmd": list(popen.call_args.args[0]) if popen.call_args else [],
                "record": record,
                "log_mode": log.stat().st_mode & 0o777 if log.exists() else None,
                "call_order": [
                    name for name in ("cooldown", "popen")
                    if (record.called if name == "cooldown" else popen.called)
                ],
            }

    def test_the_cooldown_is_armed_before_the_child_is_launched(self):
        result = self._trigger()
        self.assertEqual(result["outcome"], booking_launch.TRIGGER_LAUNCHED)
        self.assertIn("--confirm-reschedule", result["cmd"])
        result["record"].assert_called_once_with("LAUNCHED")
        # Arming happens while building argv, so the flag can only be present
        # if the gate was already on disk before Popen ran.
        self.assertEqual(result["call_order"], ["cooldown", "popen"])

    def test_an_unarmable_cooldown_withholds_the_confirm_flag(self):
        result = self._trigger(cooldown_armed=False)
        self.assertEqual(result["outcome"], booking_launch.TRIGGER_LAUNCHED)
        self.assertIn("--target-slot", result["cmd"])
        self.assertNotIn("--confirm-reschedule", result["cmd"])

    def test_an_active_cooldown_withholds_the_confirm_flag_without_rearming(self):
        result = self._trigger(cooldown_active=True)
        self.assertEqual(result["outcome"], booking_launch.TRIGGER_LAUNCHED)
        self.assertNotIn("--confirm-reschedule", result["cmd"])
        result["record"].assert_not_called()

    def test_the_reschedule_log_is_owner_only(self):
        result = self._trigger()
        if os.name == "posix":
            self.assertEqual(result["log_mode"], 0o600)


class RemovedDeadCodeTests(unittest.TestCase):
    def test_the_unused_verification_wrappers_are_gone(self):
        # wait_and_verify_booking() had no callers and, given the baseline rule
        # in classify_cards(), returned False unconditionally — reinstating it
        # as a "compatibility wrapper" would have silently broken.
        self.assertFalse(hasattr(browser, "wait_and_verify_booking"))
        self.assertFalse(hasattr(rt, "wait_for_booking_cards"))


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
