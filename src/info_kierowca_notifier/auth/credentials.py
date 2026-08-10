"""Secure operating-system storage for Profil Zaufany credentials."""
import importlib
import sys

import keyring
from keyring.backend import KeyringBackend

SERVICE_NAME = "info-kierowca-notifier/profil-zaufany"


class CredentialStorageUnavailable(RuntimeError):
    pass


class CredentialNotFound(RuntimeError):
    pass


SECURE_BACKEND_MODULES = {
    "win32": ("keyring.backends.Windows",),
    "darwin": ("keyring.backends.macOS",),
    "linux": ("keyring.backends.SecretService", "keyring.backends.libsecret",
              "keyring.backends.kwallet"),
}


def platform_backend_modules(platform=None):
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        platform = "linux"
    return SECURE_BACKEND_MODULES.get(platform, ())


def require_secure_backend(backend, *, platform=None, require_native=True):
    """Apply the production secure-backend policy without touching secrets."""
    try:
        priority = backend.priority
    except Exception as exc:
        raise CredentialStorageUnavailable(
            "Secure operating-system credential storage is unavailable."
        ) from exc
    if not isinstance(backend, KeyringBackend) or priority <= 0:
        raise CredentialStorageUnavailable(
            "Secure operating-system credential storage is unavailable."
        )
    allowed = platform_backend_modules(platform)
    if require_native and (not allowed or not type(backend).__module__.startswith(allowed)):
        raise CredentialStorageUnavailable(
            "A supported secure operating-system credential store is unavailable."
        )
    return backend


def require_packaged_keyring_support(platform=None, backend=None):
    """Validate packaged support without reading or writing a credential."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        # Headless Linux need not have a live Secret Service session, but both
        # supported implementation modules must survive freezing.
        for module in ("keyring.backends.SecretService", "keyring.backends.libsecret"):
            importlib.import_module(module)
        return "linux_secret_service_modules"
    backend = backend or keyring.get_keyring()
    require_secure_backend(backend, platform=platform, require_native=True)
    return f"{type(backend).__module__}.{type(backend).__name__}"


class SecureCredentialStore:
    def __init__(self, backend=None):
        self._require_native = backend is None
        self.backend = backend or keyring.get_keyring()

    def _require_secure_backend(self):
        return require_secure_backend(
            self.backend, require_native=self._require_native
        )

    def available(self):
        try:
            self._require_secure_backend()
            return True
        except CredentialStorageUnavailable:
            return False

    def save(self, username, password):
        if not username or not password:
            raise ValueError("Profil Zaufany username and password are required.")
        try:
            self._require_secure_backend().set_password(SERVICE_NAME, username, password)
        except CredentialStorageUnavailable:
            raise
        except Exception as exc:
            # Some secure backends propagate transport-specific exceptions
            # (for example Jeepney's DBusErrorResponse) instead of wrapping
            # them in keyring.errors.KeyringError.  Keep those implementation
            # details and any backend-provided text out of HTTP responses and
            # logs while still failing closed.
            raise CredentialStorageUnavailable(
                "Secure operating-system credential storage is unavailable."
            ) from exc

    def get(self, username):
        try:
            value = self._require_secure_backend().get_password(SERVICE_NAME, username)
        except CredentialStorageUnavailable:
            raise
        except Exception as exc:
            raise CredentialStorageUnavailable(
                "Secure operating-system credential storage is unavailable."
            ) from exc
        if value is None:
            raise CredentialNotFound("No saved Profil Zaufany password was found.")
        return value

    def delete(self, username):
        if not username:
            return
        try:
            backend = self._require_secure_backend()
            if backend.get_password(SERVICE_NAME, username) is not None:
                backend.delete_password(SERVICE_NAME, username)
        except CredentialNotFound:
            return
        except CredentialStorageUnavailable:
            raise
        except Exception as exc:
            raise CredentialStorageUnavailable(
                "Secure operating-system credential storage is unavailable."
            ) from exc
