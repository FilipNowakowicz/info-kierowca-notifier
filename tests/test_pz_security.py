import json
import unittest
from unittest.mock import patch

import app
import cdp_client


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
