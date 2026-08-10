import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import info_kierowca_notifier.app as app
from info_kierowca_notifier.auth import credentials as credential_store


class Store:
    def __init__(self, values=None, save_error=False, delete_error=False):
        self.values = dict(values or {})
        self.save_error = save_error
        self.delete_error = delete_error
        self.operations = []

    def save(self, username, password):
        self.operations.append(("save", username))
        if self.save_error:
            raise credential_store.CredentialStorageUnavailable("unavailable")
        self.values[username] = password

    def delete(self, username):
        self.operations.append(("delete", username))
        if self.delete_error:
            raise credential_store.CredentialStorageUnavailable("unavailable")
        self.values.pop(username, None)


class PZSettingsCleanupTests(unittest.TestCase):
    def previous(self):
        return {"login_method": "profil_zaufany", "pz_username": "account_A",
                "pz_credential_present": True}

    def config(self, method="profil_zaufany", username="account_B"):
        return {"login_method": method, "pz_username": username}

    def test_a_to_b_stores_and_persists_b_before_deleting_a(self):
        store = Store({"account_A": "old"})
        events = []
        original_save = store.save
        original_delete = store.delete
        store.save = lambda u, p: (events.append("save_B"), original_save(u, p))[1]
        store.delete = lambda u: (events.append("delete_A"), original_delete(u))[1]
        config = self.config()
        app.persist_settings_credentials(
            self.previous(), config, "new", store=store,
            save_config=lambda value: events.append("persist_B"), logger=Mock(),
        )
        self.assertEqual(events, ["save_B", "persist_B", "delete_A"])
        self.assertEqual(store.values, {"account_B": "new"})

    def test_same_username_password_replacement_does_not_delete(self):
        store = Store({"account_A": "old"})
        app.persist_settings_credentials(
            self.previous(), self.config(username="account_A"), "new",
            store=store, save_config=lambda _value: None, logger=Mock(),
        )
        self.assertEqual(store.values["account_A"], "new")
        self.assertEqual(store.operations, [("save", "account_A")])

    def test_switch_to_mobywatel_retains_pz_credential(self):
        store = Store({"account_A": "old"})
        config = self.config(method="mobywatel", username="")
        app.persist_settings_credentials(
            self.previous(), config, None, store=store,
            save_config=lambda _value: None, logger=Mock(),
        )
        self.assertEqual(store.values, {"account_A": "old"})
        self.assertEqual(config["pz_username"], "account_A")
        self.assertTrue(config["pz_credential_present"])

    def test_failure_saving_b_leaves_a_and_does_not_persist(self):
        store = Store({"account_A": "old"}, save_error=True)
        persisted = Mock()
        with self.assertRaises(credential_store.CredentialStorageUnavailable):
            app.persist_settings_credentials(
                self.previous(), self.config(), "new", store=store,
                save_config=persisted, logger=Mock(),
            )
        self.assertEqual(store.values, {"account_A": "old"})
        persisted.assert_not_called()

    def test_failure_deleting_a_preserves_b_and_config_with_warning(self):
        store = Store({"account_A": "old"}, delete_error=True)
        persisted = []
        logger = Mock()
        warning = app.persist_settings_credentials(
            self.previous(), self.config(), "new", store=store,
            save_config=lambda value: persisted.append(dict(value)), logger=logger,
        )
        self.assertEqual(store.values["account_B"], "new")
        self.assertTrue(persisted[0]["pz_credential_present"])
        self.assertEqual(warning, app.STALE_PZ_CREDENTIAL_WARNING)
        logger.warning.assert_called_once()


class ResetAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config_file = root / "config.json"
        self.session_file = root / "session.json"
        self.config_file.write_text("config")
        self.session_file.write_text("session")

    def reset(self, store):
        return app.reset_account_state(
            {"pz_username": "account_A"}, store=store,
            config_file=self.config_file, session_file=self.session_file,
            logger=Mock(),
        )

    def test_normal_reset_removes_credential_and_local_state(self):
        store = Store({"account_A": "SECRET"})
        self.assertIsNone(self.reset(store))
        self.assertEqual(store.values, {})
        self.assertFalse(self.config_file.exists())
        self.assertFalse(self.session_file.exists())

    def test_unavailable_keyring_still_resets_locally_and_warns_without_secret(self):
        warning = self.reset(Store({"account_A": "SECRET"}, delete_error=True))
        self.assertEqual(warning, app.RESET_CREDENTIAL_WARNING)
        self.assertFalse(self.config_file.exists())
        self.assertFalse(self.session_file.exists())
        self.assertNotIn("SECRET", warning)
        self.assertNotIn("account_A", warning)


if __name__ == "__main__":
    unittest.main()
