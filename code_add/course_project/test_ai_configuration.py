"""Exercise live gateway switching against local HTTP services only."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch

from backend_tools.openai_compatible import LLMClientError, LLMConfig, OpenAICompatibleClient
from ai_protocol_optimizer import _build_optimizer_prompt, validate_task_profile
from course_project import web_app
from course_project.test_infrastructure import HTTPTestServer


@contextmanager
def gateway(*, key="gateway-key", chat_only=False, failure=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = json.loads(body) if body else None
            calls.append((self.path, self.headers.get("Authorization"), payload))
            status = 200
            if self.headers.get("Authorization") != f"Bearer {key}":
                status, result = 401, {"error": "wrong key"}
            elif failure:
                status, result = failure, {"error": f"rejected {key}"}
            elif self.path == "/v1/models":
                result = {"data": [{"id": "local-model"}]}
            elif self.path == "/v1/responses" and not chat_only:
                result = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
            elif self.path == "/v1/chat/completions" and chat_only:
                result = {"choices": [{"message": {"content": "OK"}}]}
            else:
                status, result = 404, {"error": "unsupported API route"}
            encoded = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class LiveConfigurationTests(HTTPTestServer):
    def post(self, payload, path="/api/selfopt/ai-models"):
        status, _, body = self.request(path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        return status, json.loads(body)

    def test_change_gateway_key_and_model_without_restarting_app(self):
        with gateway(key="first-key") as (first, first_calls), gateway(key="second-key", chat_only=True) as (second, second_calls):
            for url, key, model, protocol in (
                (first + "/v1", "first-key", "first-model", "Responses"),
                (second, "second-key", "second-model", "Chat Completions"),
            ):
                status, result = self.post({"base_url": url, "api_key_value": key, "model": model, "retry_attempts": 1})
                self.assertEqual(status, 200, result)
                self.assertEqual(result["reply"], "OK")
                self.assertEqual(result["model"], model)
                self.assertEqual(result["protocol"], protocol)
                self.assertNotIn(key, json.dumps(result))
            self.assertEqual([c[0] for c in first_calls], ["/v1/responses"])
            self.assertEqual([c[0] for c in second_calls], ["/v1/responses", "/v1/chat/completions"])
            self.assertTrue(all(c[1] == "Bearer second-key" for c in second_calls))
            self.assertEqual(second_calls[-1][2]["model"], "second-model")

    def test_environment_never_supplies_a_missing_web_key(self):
        environment = {name: "old-secret" for name in ("AI_API_KEY", "OPENAI_API_KEY", "GEEK_API_KEY")}
        with patch.dict(os.environ, environment), gateway() as (url, calls):
            for path in ("/api/selfopt/ai-models", "/api/selfopt/ai-run-async", "/api/selfopt/auto-iterate-async"):
                payload = {"base_url": url, "model": "local-model", "dry_run": False, "api_key_env": "OPENAI_API_KEY"}
                status, result = self.post(payload, path)
                self.assertEqual(status, 400, (path, result))
                self.assertIn("API Key", result["error"])
                self.assertNotIn("old-secret", json.dumps(result))
            self.assertEqual(calls, [])

    def test_model_list_works_before_selecting_a_model(self):
        with gateway() as (url, calls):
            for extra in ({}, {"model": ""}):
                status, result = self.post({"base_url": url + "/v1", "api_key_value": "gateway-key", "mode": "list", **extra})
                self.assertEqual(status, 200, result)
                self.assertEqual(result["models"], ["local-model"])
            self.assertEqual([c[0] for c in calls], ["/v1/models", "/v1/models"])

    def test_gateway_auth_failure_is_visible_and_key_is_redacted(self):
        with gateway(failure=401) as (url, calls):
            status, result = self.post({"base_url": url, "api_key_value": "gateway-key", "model": "local-model", "retry_attempts": 1})
            self.assertEqual(status, 502)
            self.assertIn("401", result["error"])
            self.assertNotIn("gateway-key", json.dumps(result))
            self.assertEqual(len(calls), 1)


class GatewayConfigurationTests(unittest.TestCase):
    def test_custom_task_requires_text_and_is_embedded_in_prompt(self):
        with self.assertRaises(ValueError):
            validate_task_profile("custom_task", "短")
        text = "估计标签 price 的 90 分位数，返回 [estimated_p90]，误差不超过 5%。"
        self.assertEqual(validate_task_profile("custom_task", text), "custom_task")
        prompt = _build_optimizer_prompt(
            scene_name="test",
            parent_a="ModeQuerySEM",
            parent_b="ModeQueryTKQ",
            protocol_name="CustomProtocol",
            task_profile="custom_task",
            custom_task_text=text,
        )
        self.assertIn(text, prompt)
        self.assertIn("任意自定义任务", prompt)

    def test_custom_validation_reports_execution_scope_without_fake_accuracy(self):
        result = web_app.validate_selfopt_protocol(
            {
                "scene": "test",
                "protocol": "ModeQuerySEM",
                "task_profile": "custom_task",
                "custom_task_text": "统计标签价格的 90 分位数并返回列表结果",
                "num_tags": 20,
                "total_runs": 1,
            }
        )
        self.assertEqual(result["semantic_validation"], "not_available")
        self.assertEqual(result["validation_scope"], "execution_and_output_contract")
        self.assertEqual(result["task_profile"], "custom_task")
        self.assertTrue(result["samples"][0]["hit"])

    def test_web_ignores_legacy_environment_and_endpoint_settings(self):
        with patch.dict(os.environ, {"AI_API_BASE_URL": "https://old.invalid", "AI_API_MODEL": "old-model", "AI_API_ENDPOINT_PATH": "/wrong", "GEEK_API_KEY": "old-key"}):
            config = web_app._web_llm_config({"base_url": "https://new.example/v1", "model": "new-model", "api_key_value": "new-key", "endpoint_path": "/wrong", "api_key_env": "GEEK_API_KEY"})
        self.assertEqual((config.base_url, config.model, config.api_key, config.endpoint_path), ("https://new.example/v1", "new-model", "new-key", "auto"))
        self.assertNotIn("new-key", repr(config))

    def test_invalid_or_missing_fields_do_not_fall_back_to_environment(self):
        valid = {"base_url": "https://gateway.example/v1", "model": "local-model", "api_key_value": "key"}
        for name, value in (("base_url", ""), ("model", ""), ("api_key_value", ""), ("api_key_value", "key\nextra"), ("base_url", "file:///tmp/key"), ("base_url", "https://user:secret@gateway.example/v1"), ("base_url", "https://gateway.example?key=secret")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                web_app._web_llm_config({**valid, name: value})

    def test_accepts_base_or_complete_api_url(self):
        for suffix in ("", "/", "/responses", "/chat/completions", "/responses/"):
            with self.subTest(suffix=suffix):
                config = web_app._web_llm_config({"base_url": "https://gateway.example/v1" + suffix, "model": "local-model", "api_key_value": "key"})
                self.assertEqual(config.base_url, "https://gateway.example/v1")

    def test_successful_protocol_is_reused_within_workflow(self):
        with gateway(chat_only=True) as (url, calls):
            client = OpenAICompatibleClient(LLMConfig(base_url=url + "/v1", api_key="gateway-key", model="local-model", endpoint_path="auto", retry_attempts=1))
            for _ in range(2):
                self.assertEqual(client.generate_text(system_prompt="test", user_prompt="test"), "OK")
            self.assertEqual([c[0] for c in calls], ["/v1/responses", "/v1/chat/completions", "/v1/chat/completions"])

    def test_auth_quota_and_server_errors_do_not_switch_protocol(self):
        for status in (401, 403, 429, 503):
            with self.subTest(status=status), gateway(failure=status) as (url, calls):
                client = OpenAICompatibleClient(LLMConfig(base_url=url, api_key="gateway-key", model="local-model", endpoint_path="auto", retry_attempts=1))
                with self.assertRaises(LLMClientError) as caught:
                    client.generate_text(system_prompt="test", user_prompt="test")
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(len(calls), 1)

    def test_generation_and_iteration_pass_request_config_to_optimizer(self):
        payload = {"scene": "test", "parent_a": "ModeQuerySEM", "parent_b": "ModeQueryTKQ", "parents": ["ModeQuerySEM", "ModeQueryTKQ"], "protocol_name": "config_test", "prefix": "config_test", "iterations": 1, "base_url": "https://new.example/v1", "model": "new-model", "api_key_value": "new-key", "dry_run": False}
        with patch.object(web_app, "run_ai_optimization_workflow", return_value={"status": "success"}) as workflow, patch.object(web_app, "store_ai_job", return_value=1), patch.object(web_app, "selfopt_state", return_value={}):
            web_app.run_ai_generation(payload)
            web_app.run_selfopt_auto_iterate(payload)
        self.assertEqual(workflow.call_count, 2)
        for call in workflow.call_args_list:
            config = call.kwargs["llm_config"]
            self.assertEqual((config.base_url, config.api_key, config.model, config.endpoint_path), ("https://new.example/v1", "new-key", "new-model", "auto"))
            self.assertNotIn("api_key_env", call.kwargs)


if __name__ == "__main__":
    unittest.main()
