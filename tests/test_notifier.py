"""Coverage for a few notifier.py internals fixed by the 2026-08-11 security
and code review: push/trigger de-duplication ignoring "places", exam_type-
keyed field selection instead of an or-fallback, and loop()'s wake_event
clear-before-wait ordering."""
import logging
import threading
import time
import unittest
from unittest import mock

from info_kierowca_notifier import notifier


class PushSignatureTests(unittest.TestCase):
    def test_signature_ignores_places(self):
        """A spot count changing on an otherwise-identical slot (3 -> 2)
        must not look like a new hit and re-fire the urgent push /
        trigger_open_browser() call for a slot already being worked on."""
        hit_a = {
            "word": "WORD Warszawa", "exam_type": "Practice",
            "datetime": "2026-09-01T10:00:00", "places": 3,
        }
        hit_b = dict(hit_a, places=2)
        self.assertEqual(notifier.push_signature(hit_a), notifier.push_signature(hit_b))

    def test_signature_differs_on_real_change(self):
        base = {
            "word": "WORD Warszawa", "exam_type": "Practice",
            "datetime": "2026-09-01T10:00:00", "places": 3,
        }
        for changed_field, changed_value in (
            ("word", "WORD Radom"),
            ("exam_type", "Theoretical"),
            ("datetime", "2026-09-01T11:00:00"),
        ):
            with self.subTest(changed_field=changed_field):
                other = dict(base, **{changed_field: changed_value})
                self.assertNotEqual(notifier.push_signature(base), notifier.push_signature(other))

    def test_signature_round_trips_through_json_as_a_list(self):
        """dash_status["last_push_signature"] is persisted to status.json and
        reloaded on the next process start; json turns a tuple back into a
        list, and a tuple never compares equal to a list, so the signature
        must itself be a list to avoid a spurious re-fire after every
        restart."""
        import json

        hit = {
            "word": "WORD Warszawa", "exam_type": "Practice",
            "datetime": "2026-09-01T10:00:00", "places": 3,
        }
        signature = notifier.push_signature(hit)
        round_tripped = json.loads(json.dumps(signature))
        self.assertEqual(signature, round_tripped)


class ExamSlotFieldsTests(unittest.TestCase):
    def test_practice_hit_uses_practice_fields_even_when_theory_also_present(self):
        """The old `exam.get("theoryDateTime") or exam.get("practiceDateTime")`
        fallback always preferred theory's fields whenever both were present
        on the same record, even for a Practice hit."""
        exam = {
            "theoryDateTime": "2026-09-01T08:00:00",
            "placeTheoryAmount": 5,
            "practiceDateTime": "2026-09-01T14:00:00",
            "placePracticeAmount": 1,
        }
        dt_str, places = notifier.exam_slot_fields(exam, "Practice")
        self.assertEqual(dt_str, "2026-09-01T14:00:00")
        self.assertEqual(places, 1)

    def test_theoretical_hit_uses_theory_fields(self):
        exam = {
            "theoryDateTime": "2026-09-01T08:00:00",
            "placeTheoryAmount": 5,
            "practiceDateTime": "2026-09-01T14:00:00",
            "placePracticeAmount": 1,
        }
        dt_str, places = notifier.exam_slot_fields(exam, "Theoretical")
        self.assertEqual(dt_str, "2026-09-01T08:00:00")
        self.assertEqual(places, 5)

    def test_unrecognized_exam_type_returns_none_pair(self):
        exam = {"theoryDateTime": "2026-09-01T08:00:00", "placeTheoryAmount": 5}
        self.assertEqual(notifier.exam_slot_fields(exam, "Something else"), (None, None))

    def test_missing_field_for_the_matching_type_returns_none_datetime(self):
        exam = {"theoryDateTime": "2026-09-01T08:00:00", "placeTheoryAmount": 5}
        dt_str, places = notifier.exam_slot_fields(exam, "Practice")
        self.assertIsNone(dt_str)
        self.assertIsNone(places)


class LoopWakeEventOrderingTests(unittest.TestCase):
    def test_wake_event_set_during_run_check_does_not_shorten_the_next_wait(self):
        """A /setup save that sets wake_event while run_check() is still
        running must not survive to make the following wait() return
        instantly -- see loop()'s own docstring for the race this guards
        against. Regression test for clearing the event right before
        wait() instead of right after."""
        stop_event = threading.Event()
        wake_event = threading.Event()
        timestamps = []

        def fake_run_check(logger, dash_status):
            timestamps.append(time.monotonic())
            if len(timestamps) == 1:
                # Simulate a concurrent /setup save landing mid run_check().
                wake_event.set()
            else:
                stop_event.set()

        logger = logging.getLogger("test-info-kierowca-notifier-loop")
        with mock.patch.object(notifier, "run_check", side_effect=fake_run_check), \
             mock.patch.object(notifier, "configured_poll_interval", return_value=0.3), \
             mock.patch.object(notifier, "jittered_wait", side_effect=lambda s: s), \
             mock.patch.object(notifier, "save_status"):
            notifier.loop(logger, {}, stop_event=stop_event, wake_event=wake_event)

        self.assertEqual(len(timestamps), 2)
        gap = timestamps[1] - timestamps[0]
        # Buggy (clear-after-wait) behavior: the stale wake_event set inside
        # the first run_check() survives to the wait() call, which returns
        # near-instantly -- gap would be a few milliseconds. Fixed behavior:
        # the pre-wait clear() discards it, so ~the full configured interval
        # elapses before the second run_check().
        self.assertGreaterEqual(gap, 0.2)


if __name__ == "__main__":
    unittest.main()
