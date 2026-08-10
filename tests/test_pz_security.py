import json
import unittest
from unittest.mock import patch

import app
import auto_refresh_session
import cdp_client
import credential_store
import templates


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
        with patch("cdp_client.get_page_target", return_value=target), \
             patch("cdp_client.cdp_socket") as socket_context, \
             patch("cdp_client.cdp_call", side_effect=fake_call):
            socket_context.return_value.__enter__.return_value = object()
            cdp_client.call_function_in_target("h", 1, target, "function(password){return true}", ["SUPER_SECRET_PZ_PASSWORD_123"])
        call = calls[-1][2]
        self.assertNotIn("SUPER_SECRET_PZ_PASSWORD_123", call["functionDeclaration"])
        self.assertEqual(call["arguments"], [{"value": "SUPER_SECRET_PZ_PASSWORD_123"}])
