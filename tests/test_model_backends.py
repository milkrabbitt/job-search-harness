import io
import json
import os
import socket
import stat
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from job_harness.model import CodexModel
from job_harness.model_backends import (
    HTTPModel, MAX_RESPONSE_BYTES, PROVIDERS, _NoRedirect, _read_response,
    build_model, normalize_model_config, validate_base_url,
)
from job_harness.model_credentials import (
    DEFAULT_KEY_ENV, delete_model_key, model_key_present, read_model_key, save_model_key,
)


def result_body(advice=None, protocol="openai", **overrides):
    text = json.dumps(advice if advice is not None else {"summary": "需进一步核实", "review": ["确认岗位职责"]}, ensure_ascii=False)
    if protocol == "anthropic":
        body = {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 31, "output_tokens": 17}}
    else:
        body = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 31, "completion_tokens": 17}}
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


class ModelConfigTests(unittest.TestCase):
    def test_registry_has_documented_providers_and_no_model_guesses(self):
        self.assertEqual(set(PROVIDERS), {"none", "codex_cli", "openai", "anthropic", "gemini", "minimax",
                         "deepseek", "qwen", "moonshot", "zhipu", "ollama", "lmstudio", "openai_compatible"})
        for name, spec in PROVIDERS.items():
            with self.subTest(name=name):
                self.assertEqual(spec["id"], name)
                self.assertEqual(spec["default_model"], "")
                self.assertEqual(spec["key_required"], spec["requires_key"])
                if spec["base_url"]:
                    self.assertEqual(validate_base_url(spec["base_url"], name), spec["base_url"])

    def test_public_config_drops_secrets_and_accepts_local_model_tags(self):
        config = normalize_model_config({"provider": "ollama", "name": "owner/model:8b-Q4_K_M", "api_key": "private"})
        self.assertNotIn("api_key", config)
        self.assertEqual(config["timeout"], 75)
        self.assertEqual(config["max_output_tokens"], 1024)
        self.assertEqual(config["max_calls"], 3)
        self.assertEqual(config["cost_limit"], "")

    def test_invalid_configuration_rejected(self):
        changes = [{"provider": "unknown"}, {"name": ""}, {"name": "bad\nname"}, {"name": "a" * 129},
                   {"enabled": "perhaps"}, {"max_calls": -1}, {"max_calls": True}, {"max_calls": 1001},
                   {"timeout": 4}, {"timeout": 181}, {"max_output_tokens": 63}, {"max_output_tokens": 4097},
                   {"cost_limit": "1;secret"}, {"api_key_env": "bad env"}, {"executable": "bad\npath"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_model_config(dict({"provider": "openai", "name": "account-model"}, **change))

    def test_disable_and_cli_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(build_model({"provider": "none"}, directory))
            self.assertIsNone(build_model({"provider": "openai", "enabled": False}, directory))
            self.assertIsInstance(build_model({"provider": "codex_cli"}, directory), CodexModel)
            self.assertIsInstance(build_model({"provider": "ollama", "name": "local:8b"}, directory), HTTPModel)

    def test_url_rejects_credential_leaks_and_insecure_remote(self):
        urls = ["http://api.example.test/v1", "https://user:key@api.example.test/v1",
                "https://api.example.test/v1?key=secret", "https://api.example.test/v1#secret",
                "file:///private/key", "https://api.example.test\\evil/v1", "https://api.example.test:0/v1",
                "https://api.example.test:65536/v1", "https://%65xample.test/v1", "https://a.test/../v1",
                "https://a.test/v1/chat/completions", "https://a.test/v1/messages", "https://a.test/\nv1"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_base_url(url, "openai_compatible")

    def test_url_local_boundaries_and_https_custom(self):
        for url in ["http://127.0.0.1:11434/v1", "http://localhost:1234/v1", "http://[::1]:1234/v1"]:
            self.assertEqual(validate_base_url(url, "openai_compatible"), url)
        for url in ["http://127.0.0.1.evil.test/v1", "http://192.168.1.3/v1", "https://remote.test/v1"]:
            with self.assertRaises(ValueError):
                validate_base_url(url, "ollama")
        with self.assertRaises(ValueError):
            validate_base_url("http://localhost/v1", "openai")
        self.assertEqual(validate_base_url("https://gateway.example/v1/", "openai_compatible"), "https://gateway.example/v1")


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = {"provider": "openai"}

    def test_atomic_private_save_read_replace_delete(self):
        self.assertFalse(model_key_present(self.root, self.config))
        save_model_key(self.root, "openai", "first-secret")
        save_model_key(self.root, "openai", "second-secret")
        self.assertEqual(read_model_key(self.root, self.config), "second-secret")
        self.assertTrue(model_key_present(self.root, self.config))
        directory = self.root / "private" / "model-keys"
        self.assertEqual([x.name for x in directory.iterdir()], ["openai.key"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((directory / "openai.key").stat().st_mode), 0o600)
        delete_model_key(self.root, "openai")
        delete_model_key(self.root, "openai")
        self.assertEqual(read_model_key(self.root, self.config), "")

    def test_env_has_explicit_precedence_and_custom_name(self):
        save_model_key(self.root, "openai", "saved-secret")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-secret", "OWN_MODEL_KEY": "custom-secret"}):
            self.assertEqual(read_model_key(self.root, self.config), "env-secret")
            self.assertEqual(read_model_key(self.root, dict(self.config, api_key_env="OWN_MODEL_KEY")), "custom-secret")
        self.assertEqual(read_model_key(self.root, self.config), "saved-secret")

    def test_invalid_keys_and_path_traversal_fail_without_values(self):
        for key in ["", "has whitespace", "a\nprivate-key", "私密", "a" * 4097, None]:
            with self.subTest(key_length=len(key) if isinstance(key, str) else None), self.assertRaises(ValueError):
                save_model_key(self.root, "openai", key)
        for provider in ["../openai", "UPPER", "a/b"]:
            with self.assertRaises(ValueError):
                save_model_key(self.root, provider, "secret")

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions and symlinks")
    def test_insecure_permissions_and_symlinks_are_rejected(self):
        save_model_key(self.root, "openai", "secret")
        path = self.root / "private" / "model-keys" / "openai.key"
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            read_model_key(self.root, self.config)
        self.assertFalse(model_key_present(self.root, self.config))
        path.unlink()
        target = self.root / "target"
        target.write_text("untouched")
        path.symlink_to(target)
        for action in [lambda: save_model_key(self.root, "openai", "other"), lambda: read_model_key(self.root, self.config),
                       lambda: delete_model_key(self.root, "openai")]:
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(target.read_text(), "untouched")

    def test_corrupt_or_oversized_credential_file_fails(self):
        save_model_key(self.root, "openai", "secret")
        path = self.root / "private" / "model-keys" / "openai.key"
        for data in [b"a" * 4097, b"\xff\xff", b"secret\nother"]:
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                read_model_key(self.root, self.config)


class HTTPAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def model(self, provider="openai", **overrides):
        config = {"provider": provider, "name": "test-model", "enabled": True}
        if provider == "openai_compatible":
            config["base_url"] = "https://gateway.example.test/v1"
        config.update(overrides)
        if PROVIDERS[provider]["requires_key"]:
            save_model_key(self.root, provider, "test-private-api-key")
        return build_model(config, self.root)

    def run_response(self, model, raw=None, error=None, evidence=None, run=None):
        opener = Mock()
        if error is not None:
            opener.open.side_effect = error
        else:
            opener.open.return_value = io.BytesIO(result_body() if raw is None else raw)
        with patch("job_harness.model_backends.build_opener", return_value=opener) as factory:
            result = model.evaluate({} if run is None else run, evidence)
        return result, opener, factory

    def test_all_chat_providers_use_expected_auth_endpoint_and_bounded_request(self):
        for provider, spec in PROVIDERS.items():
            if spec["protocol"] != "openai":
                continue
            with self.subTest(provider=provider):
                model = self.model(provider)
                result, opener, _ = self.run_response(model)
                self.assertEqual(result["state"], "done", result)
                self.assertEqual(result["input_tokens"], 31)
                self.assertEqual(result["output_tokens"], 17)
                opener.open.assert_called_once()
                request = opener.open.call_args.args[0]
                payload = json.loads(request.data)
                self.assertEqual(request.full_url, model.config["base_url"] + "/chat/completions")
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(opener.open.call_args.kwargs["timeout"], 75)
                self.assertEqual(payload["model"], "test-model")
                self.assertFalse(payload["stream"])
                cap = "max_completion_tokens" if provider in {"openai", "minimax"} else "max_tokens"
                self.assertEqual(payload[cap], 1024)
                self.assertNotIn("temperature", payload)
                self.assertNotIn("tools", payload)
                if spec["requires_key"]:
                    self.assertEqual(request.get_header("Authorization"), "Bearer test-private-api-key")
                else:
                    self.assertIsNone(request.get_header("Authorization"))
                if provider == "minimax":
                    self.assertTrue(payload["reasoning_split"])
                if provider == "gemini":
                    self.assertEqual(request.get_header("X-goog-api-client"), "job-search-harness/0.2.0")

    def test_native_anthropic_messages_protocol(self):
        model = self.model("anthropic")
        result, opener, _ = self.run_response(model, result_body(protocol="anthropic"))
        self.assertEqual(result["state"], "done")
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "test-private-api-key")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIn("system", payload)
        self.assertEqual(payload["max_tokens"], 1024)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(result["input_tokens"], 31)

    def test_only_allowlisted_features_leave_the_machine(self):
        evidence = {"state": "match", "score": 83, "scope": "ai", "hard": ["private-reason"],
                    "unknown": ["private-question"], "strengths": ["AI 应用或 Agent", "leak-resume"],
                    "chat": "private-chat", "resume": "private-resume", "name": "private-person", "url": "private-url"}
        result, opener, _ = self.run_response(self.model(), evidence=evidence)
        payload = json.loads(opener.open.call_args.args[0].data)
        user_prompt = payload["messages"][1]["content"]
        for secret in ["private-reason", "private-question", "leak-resume", "private-chat", "private-resume", "private-person", "private-url", "test-private-api-key"]:
            self.assertNotIn(secret, user_prompt)
        facts = json.loads(user_prompt.split("\n", 1)[1])
        self.assertEqual(facts, {"state": "match", "score": 83, "scope": "ai", "hard_count": 1,
                                 "unknown_count": 1, "strengths": ["AI 应用或 Agent"]})
        self.assertEqual(result["state"], "done")

    def test_malformed_feature_values_are_sanitized(self):
        for evidence in [{"state": [], "scope": {}, "score": float("nan")}, {"score": 10 ** 1000}]:
            result, _, _ = self.run_response(self.model(), evidence=evidence)
            self.assertEqual(result["state"], "done")
        result, opener, _ = self.run_response(self.model(), evidence="raw document")
        self.assertEqual(result["error_code"], "configuration")
        opener.open.assert_not_called()

    def test_general_scope_is_preserved_without_claiming_target_terms_are_skills(self):
        result, opener, _ = self.run_response(self.model(), evidence={"scope": "general", "strengths": ["目标关键词：销售"]})
        facts = json.loads(json.loads(opener.open.call_args.args[0].data)["messages"][1]["content"].split("\n", 1)[1])
        self.assertEqual(result["state"], "done")
        self.assertEqual(facts["scope"], "general")
        self.assertEqual(facts["strengths"], [])

    def test_run_config_cannot_redirect_provider_or_key(self):
        model = self.model()
        result, opener, _ = self.run_response(model, run={"provider": "anthropic", "base_url": "https://evil.test/v1",
                                                        "api_key_env": "OTHER_KEY", "name": "another-model", "max_output_tokens": 64})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(json.loads(request.data)["max_completion_tokens"], 64)
        self.assertEqual(result["model"], "another-model")

    def test_missing_key_never_makes_request(self):
        model = build_model({"provider": "openai", "name": "test-model"}, self.root)
        result, opener, _ = self.run_response(model)
        self.assertEqual(result["error_code"], "missing_key")
        opener.open.assert_not_called()

    def test_private_key_echo_is_redacted(self):
        result, _, _ = self.run_response(self.model(), result_body({"summary": "test-private-api-key", "review": []}))
        self.assertEqual(result["state"], "done")
        self.assertNotIn("test-private-api-key", json.dumps(result))

    def test_long_escaped_key_is_redacted_before_truncation(self):
        model = self.model()
        secret = "key-" + "a" * 250 + '\\"tail'
        save_model_key(self.root, "openai", secret)
        result, _, _ = self.run_response(model, result_body({"summary": secret, "review": [secret]}))
        self.assertEqual(result["state"], "done")
        self.assertNotIn("key-", result["detail"])
        self.assertNotIn("a" * 20, result["detail"])

    def test_invalid_model_json_has_safe_failure(self):
        values = [{"summary": "ok"}, {"summary": "ok", "review": [], "action": "send"}, {"summary": "", "review": []},
                  {"summary": "ok", "review": ["a"] * 4}, {"summary": "ok", "review": [True]},
                  {"summary": "a" * 1001, "review": []}, ["not an object"]]
        raw_values = [result_body(value) for value in values] + [b"private invalid upstream response", b"[]", b"\xff"]
        for raw in raw_values:
            with self.subTest(raw_length=len(raw)):
                result, _, _ = self.run_response(self.model(), raw)
                self.assertEqual(result["error_code"], "invalid_response")
                self.assertNotIn("private invalid", json.dumps(result))

    def test_refusals_and_truncated_responses_are_not_success(self):
        for reason in ["length", "content_filter"]:
            raw = result_body(choices=[{"message": {"content": "{}"}, "finish_reason": reason}])
            result, _, _ = self.run_response(self.model(), raw)
            self.assertEqual(result["error_code"], "incomplete")
        for reason in ["max_tokens", "refusal"]:
            result, _, _ = self.run_response(self.model("anthropic"), result_body(protocol="anthropic", stop_reason=reason))
            self.assertEqual(result["error_code"], "incomplete")

    def test_invalid_usage_is_not_a_fabricated_cost_estimate(self):
        result, _, _ = self.run_response(self.model(), result_body(usage={"prompt_tokens": True, "completion_tokens": -2}))
        self.assertEqual(result["state"], "done")
        self.assertIsNone(result["input_tokens"])
        self.assertIsNone(result["output_tokens"])

    def test_network_errors_are_safe_single_attempt_failures(self):
        cases = [(401, "authentication"), (403, "authentication"), (429, "rate_limit"), (400, "model_or_protocol"),
                 (404, "model_or_protocol"), (422, "model_or_protocol"), (503, "http_error"), (302, "redirect")]
        for status, expected in cases:
            error_body = io.BytesIO(b"secret upstream error body")
            error = HTTPError("https://example.test", status, "secret reason", {}, error_body)
            result, opener, _ = self.run_response(self.model(), error=error)
            self.assertEqual(result["error_code"], expected)
            self.assertNotIn("secret", result["detail"])
            self.assertTrue(error_body.closed)
            opener.open.assert_called_once()
        for error, expected in [(socket.timeout(), "timeout"), (URLError(socket.timeout()), "timeout"),
                                (URLError("secret host"), "connection"), (IncompleteRead(b"secret"), "connection")]:
            result, opener, _ = self.run_response(self.model(), error=error)
            self.assertEqual(result["error_code"], expected)
            opener.open.assert_called_once()

    def test_redirect_handler_never_returns_forwarded_request(self):
        req = Request("https://first.test/v1", headers={"Authorization": "Bearer secret"})
        with self.assertRaises(HTTPError) as caught:
            _NoRedirect().redirect_request(req, io.BytesIO(b""), 307, "redirect", {}, "https://other.test/v1")
        self.assertEqual(caught.exception.code, 307)
        caught.exception.close()

    def test_response_aggregate_size_limit(self):
        result, opener, _ = self.run_response(self.model(), b" " * (MAX_RESPONSE_BYTES + 100))
        self.assertEqual(result["error_code"], "response_too_large")
        opener.open.assert_called_once()

    def test_response_deadline_does_not_reset_for_chunks(self):
        response = io.BytesIO(b"part one part two")
        with patch("job_harness.model_backends.time.monotonic", side_effect=[1.0, 4.0, 6.0]):
            with self.assertRaises(TimeoutError):
                _read_response(response, 5.0)

    def test_local_models_bypass_proxy_environment(self):
        _, _, factory = self.run_response(self.model("ollama"))
        self.assertTrue(any(getattr(handler, "proxies", None) == {} for handler in factory.call_args.args))


class LocalHTTPIntegrationTests(unittest.TestCase):
    """Exercise urllib framing, redirects and authentication over a real socket."""

    @contextmanager
    def server(self, raw, status=200, headers=None):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                received.append({"path": self.path, "authorization": self.headers.get("Authorization"), "body": json.loads(data)})
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("Runtime does not allow loopback listening")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:" + str(server.server_port) + "/v1", received
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_actual_http_response_and_request_schema(self):
        with tempfile.TemporaryDirectory() as directory, self.server(result_body()) as (url, received), patch.dict(os.environ, {}, clear=True):
            model = build_model({"provider": "openai_compatible", "base_url": url, "name": "local-model:4b"}, directory)
            result = model.evaluate({})
        self.assertEqual(result["state"], "done", result)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["path"], "/v1/chat/completions")
        self.assertEqual(received[0]["body"]["model"], "local-model:4b")
        self.assertIsNone(received[0]["authorization"])

    def test_actual_redirect_is_not_followed_or_retried(self):
        with tempfile.TemporaryDirectory() as directory, self.server(b"private error", 307, {"Location": "/should-not-receive-key"}) as (url, received), patch.dict(os.environ, {}, clear=True):
            save_model_key(directory, "openai_compatible", "integration-private-key")
            model = build_model({"provider": "openai_compatible", "base_url": url, "name": "local-model"}, directory)
            result = model.evaluate({})
        self.assertEqual(result["error_code"], "redirect")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["authorization"], "Bearer integration-private-key")
        self.assertNotIn("private", result["detail"])

    def test_actual_authentication_error_has_no_body_disclosure(self):
        with tempfile.TemporaryDirectory() as directory, self.server(b"sensitive account error", 401) as (url, received), patch.dict(os.environ, {}, clear=True):
            model = build_model({"provider": "openai_compatible", "base_url": url, "name": "local-model"}, directory)
            result = model.evaluate({})
        self.assertEqual(result["error_code"], "authentication")
        self.assertEqual(len(received), 1)
        self.assertNotIn("sensitive", result["detail"])


if __name__ == "__main__":
    unittest.main()
