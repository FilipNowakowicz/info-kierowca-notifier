"""Regression coverage for wiring build_pkk_prefill() into GET /settings.

Before this, /settings always rendered the wizard with no pkk_profiles,
forcing manual PKK-number/category entry even when the account's own PKK
profile(s) were available via the existing session - see app.py's do_GET and
CLAUDE.md's notes on the /settings bullet. This also covers the three
render_wizard() presentations (0/1/2+ profiles) at the markup level; the
client-side masking/reveal-toggle *behavior* itself has no test coverage in
this codebase's existing pattern (no headless-browser/JS test harness here -
same as every other piece of this page's JS, e.g. the reveal buttons,
calendar, and sliders already in render_wizard()).
"""
import http.client
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import info_kierowca_notifier.app as app


def make_handler(handler_cls, path, headers):
    """A handler instance with no socket behind it - mirrors
    test_local_http_guard.make_handler (kept local here to avoid a
    cross-test-file import this suite has no other precedent for)."""
    handler = handler_cls.__new__(handler_cls)
    handler.path = path
    handler.headers = http.client.HTTPMessage()
    for key, value in headers.items():
        if value is not None:
            handler.headers[key] = value
    handler.sent = []
    handler._send = lambda code, body, content_type=None: handler.sent.append(
        (code, body, content_type)
    )
    handler._send_json = lambda code, obj: handler.sent.append(
        (code, json.dumps(obj).encode(), "application/json")
    )
    return handler


ONE_PROFILE = [{"pkkNumber": "1234567890", "categoryName": "B"}]
TWO_PROFILES = [
    {"pkkNumber": "1234567890", "categoryName": "B"},
    {"pkkNumber": "9876543210", "categoryName": "A"},
]


class SettingsPrefillTests(unittest.TestCase):
    def test_settings_fetches_prefill_when_a_session_exists(self):
        handler = make_handler(app.AppHandler, "/settings", {"Host": f"127.0.0.1:{app.PORT}"})
        with patch.object(app.notifier, "SESSION_FILE") as session_file, \
                patch.object(app.notifier, "CONFIG_FILE", Path("/nonexistent-ikw-test/config.json")), \
                patch.object(app.notifier, "load_json", return_value={"cookies": {}}), \
                patch.object(app.client, "fetch_pkk_profiles", return_value=ONE_PROFILE) as fetch:
            session_file.exists.return_value = True
            handler.do_GET()
        fetch.assert_called_once()
        self.assertEqual(handler.sent[0][0], 200)
        page = handler.sent[0][1].decode("utf-8")
        self.assertIn('"pkkNumber": "1234567890"', page)
        self.assertIn('"categoryId": 5', page)

    def test_settings_falls_back_to_no_prefill_without_a_session(self):
        missing = Path("/nonexistent-ikw-test/session.json")
        handler = make_handler(app.AppHandler, "/settings", {"Host": f"127.0.0.1:{app.PORT}"})
        with patch.object(app.notifier, "SESSION_FILE", missing), \
                patch.object(app.notifier, "CONFIG_FILE", Path("/nonexistent-ikw-test/config.json")), \
                patch.object(app.client, "fetch_pkk_profiles") as fetch:
            handler.do_GET()
        fetch.assert_not_called()
        self.assertEqual(handler.sent[0][0], 200)
        page = handler.sent[0][1].decode("utf-8")
        self.assertIn("const PKK_PROFILES = [];", page)

    def test_settings_still_prefills_over_an_existing_saved_config(self):
        # The actual bug report: a screenshot of /settings (config already
        # saved) showing a masked manual field + pills that didn't need to
        # be there, because /settings never called build_pkk_prefill().
        handler = make_handler(app.AppHandler, "/settings", {"Host": f"127.0.0.1:{app.PORT}"})
        saved_config = {"ntfy_topic": "existing-topic", "profile_number": "1234567890", "category": 5}
        with patch.object(app.notifier, "SESSION_FILE") as session_file, \
                patch.object(app.notifier, "CONFIG_FILE") as config_file_mock, \
                patch.object(app.notifier, "load_json", return_value=saved_config), \
                patch.object(app.client, "fetch_pkk_profiles", return_value=ONE_PROFILE) as fetch:
            session_file.exists.return_value = True
            config_file_mock.exists.return_value = True
            handler.do_GET()
        fetch.assert_called_once()
        page = handler.sent[0][1].decode("utf-8")
        self.assertIn('"pkkNumber": "1234567890"', page)
        self.assertIn('"existing-topic"', page)


class RenderWizardPkkProfileMarkupTests(unittest.TestCase):
    """render_wizard()'s output structurally supports all three states; the
    runtime branch on PKK_PROFILES.length is exercised in the browser only
    (see module docstring)."""

    def test_zero_profiles_renders_the_plain_manual_block_unchanged(self):
        page = app.render_wizard().decode("utf-8")
        self.assertIn("const PKK_PROFILES = [];", page)
        self.assertIn('id="pkk-manual-block"', page)
        self.assertIn('id="cat-primary"', page)

    def test_profiles_are_embedded_as_pkk_number_category_id_and_code(self):
        page = app.render_wizard(pkk_profiles=[
            {"pkkNumber": "1234567890", "categoryId": 5, "categoryCode": "B"},
        ]).decode("utf-8")
        self.assertIn('"pkkNumber": "1234567890"', page)
        self.assertIn('"categoryId": 5', page)
        self.assertIn('"categoryCode": "B"', page)

    def test_markup_contains_all_three_presentation_blocks(self):
        page = app.render_wizard().decode("utf-8")
        # Single-profile: reused #profile_number reveal input, moved in and
        # made read-only, plus a plain (non-clickable) category label.
        self.assertIn('id="pkk-single-block"', page)
        self.assertIn('id="pkk-single-category"', page)
        self.assertIn('id="pkk-reveal"', page)
        self.assertIn('id="pkk-manual-cat-label"', page)
        # 2+ profiles: masked dropdown with its own reveal toggle.
        self.assertIn('id="pkk-select-block"', page)
        self.assertIn('id="reveal-pkk-select"', page)
        self.assertIn("function maskedPkk(num)", page)
        self.assertIn("selectRevealed", page)

    def test_two_profiles_are_all_embedded_for_the_dropdown_state(self):
        page = app.render_wizard(pkk_profiles=[
            {"pkkNumber": p["pkkNumber"], "categoryId": app.pkk_category_id(p["categoryName"]),
             "categoryCode": p["categoryName"]}
            for p in TWO_PROFILES
        ]).decode("utf-8")
        self.assertIn('"pkkNumber": "1234567890"', page)
        self.assertIn('"pkkNumber": "9876543210"', page)

    def test_a_hostile_profile_field_cannot_break_out_of_the_script(self):
        hostile = '</script><script>alert(1)</script>'
        page = app.render_wizard(pkk_profiles=[
            {"pkkNumber": hostile, "categoryId": 5, "categoryCode": "B"},
        ]).decode("utf-8")
        self.assertNotIn("</script><script>alert(1)</script>", page)


if __name__ == "__main__":
    unittest.main()
