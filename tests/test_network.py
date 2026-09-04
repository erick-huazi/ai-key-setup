from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import ai_key_setup as app


PROVIDER = app.ProviderConfig(
    "gateway",
    "Gateway",
    "https://api.example.com/v1",
    "model-1",
    "GATEWAY_API_KEY",
)


class CompatibleHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, payload: object) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if (
            self.path != "/v1/models"
            or self.headers.get("Authorization") != "Bearer local-secret"
        ):
            self.send_error(401)
            return
        self._send({"object": "list", "data": [{"id": "model-1"}]})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if (
            self.path != "/v1/responses"
            or self.headers.get("Authorization") != "Bearer local-secret"
            or body.get("model") != "model-1"
        ):
            self.send_error(400)
            return
        self._send({"id": "resp_test", "object": "response", "output": []})


class NetworkSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        app.ACTIVE_SECRETS.clear()

    def tearDown(self) -> None:
        app.ACTIVE_SECRETS.clear()

    def test_cross_origin_redirect_is_blocked(self) -> None:
        handler = app.SameOriginRedirectHandler()
        request = urllib.request.Request(
            "https://api.example.com/v1/models",
            headers={"Authorization": "Bearer secret"},
        )
        with self.assertRaises(app.SetupError):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://other.example/v1/models",
            )

    def test_response_size_is_bounded(self) -> None:
        with self.assertRaises(app.SetupError):
            app._read_limited(io.BytesIO(b"x" * 11), limit=10)

    def test_models_verification_requires_exact_model(self) -> None:
        with mock.patch(
            "ai_key_setup.request_json",
            return_value={"object": "list", "data": [{"id": "model-2"}]},
        ):
            with self.assertRaises(app.SetupError):
                app.verify_models_endpoint(PROVIDER, "secret")

    def test_models_verification_accepts_exact_model(self) -> None:
        with (
            mock.patch(
                "ai_key_setup.request_json",
                return_value={"object": "list", "data": [{"id": "model-1"}]},
            ),
            mock.patch("builtins.print"),
        ):
            app.verify_models_endpoint(PROVIDER, "secret")

    def test_responses_verification_rejects_error_object(self) -> None:
        with mock.patch(
            "ai_key_setup.request_json",
            return_value={"error": {"message": "bad model"}},
        ):
            with self.assertRaises(app.SetupError):
                app.verify_responses_endpoint(PROVIDER, "secret")

    def test_responses_verification_rejects_chat_completions_shape(self) -> None:
        with mock.patch(
            "ai_key_setup.request_json",
            return_value={
                "id": "chatcmpl_test",
                "object": "chat.completion",
                "choices": [],
            },
        ):
            with self.assertRaises(app.SetupError):
                app.verify_responses_endpoint(PROVIDER, "secret")

    def test_both_mode_calls_both_checks(self) -> None:
        with (
            mock.patch("ai_key_setup.verify_models_endpoint") as models,
            mock.patch("ai_key_setup.verify_responses_endpoint") as responses,
        ):
            app.run_network_verification("both", PROVIDER, "secret")
        models.assert_called_once()
        responses.assert_called_once()

    def test_http_error_body_is_redacted(self) -> None:
        secret = "sk" + "-network-secret-value-1234567890"
        app.ACTIVE_SECRETS.append(secret)
        error = urllib.error.HTTPError(
            "https://api.example.com/v1/models",
            401,
            "Unauthorized",
            {},
            io.BytesIO(f'{{"error":"{secret}"}}'.encode()),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(app.SetupError) as captured:
                app.request_json("GET", "https://api.example.com/v1/models", secret)
        self.assertNotIn(secret, str(captured.exception))
        self.assertIn("[REDACTED]", str(captured.exception))

    def test_real_local_openai_compatible_endpoints(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), CompatibleHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        local_provider = app.ProviderConfig(
            "local-test",
            "Local Test",
            f"http://127.0.0.1:{server.server_port}/v1",
            "model-1",
            "LOCAL_TEST_API_KEY",
        )
        try:
            with mock.patch("builtins.print"):
                app.run_network_verification("both", local_provider, "local-secret")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
