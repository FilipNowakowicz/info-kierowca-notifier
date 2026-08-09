import os
import ssl
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import tls_transport


class _OKHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, _format, *_args):
        pass


class _LocalTLSServer:
    def __enter__(self):
        if not shutil.which("openssl"):
            raise unittest.SkipTest("openssl is required for local TLS verification tests")
        self.tempdir = tempfile.TemporaryDirectory()
        cert = Path(self.tempdir.name) / "cert.pem"
        key = Path(self.tempdir.name) / "key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert), "-days", "1",
                "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.server = HTTPServer(("127.0.0.1", 0), _OKHandler)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        self.server.socket = server_context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.ca_file = str(cert)
        self.port = self.server.server_port
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.tempdir.cleanup()


class TLSContextTests(unittest.TestCase):
    def setUp(self):
        self.environ = os.environ.copy()
        for name in (tls_transport.APP_CA_BUNDLE_ENV, *tls_transport.STANDARD_CA_ENVS):
            os.environ.pop(name, None)
        tls_transport.reset_for_tests()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.environ)
        tls_transport.reset_for_tests()

    def test_native_context_is_selected_when_available(self):
        native = ssl.create_default_context()
        with mock.patch.object(tls_transport, "_native_context", return_value=native):
            context = tls_transport.ssl_context()
        self.assertIs(context, native)
        self.assertEqual(tls_transport.trust_backend(), "native_truststore")

    def test_explicit_app_bundle_takes_precedence(self):
        import certifi

        os.environ[tls_transport.APP_CA_BUNDLE_ENV] = certifi.where()
        os.environ["SSL_CERT_FILE"] = "/does/not/matter.pem"
        context = tls_transport.ssl_context()
        self.assertEqual(tls_transport.trust_backend(), "app_ca_bundle")
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_missing_explicit_app_bundle_fails_closed(self):
        os.environ[tls_transport.APP_CA_BUNDLE_ENV] = "/missing/ca.pem"
        with self.assertRaises(tls_transport.TLSConfigurationError):
            tls_transport.ssl_context()

    def test_standard_ca_environment_uses_stdlib_default_context(self):
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
        with mock.patch.object(tls_transport, "_native_context") as native, mock.patch.object(
            tls_transport, "_verified_context", return_value=ssl.create_default_context()
        ) as verified:
            tls_transport.ssl_context()
        native.assert_not_called()
        verified.assert_called_once_with()
        self.assertEqual(tls_transport.trust_backend(), "system_override")

    def test_standard_ca_directory_is_honored(self):
        with tempfile.TemporaryDirectory() as ca_dir:
            os.environ["SSL_CERT_DIR"] = ca_dir
            with mock.patch.object(
                tls_transport, "_verified_context", return_value=ssl.create_default_context()
            ) as verified:
                tls_transport.ssl_context()
        verified.assert_called_once_with()
        self.assertEqual(tls_transport.trust_backend(), "system_override")

    def test_bad_standard_ca_path_fails_closed(self):
        os.environ["SSL_CERT_FILE"] = "/missing/explicit-ca.pem"
        with mock.patch.object(tls_transport, "_certifi_context") as fallback:
            with self.assertRaises(tls_transport.TLSConfigurationError):
                tls_transport.ssl_context()
        fallback.assert_not_called()

    def test_certifi_is_verified_fallback_when_native_store_fails(self):
        fallback = ssl.create_default_context()
        with mock.patch.object(tls_transport, "_native_context", side_effect=RuntimeError("unavailable")):
            with mock.patch.object(tls_transport, "_certifi_context", return_value=fallback):
                context = tls_transport.ssl_context()
        self.assertIs(context, fallback)
        self.assertEqual(tls_transport.trust_backend(), "certifi_fallback")

    def test_malformed_explicit_app_bundle_fails_closed(self):
        with tempfile.NamedTemporaryFile() as malformed:
            malformed.write(b"not a CA certificate")
            malformed.flush()
            os.environ[tls_transport.APP_CA_BUNDLE_ENV] = malformed.name
            with mock.patch.object(tls_transport, "_certifi_context") as fallback:
                with self.assertRaises(tls_transport.TLSConfigurationError):
                    tls_transport.ssl_context()
        fallback.assert_not_called()

    def test_context_always_requires_chain_and_hostname_verification(self):
        context = tls_transport._verified_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_untrusted_local_certificate_fails(self):
        with _LocalTLSServer() as server:
            with self.assertRaises(urllib.error.URLError) as raised:
                urllib.request.urlopen(
                    f"https://localhost:{server.port}/", timeout=5,
                    context=tls_transport._verified_context(),
                )
        self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)

    def test_certifi_fallback_rejects_untrusted_certificate(self):
        with _LocalTLSServer() as server:
            with self.assertRaises(urllib.error.URLError) as raised:
                urllib.request.urlopen(
                    f"https://localhost:{server.port}/",
                    timeout=5,
                    context=tls_transport._certifi_context(),
                )
        self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)

    def test_hostname_mismatch_fails_even_when_certificate_is_trusted(self):
        with _LocalTLSServer() as server:
            context = tls_transport._verified_context(server.ca_file)
            with self.assertRaises(urllib.error.URLError) as raised:
                urllib.request.urlopen(
                    f"https://127.0.0.1:{server.port}/", timeout=5, context=context,
                )
        self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)

    def test_diagnostics_do_not_expose_ca_paths(self):
        import certifi

        os.environ[tls_transport.APP_CA_BUNDLE_ENV] = certifi.where()
        with mock.patch.object(tls_transport, "_verified_context", return_value=ssl.create_default_context()):
            details = tls_transport.diagnostics()
        self.assertTrue(details.app_ca_bundle_configured)
        self.assertNotIn("cacert.pem", repr(details))


class TLSRequestResolutionTests(unittest.TestCase):
    URL = "https://example.test/private?token=secret"

    def setUp(self):
        self.environ = os.environ.copy()
        for name in (tls_transport.APP_CA_BUNDLE_ENV, *tls_transport.STANDARD_CA_ENVS):
            os.environ.pop(name, None)
        tls_transport.reset_for_tests()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.environ)
        tls_transport.reset_for_tests()

    def _response(self):
        response = mock.MagicMock()
        response.status = 200
        return response

    def _verification_error(self, verify_code, verify_message):
        error = ssl.SSLCertVerificationError(1, verify_message)
        error.verify_code = verify_code
        error.verify_message = verify_message
        return urllib.error.URLError(error)

    def test_native_verification_success_uses_native_for_application_request(self):
        native = ssl.create_default_context()
        request = urllib.request.Request(self.URL)
        response = self._response()
        with mock.patch.object(tls_transport, "_native_context", return_value=native), mock.patch.object(
            tls_transport, "_probe_verified_origin"
        ) as probe, mock.patch.object(
            tls_transport.urllib.request, "urlopen", return_value=response
        ) as application_open:
            actual = tls_transport.urlopen(request, timeout=7)
        self.assertIs(actual, response)
        probe.assert_called_once_with("https://example.test", native, 7)
        application_open.assert_called_once_with(request, timeout=7, context=native)
        self.assertEqual(tls_transport.trust_backend(self.URL), "native_truststore")

    def test_native_verification_failure_uses_verified_certifi_fallback(self):
        native = ssl.create_default_context()
        certifi_context = ssl.create_default_context()
        verification_error = self._verification_error(
            20, "unable to get local issuer certificate"
        )
        request = urllib.request.Request(self.URL)
        with mock.patch.object(tls_transport, "_native_context", return_value=native), mock.patch.object(
            tls_transport, "_certifi_context", return_value=certifi_context
        ) as fallback, mock.patch.object(
            tls_transport,
            "_probe_verified_origin",
            side_effect=[verification_error, None],
        ) as probe, mock.patch.object(
            tls_transport.urllib.request, "urlopen", return_value=self._response()
        ) as application_open:
            tls_transport.urlopen(request, timeout=4)
        fallback.assert_called_once_with()
        self.assertEqual(
            probe.call_args_list,
            [
                mock.call("https://example.test", native, 4),
                mock.call("https://example.test", certifi_context, 4),
            ],
        )
        application_open.assert_called_once_with(request, timeout=4, context=certifi_context)
        self.assertEqual(tls_transport.trust_backend(self.URL), "certifi_fallback")

    def test_non_tls_network_error_does_not_trigger_ca_fallback(self):
        native = ssl.create_default_context()
        network_error = urllib.error.URLError("name resolution failed")
        with mock.patch.object(tls_transport, "_native_context", return_value=native), mock.patch.object(
            tls_transport, "_probe_verified_origin", side_effect=network_error
        ), mock.patch.object(tls_transport, "_certifi_context") as fallback, mock.patch.object(
            tls_transport.urllib.request, "urlopen"
        ) as application_open:
            with self.assertRaises(urllib.error.URLError):
                tls_transport.urlopen(self.URL, timeout=2)
        fallback.assert_not_called()
        application_open.assert_not_called()

    def test_certifi_verification_failure_fails_before_application_request(self):
        native = ssl.create_default_context()
        certifi_context = ssl.create_default_context()
        native_error = self._verification_error(
            20, "unable to get local issuer certificate"
        )
        certifi_error = self._verification_error(
            20, "unable to get local issuer certificate"
        )
        with mock.patch.object(tls_transport, "_native_context", return_value=native), mock.patch.object(
            tls_transport, "_certifi_context", return_value=certifi_context
        ), mock.patch.object(
            tls_transport,
            "_probe_verified_origin",
            side_effect=[native_error, certifi_error],
        ), mock.patch.object(
            tls_transport.urllib.request, "urlopen"
        ) as application_open:
            with self.assertRaises(urllib.error.URLError) as raised:
                tls_transport.urlopen(self.URL, timeout=2)
        self.assertIs(raised.exception, certifi_error)
        application_open.assert_not_called()

    def test_hostname_mismatch_does_not_trigger_ca_fallback(self):
        native = ssl.create_default_context()
        mismatch = self._verification_error(62, "hostname mismatch")
        with mock.patch.object(tls_transport, "_native_context", return_value=native), mock.patch.object(
            tls_transport, "_probe_verified_origin", side_effect=mismatch
        ), mock.patch.object(tls_transport, "_certifi_context") as fallback, mock.patch.object(
            tls_transport.urllib.request, "urlopen"
        ) as application_open:
            with self.assertRaises(urllib.error.URLError) as raised:
                tls_transport.urlopen(self.URL, timeout=2)
        self.assertIs(raised.exception, mismatch)
        fallback.assert_not_called()
        application_open.assert_not_called()

    def test_expired_certificate_does_not_trigger_ca_fallback(self):
        native = ssl.create_default_context()
        expired = self._verification_error(10, "certificate has expired")
        with mock.patch.object(tls_transport, "_native_context", return_value=native), mock.patch.object(
            tls_transport, "_probe_verified_origin", side_effect=expired
        ), mock.patch.object(tls_transport, "_certifi_context") as fallback, mock.patch.object(
            tls_transport.urllib.request, "urlopen"
        ) as application_open:
            with self.assertRaises(urllib.error.URLError) as raised:
                tls_transport.urlopen(self.URL, timeout=2)
        self.assertIs(raised.exception, expired)
        fallback.assert_not_called()
        application_open.assert_not_called()

    def test_message_fallback_is_limited_to_chain_discovery(self):
        chain_error = urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")
        )
        hostname_error = urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "hostname mismatch")
        )
        expired_error = urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "certificate has expired")
        )
        self.assertTrue(tls_transport._is_trust_chain_verification_error(chain_error))
        self.assertFalse(tls_transport._is_trust_chain_verification_error(hostname_error))
        self.assertFalse(tls_transport._is_trust_chain_verification_error(expired_error))

    def test_explicit_bad_ca_verification_is_not_bypassed(self):
        import certifi

        os.environ[tls_transport.APP_CA_BUNDLE_ENV] = certifi.where()
        explicit = ssl.create_default_context()
        verification_error = self._verification_error(
            20, "unable to get local issuer certificate"
        )
        request = urllib.request.Request(self.URL)
        with mock.patch.object(tls_transport, "_verified_context", return_value=explicit), mock.patch.object(
            tls_transport, "_probe_verified_origin"
        ) as probe, mock.patch.object(tls_transport, "_certifi_context") as fallback, mock.patch.object(
            tls_transport.urllib.request, "urlopen", side_effect=verification_error
        ) as application_open:
            with self.assertRaises(urllib.error.URLError):
                tls_transport.urlopen(request, timeout=3)
        probe.assert_not_called()
        fallback.assert_not_called()
        application_open.assert_called_once_with(request, timeout=3, context=explicit)

    def test_post_body_is_sent_exactly_once_after_backend_resolution(self):
        native = ssl.create_default_context()
        certifi_context = ssl.create_default_context()
        verification_error = self._verification_error(
            20, "unable to get local issuer certificate"
        )
        request = urllib.request.Request(
            self.URL,
            data=b"non-idempotent-payload",
            method="POST",
        )
        with mock.patch.object(tls_transport, "_native_context", return_value=native), mock.patch.object(
            tls_transport, "_certifi_context", return_value=certifi_context
        ), mock.patch.object(
            tls_transport,
            "_probe_verified_origin",
            side_effect=[verification_error, None],
        ), mock.patch.object(
            tls_transport.urllib.request, "urlopen", return_value=self._response()
        ) as application_open:
            tls_transport.urlopen(request, timeout=5)
        application_open.assert_called_once_with(request, timeout=5, context=certifi_context)
        self.assertEqual(request.data, b"non-idempotent-payload")

    def test_origin_resolution_is_cached_without_leaking_path_or_query(self):
        native = ssl.create_default_context()
        with mock.patch.object(tls_transport, "_native_context", return_value=native), mock.patch.object(
            tls_transport, "_probe_verified_origin"
        ) as probe, mock.patch.object(
            tls_transport.urllib.request, "urlopen", return_value=self._response()
        ):
            tls_transport.urlopen(self.URL, timeout=2)
            tls_transport.urlopen("https://example.test/another?password=hidden", timeout=2)
        probe.assert_called_once_with("https://example.test", native, 2)

    def test_probe_uses_bodyless_origin_only_head_request(self):
        context = ssl.create_default_context()
        response = self._response()
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch.object(tls_transport.urllib.request, "build_opener", return_value=opener):
            tls_transport._probe_verified_origin("https://example.test:8443", context, 6)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test:8443/")
        self.assertEqual(request.get_method(), "HEAD")
        self.assertIsNone(request.data)
        self.assertNotIn("secret", request.full_url)
        response.close.assert_called_once_with()


class TLSSourcePolicyTests(unittest.TestCase):
    def test_no_insecure_tls_api_exists_in_project_python(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = (
            "_create_unverified_context",
            "CERT_NONE",
            "check_hostname = False",
        )
        for path in [*root.glob("*.py"), *root.glob("tests/*.py")]:
            if path == Path(__file__):
                continue
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"insecure TLS policy token in {path}")


if __name__ == "__main__":
    unittest.main()
