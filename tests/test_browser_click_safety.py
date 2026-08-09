import contextlib
import io
import unittest
from unittest.mock import patch

import auto_refresh_session
import open_logged_in_browser


class BrowserClickSafetyTests(unittest.TestCase):
    def test_shared_helpers_reject_sensitive_or_unrelated_controls(self):
        js = auto_refresh_session.CLICKABLE_HELPERS_JS
        for expected in (
            "tag === 'header'", "tag === 'footer'", "logo|footer|header|language",
            "załóż", "przypomnij", "polityka", "help|regulamin|terms",
            "login.gov.pl", "__ikw_pageIsKnownError", "known_error_page",
            r"\b(wstecz", "new URL(href, location.href)", "__ikw_safeMatchedText",
            "[redacted sensitive text]",
        ):
            self.assertIn(expected, js)

    def test_auto_click_returns_structured_diagnostics_only_when_clicked(self):
        clicked = {
            "clicked": True, "requested_label": "gov.pl", "matched_text": "Zaloguj przez gov.pl",
            "page_host": "login.gov.pl", "tag": "BUTTON", "element_id": "chooser",
            "element_class": "tile", "href": "",
        }
        with patch.object(auto_refresh_session.cdp_client, "evaluate_in_page", return_value=clicked):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = auto_refresh_session.try_auto_click("127.0.0.1", 9333, target="correct-tab")
        self.assertEqual(result, clicked)
        output = stream.getvalue()
        self.assertIn("host='login.gov.pl'", output)
        self.assertNotIn("cookie", output.lower())

    def test_sensitive_matched_text_is_redacted_before_returning_or_logging(self):
        result = auto_refresh_session.sanitize_click_diagnostics(
            {"clicked": True, "matched_text": "PKK 12345678901", "page_url": "https://login.gov.pl/path"}
        )
        self.assertEqual(result["matched_text"], "[redacted sensitive text]")
        self.assertEqual(result["page_url"], "https://login.gov.pl/path")

    def test_safe_matched_text_is_preserved(self):
        result = auto_refresh_session.sanitize_click_diagnostics(
            {"clicked": True, "matched_text": "Aplikacja mObywatel"}
        )
        self.assertEqual(result["matched_text"], "Aplikacja mObywatel")

    def test_auto_click_abstains_for_non_click_outcome(self):
        with patch.object(
            auto_refresh_session.cdp_client,
            "evaluate_in_page",
            return_value={"clicked": False, "reason": "ambiguous_match"},
        ):
            self.assertIsNone(auto_refresh_session.try_auto_click("127.0.0.1", 9333, target="correct-tab"))

    def test_browser_click_js_uses_conservative_shared_helper(self):
        js = open_logged_in_browser.click_text_js("Zmień termin")
        self.assertIn("__ikw_clickByText", js)
        self.assertIn("__ikw_isExcludedControl", js)
        self.assertIn("known_error_page", js)
        self.assertNotIn(".click();\n    return true", js)

    def test_enabled_click_requires_exact_label_and_enabled_control(self):
        js = open_logged_in_browser.click_enabled_button_js("Przejdź do podsumowania")
        self.assertIn("true, true", js)
        self.assertIn("aria-disabled", js)

    def test_polling_only_accepts_a_confirmed_click(self):
        with patch.object(
            open_logged_in_browser.cdp_client,
            "evaluate_in_page",
            return_value={"clicked": False, "reason": "ambiguous_match"},
        ), patch.object(open_logged_in_browser.time, "monotonic", side_effect=[0, 21]):
            self.assertFalse(open_logged_in_browser._poll_until_truthy("host", 1, "js", timeout=20))


if __name__ == "__main__":
    unittest.main()
