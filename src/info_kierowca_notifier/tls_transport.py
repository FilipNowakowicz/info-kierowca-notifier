"""Verified HTTPS context creation shared by the application's clients.

The application must never weaken certificate or hostname verification to
work around a machine's trust-store configuration.  This module makes the
choice observable and keeps the same verified policy across API and ntfy
requests.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Mapping, Optional, Tuple


APP_CA_BUNDLE_ENV = "INFO_KIEROWCA_CA_BUNDLE"
STANDARD_CA_ENVS = ("SSL_CERT_FILE", "SSL_CERT_DIR")
_PROBE_USER_AGENT = "info-kierowca-notifier-tls-probe"
_LOGGER = logging.getLogger(__name__)

# OpenSSL X509_V_ERR values that mean the verifier could not build a chain to
# a trusted issuer. Other verification failures (notably validity dates and
# hostname/IP mismatch) cannot be repaired by changing the CA source and must
# fail immediately. Keep this deliberately narrow.
_TRUST_CHAIN_VERIFY_CODES = frozenset({2, 20, 21})
_TRUST_CHAIN_VERIFY_MESSAGES = (
    "unable to get issuer certificate",
    "unable to get local issuer certificate",
    "unable to verify the first certificate",
)


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
        except (OSError, ssl.SSLError) as exc:
            raise TLSConfigurationError(
                f"{APP_CA_BUNDLE_ENV} is not a usable CA bundle: {exc}"
            ) from exc

    # CPython's default context honors SSL_CERT_FILE and SSL_CERT_DIR. Treat
    # either variable as authoritative: an obvious bad path must fail closed,
    # not be hidden by the automatic certifi fallback below.
    if ssl_cert_file or ssl_cert_dir:
        if ssl_cert_file and not os.path.isfile(ssl_cert_file):
            raise TLSConfigurationError("SSL_CERT_FILE does not name a readable file")
        if ssl_cert_dir and not os.path.isdir(ssl_cert_dir):
            raise TLSConfigurationError("SSL_CERT_DIR does not name a readable directory")
        try:
            return _verified_context(), "system_override"
        except (OSError, ssl.SSLError) as exc:
            raise TLSConfigurationError(
                "The configured SSL_CERT_FILE/SSL_CERT_DIR trust source is unusable"
            ) from exc

    try:
        return _native_context(), "native_truststore"
    except Exception:
        # certifi is a maintained, verified CA bundle.  It is a portability
        # fallback only; it never relaxes chain, hostname, or expiry checks.
        return _certifi_context(), "certifi_fallback"


def ssl_context() -> ssl.SSLContext:
    """Return a cached context that always requires verified HTTPS."""
    return _context_for_environment(_environment_snapshot())[0]


_resolved_contexts: Dict[
    Tuple[Tuple[Optional[str], ...], str], Tuple[ssl.SSLContext, str]
] = {}
_resolution_lock = threading.RLock()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the trust probe on the requested origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _redirect_origin(url: str):
    """(scheme, host, port) for comparing whether a redirect changed origin.

    Deliberately not ``_origin()`` above: that function only tracks https
    origins (it's used to pick a TLS context) and returns None for anything
    non-https, including plain HTTP localhost CDP traffic that also goes
    through ``urlopen()``. Reusing it here would make two *different*
    non-https endpoints both compare as None == None — "same origin" — and
    silently skip stripping credentials on an http-to-http redirect across
    hosts/ports. This helper distinguishes any scheme/host/port combination
    from any other, https included.
    """
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        port = None
    return (parsed.scheme.lower(), parsed.hostname.lower(), port)


class _CookieSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward credential headers across an origin change on redirect.

    The stock HTTPRedirectHandler copies every header from the original
    request — including Cookie and Authorization — onto the redirected
    request regardless of the destination host or scheme. This application's
    info-kierowca.pl session cookie must never leave that origin (see
    client.py's do_request/COOKIE_NAMES): without this, a compromised or
    malicious redirect target could have the cookie handed to it
    automatically. Strip those headers whenever the redirect target's origin
    differs from the request that produced it — including a same-host
    downgrade from https to http, or a cross-port hop between two plain HTTP
    endpoints (see _redirect_origin() above for why a plain scheme/host
    check isn't enough there).
    """

    _CREDENTIAL_HEADERS = ("Cookie", "Authorization")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        if _redirect_origin(req.full_url) != _redirect_origin(newurl):
            for header in self._CREDENTIAL_HEADERS:
                new_req.remove_header(header)
        return new_req


def _origin(url: str) -> Optional[str]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError as exc:
        raise TLSConfigurationError("HTTPS request contains an invalid port") from exc
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    authority = host if port is None else f"{host}:{port}"
    return f"https://{authority}"


def _request_url(request) -> str:
    if isinstance(request, urllib.request.Request):
        return request.full_url
    return str(request)


def _is_trust_chain_verification_error(error: BaseException) -> bool:
    """Recognize only CA-chain failures for which changing roots can help.

    CPython exposes OpenSSL's stable X509 verify code on real verification
    exceptions. Some platform wrappers omit it, so a small exact-purpose
    message fallback covers only issuer/chain-discovery failures. Unknown
    verification failures remain terminal rather than guessing.
    """
    current: Optional[BaseException] = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            verify_code = getattr(current, "verify_code", None)
            if isinstance(verify_code, int):
                return verify_code in _TRUST_CHAIN_VERIFY_CODES
            verify_message = getattr(current, "verify_message", None) or str(current)
            normalized = verify_message.casefold()
            return any(message in normalized for message in _TRUST_CHAIN_VERIFY_MESSAGES)
        if isinstance(current, urllib.error.URLError) and isinstance(
            current.reason, BaseException
        ):
            current = current.reason
            continue
        current = current.__cause__ or current.__context__
    return False


def _probe_verified_origin(origin: str, context: ssl.SSLContext, timeout: float) -> None:
    """Verify an origin without sending application request data.

    A HEAD request is sent only to the origin root, with redirects disabled,
    so no request body, credentials, original path, or query string is sent.
    Any HTTP response proves that the TLS handshake and hostname verification
    completed successfully; HTTP status is deliberately irrelevant here.
    """
    request = urllib.request.Request(
        f"{origin}/",
        headers={"User-Agent": _PROBE_USER_AGENT},
        method="HEAD",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(),
        urllib.request.HTTPSHandler(context=context),
        _NoRedirectHandler(),
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as response:
        # Includes redirect responses because redirects are disabled. TLS has
        # already succeeded, which is the only result this probe needs.
        response.close()
        return
    else:
        response.close()


def _resolved_context(origin: str, timeout: float) -> Tuple[ssl.SSLContext, str]:
    environment = _environment_snapshot()
    key = (environment, origin)
    with _resolution_lock:
        cached = _resolved_contexts.get(key)
        if cached is not None:
            return cached

        context, backend = _context_for_environment(environment)
        if backend != "native_truststore":
            resolved = (context, backend)
        else:
            try:
                _probe_verified_origin(origin, context, timeout)
                resolved = (context, backend)
            except Exception as exc:
                if not _is_trust_chain_verification_error(exc):
                    raise
                fallback = _certifi_context()
                # A failed fallback probe is surfaced as-is. It is never
                # replaced by a permissive context or another trust source.
                _probe_verified_origin(origin, fallback, timeout)
                resolved = (fallback, "certifi_fallback")
                hostname = urllib.parse.urlsplit(origin).hostname or "unknown"
                _LOGGER.warning(
                    "native certificate verification failed; tls_backend=certifi_fallback host=%s",
                    hostname,
                )

        _resolved_contexts[key] = resolved
        return resolved


def trust_backend(url: Optional[str] = None) -> str:
    """Return the selected backend without exposing CA path values.

    When ``url`` is supplied, report an already-resolved origin fallback if
    one exists. This lookup does not initiate network traffic.
    """
    environment = _environment_snapshot()
    if url:
        origin = _origin(url)
        if origin:
            with _resolution_lock:
                resolved = _resolved_contexts.get((environment, origin))
            if resolved is not None:
                return resolved[1]
    return _context_for_environment(environment)[1]


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

    Every request goes through an opener whose redirect handling is
    _CookieSafeRedirectHandler instead of urllib's default — see that
    class's docstring for why: a redirect must never carry this
    application's session cookie (or any Authorization header) to a
    different origin.
    """
    origin = _origin(_request_url(request))
    if origin is None:
        opener = urllib.request.build_opener(_CookieSafeRedirectHandler())
        return opener.open(request, timeout=timeout)
    context, _backend = _resolved_context(origin, timeout)
    # This is the only send of the caller's request. Native/certifi selection
    # has already been resolved with a bodyless, credential-free HEAD probe.
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), _CookieSafeRedirectHandler()
    )
    return opener.open(request, timeout=timeout)


def reset_for_tests() -> None:
    """Clear the process-local context cache after test environment changes."""
    _context_for_environment.cache_clear()
    with _resolution_lock:
        _resolved_contexts.clear()
