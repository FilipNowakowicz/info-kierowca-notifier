"""Verified HTTPS context creation shared by the application's clients.

The application must never weaken certificate or hostname verification to
work around a machine's trust-store configuration.  This module makes the
choice observable and keeps the same verified policy across API and ntfy
requests.
"""
from __future__ import annotations

import importlib.util
import os
import ssl
import sys
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping, Optional, Tuple


APP_CA_BUNDLE_ENV = "INFO_KIEROWCA_CA_BUNDLE"
STANDARD_CA_ENVS = ("SSL_CERT_FILE", "SSL_CERT_DIR")


class TLSConfigurationError(RuntimeError):
    """An explicitly configured CA source cannot be used safely."""


@dataclass(frozen=True)
class TrustDiagnostics:
    """Safe-to-log summary of the active certificate-verification policy."""

    backend: str
    platform: str
    openssl: str
    app_ca_bundle_configured: bool
    ssl_cert_file_configured: bool
    ssl_cert_dir_configured: bool
    bundled_ca_available: bool


def _environment_snapshot(environ: Optional[Mapping[str, str]] = None):
    source = os.environ if environ is None else environ
    return tuple(source.get(name) for name in (APP_CA_BUNDLE_ENV, *STANDARD_CA_ENVS))


def _verified_context(cafile: Optional[str] = None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=cafile)
    # Set these explicitly even though create_default_context already does,
    # so future edits cannot accidentally make the transport permissive.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _native_context() -> ssl.SSLContext:
    """Use the platform trust store through truststore when it is available."""
    try:
        import truststore
    except ImportError as exc:  # pragma: no cover - dependencies install this in production
        raise RuntimeError("truststore is unavailable") from exc

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _certifi_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError as exc:  # pragma: no cover - dependencies install this in production
        raise TLSConfigurationError("No verified bundled CA source is available") from exc
    return _verified_context(certifi.where())


@lru_cache(maxsize=8)
def _context_for_environment(
    environment: Tuple[Optional[str], ...],
) -> Tuple[ssl.SSLContext, str]:
    app_bundle, ssl_cert_file, ssl_cert_dir = environment
    if app_bundle:
        if not os.path.isfile(app_bundle):
            raise TLSConfigurationError(
                f"{APP_CA_BUNDLE_ENV} points to a file that cannot be read"
            )
        try:
            return _verified_context(app_bundle), "app_ca_bundle"
        except ssl.SSLError as exc:
            raise TLSConfigurationError(
                f"{APP_CA_BUNDLE_ENV} is not a usable CA bundle: {exc}"
            ) from exc

    # CPython's default context already honors SSL_CERT_FILE and SSL_CERT_DIR.
    # Prefer it whenever either is deliberate user configuration.
    if ssl_cert_file or ssl_cert_dir:
        return _verified_context(), "system_override"

    try:
        return _native_context(), "native_truststore"
    except Exception:
        # certifi is a maintained, verified CA bundle.  It is a portability
        # fallback only; it never relaxes chain, hostname, or expiry checks.
        return _certifi_context(), "certifi_fallback"


def ssl_context() -> ssl.SSLContext:
    """Return a cached context that always requires verified HTTPS."""
    return _context_for_environment(_environment_snapshot())[0]


def trust_backend() -> str:
    """Return the selected backend without exposing CA path values."""
    return _context_for_environment(_environment_snapshot())[1]


def diagnostics() -> TrustDiagnostics:
    """Return safe diagnostics for logs/support reports (no secrets or paths)."""
    app_bundle, cert_file, cert_dir = _environment_snapshot()
    bundled_available = importlib.util.find_spec("certifi") is not None
    return TrustDiagnostics(
        backend=trust_backend(),
        platform=sys.platform,
        openssl=ssl.OPENSSL_VERSION,
        app_ca_bundle_configured=bool(app_bundle),
        ssl_cert_file_configured=bool(cert_file),
        ssl_cert_dir_configured=bool(cert_dir),
        bundled_ca_available=bundled_available,
    )


def urlopen(request, *, timeout: float):
    """Open an HTTPS request with the common verified context.

    HTTP localhost CDP calls can use this too; ``urllib`` ignores the TLS
    context for non-HTTPS URLs.
    """
    return urllib.request.urlopen(request, timeout=timeout, context=ssl_context())


def reset_for_tests() -> None:
    """Clear the process-local context cache after test environment changes."""
    _context_for_environment.cache_clear()
