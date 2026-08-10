import json
import unittest
from unittest.mock import patch

import info_kierowca_notifier.app as app
from info_kierowca_notifier.auth import session as auto_refresh_session
from info_kierowca_notifier.browser import cdp as cdp_client
from info_kierowca_notifier.auth import credentials as credential_store
from info_kierowca_notifier.web import templates


class PZSecurityTests(unittest.TestCase):
    def payload(self):
        return {
            "login_method": "profil_zaufany", "pz_username": "person",
            "pz_password": "SUPER_SECRET_PZ_PASSWORD_123", "profile_number": "123",
            "ntfy_topic": "topic", "organization_ids": [1], "exam_types": ["Theoretical"],
            "category": 5, "current_slot_date": "2026-09-14",
        }

    def test_password_is_absent_from_config_and_settings_html(self):
        config = app.build_config(self.payload())
        serialized = json.dumps(config)
        page = app.render_wizard(config).decode()
        self.assertNotIn("SUPER_SECRET_PZ_PASSWORD_123", serialized)
        self.assertNotIn("SUPER_SECRET_PZ_PASSWORD_123", page)
        self.assertNotIn("pz_password", config)

    def test_pz_username_inputs_are_concealed_with_reveal_controls(self):
        self.assertIn('id="pz-username" type="password"', templates.LOGIN_PAGE)
        self.assertIn('id="reveal-pz-username"', templates.LOGIN_PAGE)
        page = app.render_wizard(app.build_config(self.payload())).decode()
        self.assertIn('id="settings-pz-username" type="password"', page)
        self.assertIn('id="reveal-pz-username-settings"', page)

    def test_profil_zaufany_is_the_first_run_default(self):
        self.assertIn('id="method-pz" class="on"', templates.LOGIN_PAGE)
        self.assertIn("let loginMethod = 'profil_zaufany'", templates.LOGIN_PAGE)
        self.assertLess(
            templates.LOGIN_PAGE.index('id="method-pz"'),
            templates.LOGIN_PAGE.index('id="method-mobywatel"'),
        )
        page = app.render_wizard().decode()
        self.assertLess(
            page.index('<option value="profil_zaufany">'),
            page.index('<option value="mobywatel">'),
        )
        self.assertIn("updateAuthFields();", page)

    def test_pairing_is_explained_without_an_sms_test_action(self):
        explanation = "Required for automatic Profil Zaufany login"
        self.assertIn(explanation, templates.LOGIN_PAGE)
        self.assertNotIn('id="test-messages"', templates.LOGIN_PAGE)
        page = app.render_wizard(app.build_config(self.payload())).decode()
        self.assertIn(explanation, page)
        self.assertNotIn('id="settings-test-messages"', page)

    def test_session_recovery_is_enabled_without_a_settings_toggle(self):
        submitted = self.payload()
        submitted["auto_refresh_chrome"] = False
        config = app.build_config(submitted)
        self.assertTrue(config["auto_refresh_chrome"])
        page = app.render_wizard(config).decode()
        self.assertNotIn('id="auto_refresh_chrome"', page)
        self.assertNotIn("Reopen Chrome to log back in", page)

    def test_headless_pz_login_is_opt_in_and_rendered_in_settings(self):
        config = app.build_config(self.payload())
        self.assertFalse(config["headless_pz_login"])
        submitted = self.payload()
        submitted["headless_pz_login"] = True
        config = app.build_config(submitted)
        self.assertTrue(config["headless_pz_login"])
        page = app.render_wizard(config).decode()
        self.assertIn('id="headless_pz_login"', page)
        self.assertIn("EXISTING_CONFIG.headless_pz_login === true", page)

    def test_headless_chrome_flag_is_opt_in(self):
        headed = auto_refresh_session.authentication_chrome_args(9333, "/profile")
        headless = auto_refresh_session.authentication_chrome_args(
            9333, "/profile", headless=True
        )
        self.assertNotIn("--headless=new", headed)
        self.assertIn("--headless=new", headless)

    def test_config_cannot_forge_credential_present_marker(self):
        submitted = self.payload()
        submitted["pz_credential_present"] = True
        self.assertNotIn("pz_credential_present", app.build_config(submitted))

    def test_background_relogin_does_not_touch_keyring_without_save_marker(self):
        class UnexpectedStore:
            def get(self, _username):
                raise AssertionError("keyring must not be touched")

        with self.assertRaises(credential_store.CredentialNotFound):
            auto_refresh_session.load_pz_credentials(
                {"login_method": "profil_zaufany", "pz_username": "person"},
                UnexpectedStore(),
            )

    def test_saved_marker_allows_targeted_credential_read(self):
        class Store:
            def get(self, username):
                self.username = username
                return "stored-secret"

        store = Store()
        username, password = auto_refresh_session.load_pz_credentials(
            {
                "login_method": "profil_zaufany",
                "pz_username": "person",
                "pz_credential_present": True,
            },
            store,
        )
        self.assertEqual((username, password), ("person", "stored-secret"))
        self.assertEqual(store.username, "person")

    def test_cdp_function_passes_secrets_as_arguments_not_source(self):
        target = cdp_client.PageTarget("auth", "https://login.gov.pl", "", "ws://h:1/x")
        calls = []
        def fake_call(_sock, req_id, method, params=None):
            calls.append((req_id, method, params))
            if method == "Runtime.evaluate": return {"result": {"objectId": "global"}}
            return {"result": {"value": True}}
        with patch("info_kierowca_notifier.browser.cdp.get_page_target", return_value=target), \
             patch("info_kierowca_notifier.browser.cdp.cdp_socket") as socket_context, \
             patch("info_kierowca_notifier.browser.cdp.cdp_call", side_effect=fake_call):
            socket_context.return_value.__enter__.return_value = object()
            cdp_client.call_function_in_target("h", 1, target, "function(password){return true}", ["SUPER_SECRET_PZ_PASSWORD_123"])
        call = calls[-1][2]
        self.assertNotIn("SUPER_SECRET_PZ_PASSWORD_123", call["functionDeclaration"])
        self.assertEqual(call["arguments"], [{"value": "SUPER_SECRET_PZ_PASSWORD_123"}])
