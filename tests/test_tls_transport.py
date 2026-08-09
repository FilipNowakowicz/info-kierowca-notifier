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
        os.environ["SSL_CERT_FILE"] = "/configured/by/user.pem"
        with mock.patch.object(tls_transport, "_native_context") as native:
            with mock.patch.object(tls_transport, "_verified_context", return_value=ssl.create_default_context()) as verified:
                tls_transport.ssl_context()
        native.assert_not_called()
        verified.assert_called_once_with()
        self.assertEqual(tls_transport.trust_backend(), "system_override")

    def test_certifi_is_verified_fallback_when_native_store_fails(self):
        fallback = ssl.create_default_context()
        with mock.patch.object(tls_transport, "_native_context", side_effect=RuntimeError("unavailable")):
            with mock.patch.object(tls_transport, "_certifi_context", return_value=fallback):
                context = tls_transport.ssl_context()
        self.assertIs(context, fallback)
        self.assertEqual(tls_transport.trust_backend(), "certifi_fallback")

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


if __name__ == "__main__":
    unittest.main()
