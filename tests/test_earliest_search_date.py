"""Regression coverage for issue #58's independent search lower bound."""
import json
import logging
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import app
import notifier


def payload(**overrides):
    value = {
        "profile_number": "PKK-1",
        "ntfy_topic": "test-topic",
        "organization_ids": [26],
        "exam_types": ["Theoretical"],
        "category": 5,
        "current_slot_date": "2026-09-30",
        "poll_interval_seconds": 60,
        "earliest_slot_hour": 0,
        "latest_slot_hour": 24,
    }
    value.update(overrides)
    return value


class SearchStartDateTests(unittest.TestCase):
    def test_missing_and_invalid_values_preserve_today(self):
        today = date(2026, 8, 9)
        self.assertEqual(notifier.search_start_date(None, today=today), today)
        self.assertEqual(notifier.search_start_date("not-a-date", today=today), today)

    def test_date_is_clamped_to_today_and_horizon(self):
        today = date(2026, 8, 9)
        self.assertEqual(notifier.search_start_date("2026-08-01", today=today), today)
        self.assertEqual(
            notifier.search_start_date("2026-10-01", today=today), date(2026, 9, 9)
        )

    def test_config_keeps_old_configs_compatible(self):
        config = app.build_config(payload())
        self.assertEqual(config["search_start_date"], "")

    def test_config_accepts_a_date_and_rejects_an_invalid_one(self):
        with patch("app.datetime") as mocked_datetime:
            mocked_datetime.now.return_value.date.return_value = date(2026, 8, 9)
            mocked_datetime.fromisoformat.side_effect = __import__("datetime").datetime.fromisoformat
            config = app.build_config(payload(search_start_date="2026-08-20"))
        self.assertEqual(config["search_start_date"], "2026-08-20")
        with self.assertRaisesRegex(ValueError, "Earliest acceptable exam date"):
            app.build_config(payload(search_start_date="20/08/2026"))

    def test_wizard_has_an_optional_localized_date_control(self):
        page = app.render_wizard().decode("utf-8")
        self.assertIn('id="search_start_date"', page)
        self.assertIn("Earliest acceptable exam date (optional)", page)
        self.assertIn("Search from today", page)

    def test_booking_prerequisite_can_be_dismissed_without_touching_config(self):
        page = app.render_wizard().decode("utf-8")
        self.assertIn('id="dismiss-booking-note"', page)
        self.assertIn("BOOKING_PREREQUISITE_DISMISSED_KEY", page)
        self.assertIn("localStorage.setItem(BOOKING_PREREQUISITE_DISMISSED_KEY, '1')", page)

    def test_run_check_sends_lower_bound_and_filters_older_api_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file, session_file = root / "config.json", root / "session.json"
            config = app.build_config(payload(search_start_date="2026-08-20"))
            config_file.write_text(json.dumps(config))
            session_file.write_text(json.dumps({"cookies": {}}))
            calls = []
            results = [{
                "wordId": 26,
                "wordName": "Test WORD",
                "examCollectionForDay": [
                    {"examType": "Theoretical", "theoryDateTime": "2026-08-19T10:00:00", "placeTheoryAmount": 1},
                    {"examType": "Theoretical", "theoryDateTime": "2026-08-20T10:00:00", "placeTheoryAmount": 2},
                ],
            }]

            def request(_url, _session, method="GET", json_body=None):
                calls.append((method, json_body))
                return (204, b"", {}) if method == "GET" else (200, json.dumps(results).encode(), {})

            class FixedDateTime(__import__("datetime").datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 8, 9, 12, 0, 0, tzinfo=tz)

            with patch.object(notifier, "CONFIG_FILE", config_file), \
                 patch.object(notifier, "SESSION_FILE", session_file), \
                 patch.object(notifier, "do_request", side_effect=request), \
                 patch.object(notifier, "datetime", FixedDateTime), \
                 patch.object(notifier, "build_search_organization_ids", return_value=[26] * 5), \
                 patch.object(notifier, "trigger_open_browser"), \
                 patch.object(notifier, "push_ntfy"):
                status = {"paused": False}
                notifier.run_check(logging.getLogger("test"), status)

            self.assertEqual(calls[1][1]["startDate"], "2026-08-20")
            self.assertEqual(
                [hit["datetime"] for hit in status["current_hits"]],
                ["2026-08-20T10:00:00"],
            )
