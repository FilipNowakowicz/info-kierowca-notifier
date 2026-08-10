"""Small, safe ntfy transport shared by every notification caller.

This module deliberately has no dependency on the application's higher-level
modules.  That keeps the browser helpers free of the notifier import cycle and
gives UI callers an honest, structured delivery result.
"""
from __future__ import annotations

from dataclasses import dataclass
import ssl
import urllib.error
import urllib.request
from typing import Optional

from info_kierowca_notifier import tls_transport


NTFY_URL = "https://ntfy.sh"
DEFAULT_TIMEOUT = 15

SUCCESS = "success"
HTTP_ERROR = "http_error"
NETWORK_ERROR = "network_error"
TLS_ERROR = "tls_error"
TLS_CONFIGURATION_ERROR = "tls_configuration_error"
NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class NotificationOutcome:
    """Safe result of a single ntfy request.

    ``detail`` is intentionally suitable for UI/logging: it never contains a
    topic, URL, headers, request body, or an exception string (which can embed
    a private ntfy topic in urllib errors).
    """

    kind: str
    detail: str
    http_status: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.kind == SUCCESS


def _tls_error(error: BaseException) -> bool:
    if isinstance(error, (ssl.SSLError, tls_transport.TLSConfigurationError)):
        return True
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, ssl.SSLError)
    return False


def push_ntfy(topic, title, message, priority="default", tags=None, *, timeout=DEFAULT_TIMEOUT):
    """Send one notification and return its verified delivery outcome.

    A successful outcome requires a 2xx HTTP response. TLS failures fail
    closed through :mod:`tls_transport`; this function never retries with a
    permissive context.
    """
    if not topic:
        return NotificationOutcome(NOT_CONFIGURED, "No notification topic is configured.")

    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    request = urllib.request.Request(
        f"{NTFY_URL}/{topic}", data=message.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with tls_transport.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if 200 <= status < 300:
                return NotificationOutcome(SUCCESS, "Notification accepted by ntfy.", status)
            return NotificationOutcome(HTTP_ERROR, f"ntfy returned HTTP {status}.", status)
    except urllib.error.HTTPError as error:
        return NotificationOutcome(HTTP_ERROR, f"ntfy returned HTTP {error.code}.", error.code)
    except Exception as error:
        if _tls_error(error):
            if isinstance(error, tls_transport.TLSConfigurationError):
                detail = "TLS trust configuration is invalid. Check the configured CA bundle."
                kind = TLS_CONFIGURATION_ERROR
            else:
                detail = "TLS certificate verification failed. Check your system trust settings."
                kind = TLS_ERROR
            return NotificationOutcome(kind, detail)
        return NotificationOutcome(NETWORK_ERROR, "Could not reach ntfy. Check your network connection.")
