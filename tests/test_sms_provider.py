import time
import unittest
from unittest.mock import patch

import cdp_client
from sms_provider import GoogleMessagesWebProvider


class SMSProviderTests(unittest.TestCase):
    def setUp(self):
        self.now = time.mktime((2026, 8, 9, 12, 1, 0, 0, 0, -1))
        self.provider = GoogleMessagesWebProvider("h", 1, cdp_client.PageTarget("m", "https://messages.google.com/web", "", "ws"), wall_clock=lambda: self.now)

    def scan(self, result, after=None):
        with patch("sms_provider.cdp_client.get_page_target", return_value=self.provider.target), patch("sms_provider.cdp_client.call_function_in_target", return_value=result):
            return self.provider.get_latest_pz_code(after or self.now - 60)

    def test_latest_valid_code(self):
        result=self.scan({"status":"candidates","candidates":[{"date":"09.08.2026","time":"12:00:10","code":"12345678"},{"date":"09.08.2026","time":"12:00:40","code":"87654321"}]})
        self.assertEqual(result.status, "found"); self.assertEqual(result.code, "87654321")
    def test_stale_code(self): self.assertEqual(self.scan({"status":"candidates","candidates":[{"date":"09.08.2026","time":"11:00:00","code":"12345678"}]}).status, "stale_otp")
    def test_pre_attempt_code(self): self.assertEqual(self.scan({"status":"candidates","candidates":[{"date":"09.08.2026","time":"11:59:00","code":"12345678"}]}, self.now).status, "stale_otp")
    def test_malformed_message(self): self.assertEqual(self.scan({"status":"candidates","candidates":[{"date":"bad","time":"bad","code":"abc"}]}).status, "no_current_otp")
    def test_diagnostic_without_code_is_not_success(self): self.assertEqual(self.scan({"status":"no_current_otp","candidates":[]}).status, "no_current_otp")
    def test_unpaired(self): self.assertEqual(self.scan({"status":"not_paired","candidates":[]}).status, "not_paired")
