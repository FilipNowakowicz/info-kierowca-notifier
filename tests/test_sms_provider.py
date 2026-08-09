import time
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import cdp_client
from sms_provider import GoogleMessagesWebProvider


class SMSProviderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 9, 12, 1, tzinfo=ZoneInfo("Europe/Warsaw")).timestamp()
        self.provider = GoogleMessagesWebProvider("h", 1, cdp_client.PageTarget("m", "https://messages.google.com/web", "", "ws"), wall_clock=lambda: self.now)

    def scan(self, result, after=None):
        with patch("sms_provider.cdp_client.get_page_target", return_value=self.provider.target), patch("sms_provider.cdp_client.call_function_in_target", return_value=result):
            return self.provider.get_latest_pz_code(after or self.now - 60)

    def test_latest_valid_code(self):
        result=self.scan({"status":"candidates","candidates":[{"date":"09.08.2026","time":"12:00:10","code":"12345678"},{"date":"09.08.2026","time":"12:00:40","code":"87654321"}]})
        self.assertEqual(result.status, "found"); self.assertEqual(result.code, "87654321")
    def test_stale_code(self): self.assertEqual(self.scan({"status":"candidates","candidates":[{"date":"09.08.2026","time":"11:00:00","code":"12345678"}]}).status, "stale_otp")
    def test_pre_attempt_code(self): self.assertEqual(self.scan({"status":"candidates","candidates":[{"date":"09.08.2026","time":"11:59:00","code":"12345678"}]}, self.now).status, "stale_otp")
    def test_current_attempt_sms_before_challenge_render_is_accepted(self):
        result = self.scan(
            {"status": "candidates", "candidates": [
                {"date": "09.08.2026", "time": "12:00:55", "code": "12345678"}
            ]},
            self.now,
        )
        self.assertEqual((result.status, result.code), ("found", "12345678"))

    def test_preexisting_code_is_rejected_inside_tolerance(self):
        with patch("sms_provider.cdp_client.get_page_target", return_value=self.provider.target), \
             patch("sms_provider.cdp_client.call_function_in_target", return_value={
                 "status": "candidates", "candidates": [
                     {"date": "09.08.2026", "time": "12:00:55", "code": "12345678"}
                 ],
             }):
            result = self.provider.get_latest_pz_code(
                self.now, {"12345678"}, tolerance_seconds=20
            )
        self.assertEqual(result.status, "no_current_otp")

    def test_newest_current_attempt_code_wins(self):
        result = self.scan({"status": "candidates", "candidates": [
            {"date": "09.08.2026", "time": "12:00:45", "code": "12345678"},
            {"date": "09.08.2026", "time": "12:00:58", "code": "87654321"},
        ]}, self.now)
        self.assertEqual(result.code, "87654321")
    def test_malformed_message(self): self.assertEqual(self.scan({"status":"candidates","candidates":[{"date":"bad","time":"bad","code":"abc"}]}).status, "no_current_otp")
    def test_diagnostic_without_code_is_not_success(self): self.assertEqual(self.scan({"status":"no_current_otp","candidates":[]}).status, "no_current_otp")
    def test_unpaired(self): self.assertEqual(self.scan({"status":"not_paired","candidates":[]}).status, "not_paired")

    def test_winter_and_summer_timestamps_are_explicit_warsaw_time(self):
        cases = (
            (("15.01.2026", "12:00:00"), 1768474800.0),
            (("15.07.2026", "12:00:00"), 1784109600.0),
        )
        for (date_text, time_text), expected in cases:
            with self.subTest(date=date_text):
                self.assertEqual(self.provider._timestamp(date_text, time_text), expected)

    def test_timestamp_is_independent_of_process_timezone(self):
        expected = self.provider._timestamp("15.07.2026", "12:00:00")
        if not hasattr(time, "tzset"):
            self.skipTest("process timezone switching is unavailable")
        import os
        original = os.environ.get("TZ")
        try:
            for timezone in ("UTC", "America/New_York", "Asia/Tokyo"):
                os.environ["TZ"] = timezone
                time.tzset()
                self.assertEqual(
                    self.provider._timestamp("15.07.2026", "12:00:00"), expected
                )
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_malformed_timestamp_fails_safely(self):
        self.assertIsNone(self.provider._timestamp("31.02.2026", "12:00:00"))

    def test_rejects_retained_target_navigated_off_messages_origin(self):
        for url in (
            "https://unrelated.example/", "http://messages.google.com/web",
            "https://messages.google.com.evil.example/web",
        ):
            with self.subTest(url=url), patch(
                "sms_provider.cdp_client.get_page_target",
                return_value=cdp_client.PageTarget("m", url, "", "ws"),
            ), patch("sms_provider.cdp_client.call_function_in_target") as scan:
                result = self.provider.get_latest_pz_code(self.now - 60)
            self.assertEqual(result.status, "unexpected_messages_origin")
            scan.assert_not_called()

    def test_valid_messages_origin_is_scanned(self):
        with patch("sms_provider.cdp_client.get_page_target", return_value=self.provider.target), \
             patch("sms_provider.cdp_client.call_function_in_target",
                   return_value={"status": "no_current_otp", "candidates": []}) as scan:
            self.assertEqual(
                self.provider.get_latest_pz_code(self.now - 60).status,
                "no_current_otp",
            )
        scan.assert_called_once()
