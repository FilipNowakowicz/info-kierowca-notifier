import unittest
from keyring.backend import KeyringBackend
from unittest.mock import patch

import credential_store


class MemoryBackend(KeyringBackend):
    priority = 1
    def __init__(self): self.values = {}
    def get_password(self, service, username): return self.values.get((service, username))
    def set_password(self, service, username, password): self.values[(service, username)] = password
    def delete_password(self, service, username): del self.values[(service, username)]


class UnavailableBackend(KeyringBackend):
    priority = 0
    def get_password(self, service, username): return None
    def set_password(self, service, username, password): raise AssertionError("plaintext fallback")
    def delete_password(self, service, username): pass


class TransportFailureBackend(KeyringBackend):
    priority = 1
    def get_password(self, service, username): raise RuntimeError("D-Bus transport details")
    def set_password(self, service, username, password): raise RuntimeError("D-Bus transport details")
    def delete_password(self, service, username): raise RuntimeError("D-Bus transport details")


class CredentialStoreTests(unittest.TestCase):
    def test_save_get_replace_delete(self):
        backend = MemoryBackend(); store = credential_store.SecureCredentialStore(backend)
        store.save("user", "first"); self.assertEqual(store.get("user"), "first")
        store.save("user", "second"); self.assertEqual(store.get("user"), "second")
        store.delete("user"); self.assertRaises(credential_store.CredentialNotFound, store.get, "user")

    def test_unavailable_backend_fails_without_fallback(self):
        store = credential_store.SecureCredentialStore(UnavailableBackend())
        self.assertFalse(store.available())
        self.assertRaises(credential_store.CredentialStorageUnavailable, store.save, "user", "secret")

    def test_missing_password_is_structured(self):
        self.assertRaises(credential_store.CredentialNotFound, credential_store.SecureCredentialStore(MemoryBackend()).get, "user")

    def test_backend_transport_errors_are_safely_normalized(self):
        store = credential_store.SecureCredentialStore(TransportFailureBackend())
        for operation in (
            lambda: store.save("user", "secret"),
            lambda: store.get("user"),
            lambda: store.delete("user"),
        ):
            with self.assertRaisesRegex(
                credential_store.CredentialStorageUnavailable,
                "Secure operating-system credential storage is unavailable",
            ) as caught:
                operation()
            self.assertNotIn("D-Bus transport details", str(caught.exception))

    def test_shared_native_backend_policy_accepts_platform_backend(self):
        backend_type = type(
            "WinVaultKeyring", (MemoryBackend,),
            {"__module__": "keyring.backends.Windows"},
        )
        backend = backend_type()
        self.assertIs(
            credential_store.require_secure_backend(backend, platform="win32"),
            backend,
        )

    def test_shared_native_backend_policy_rejects_wrong_platform_backend(self):
        backend_type = type(
            "WinVaultKeyring", (MemoryBackend,),
            {"__module__": "keyring.backends.Windows"},
        )
        with self.assertRaises(credential_store.CredentialStorageUnavailable):
            credential_store.require_secure_backend(
                backend_type(), platform="darwin"
            )

    def test_linux_packaging_check_imports_secret_service_implementations(self):
        with patch("credential_store.importlib.import_module") as imported:
            detail = credential_store.require_packaged_keyring_support("linux")
        self.assertEqual(detail, "linux_secret_service_modules")
        self.assertEqual(
            [call.args[0] for call in imported.call_args_list],
            ["keyring.backends.SecretService", "keyring.backends.libsecret"],
        )
