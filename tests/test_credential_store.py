import unittest
from keyring.backend import KeyringBackend

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
