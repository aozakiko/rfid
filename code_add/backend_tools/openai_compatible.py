from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class LLMClientError(RuntimeError):
    """OpenAI 兼容接口调用失败。"""


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return normalized


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    endpoint_path: str = "/v1/chat/completions"
    temperature: float = 0.2
    max_tokens: int = 2800
    timeout_seconds: int = 120
    retry_attempts: int = 3
    retry_delay_seconds: float = 1.5

    @classmethod
    def from_sources(
        cls,
        *,
        base_url: str | None = None,
        model: str | None = None,
        endpoint_path: str | None = None,
        api_key: str | None = None,
        api_key_env: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2800,
        timeout_seconds: int = 120,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 1.5,
    ) -> "LLMConfig":
        resolved_base_url = _first_non_empty(
            base_url,
            os.getenv("AI_API_BASE_URL"),
            os.getenv("OPENAI_BASE_URL"),
            os.getenv("GEEK_API_BASE_URL"),
        )
        if not resolved_base_url:
            raise LLMClientError("缺少 base_url。请设置 --base-url 或环境变量 AI_API_BASE_URL。")

        resolved_model = _first_non_empty(
            model,
            os.getenv("AI_API_MODEL"),
            os.getenv("OPENAI_MODEL"),
            os.getenv("GEEK_API_MODEL"),
        )
        if not resolved_model:
            raise LLMClientError("缺少 model。请设置 --model 或环境变量 AI_API_MODEL。")

        resolved_api_key = _first_non_empty(api_key)
        if not resolved_api_key:
            env_names = [api_key_env, "AI_API_KEY", "OPENAI_API_KEY", "GEEK_API_KEY"]
            for env_name in env_names:
                if env_name:
                    env_value = os.getenv(env_name)
                    if env_value and env_value.strip():
                        resolved_api_key = env_value.strip()
                        break
        if not resolved_api_key:
            chosen_name = api_key_env or "AI_API_KEY"
            raise LLMClientError(f"缺少 API Key。请设置环境变量 {chosen_name}。")

        resolved_endpoint = endpoint_path or os.getenv("AI_API_ENDPOINT_PATH") or "/v1/chat/completions"
        if not resolved_endpoint.startswith("/"):
            resolved_endpoint = "/" + resolved_endpoint

        return cls(
            base_url=_normalize_base_url(resolved_base_url),
            api_key=resolved_api_key,
            model=resolved_model,
            endpoint_path=resolved_endpoint,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            timeout_seconds=int(timeout_seconds),
            retry_attempts=max(1, int(retry_attempts)),
            retry_delay_seconds=max(0.0, float(retry_delay_seconds)),
        )


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def list_models(self) -> list[str]:
        response_data = self._request_json("GET", "/v1/models")
        models = response_data.get("data", [])
        results = []
        for item in models:
            if isinstance(item, dict) and item.get("id"):
                results.append(str(item["id"]))
        return results

    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        endpoint = self.config.endpoint_path
        if endpoint == "/v1/responses":
            return self._call_responses(system_prompt, user_prompt)

        try:
            return self._call_chat_completions(system_prompt, user_prompt)
        except LLMClientError:
            if endpoint == "/v1/chat/completions":
                return self._call_responses(system_prompt, user_prompt)
            raise

    def _call_chat_completions(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        response_data = self._request_json("POST", "/v1/chat/completions", payload)
        choices = response_data.get("choices") or []
        if not choices:
            raise LLMClientError("chat.completions 返回中缺少 choices。")

        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            if parts:
                return "\n".join(parts)
        raise LLMClientError("chat.completions 返回中缺少可解析文本。")

    def _call_responses(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "instructions": system_prompt,
            "input": user_prompt,
            "temperature": self.config.temperature,
            "max_output_tokens": self.config.max_tokens,
        }
        response_data = self._request_json("POST", "/v1/responses", payload)

        if response_data.get("output_text"):
            return str(response_data["output_text"])

        output_items = response_data.get("output") or []
        collected: list[str] = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            content_items = item.get("content") or []
            for content_item in content_items:
                if isinstance(content_item, dict) and content_item.get("text"):
                    collected.append(str(content_item["text"]))
        if collected:
            return "\n".join(collected)

        raise LLMClientError("responses 返回中缺少可解析文本。")

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request_path = path
        if self.config.base_url.endswith("/v1") and request_path.startswith("/v1/"):
            request_path = request_path[3:]
        url = self.config.base_url + request_path
        body = None
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        last_error: Exception | None = None
        for attempt in range(1, self.config.retry_attempts + 1):
            req = request.Request(url=url, method=method, data=body, headers=headers)
            try:
                with request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                    raw_body = resp.read().decode("utf-8")
                break
            except error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                should_retry = self._should_retry_http_error(exc.code, err_body, attempt)
                last_error = LLMClientError(f"HTTP {exc.code} 调用失败，已尝试 {attempt}/{self.config.retry_attempts}: {err_body}")
                if should_retry:
                    time.sleep(self.config.retry_delay_seconds * attempt)
                    continue
                raise last_error from exc
            except error.URLError as exc:
                last_error = LLMClientError(f"网络调用失败: {exc}")
                if attempt < self.config.retry_attempts:
                    time.sleep(self.config.retry_delay_seconds * attempt)
                    continue
                raise last_error from exc
            except (ConnectionError, OSError, TimeoutError, socket.timeout) as exc:
                last_error = LLMClientError(f"网络调用失败，已重试 {attempt}/{self.config.retry_attempts}: {exc}")
                if attempt < self.config.retry_attempts:
                    time.sleep(self.config.retry_delay_seconds * attempt)
                    continue
                raise last_error from exc
        else:
            if last_error is not None:
                raise last_error
            raise LLMClientError("接口调用失败，未获得响应。")

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise LLMClientError(f"接口返回不是合法 JSON: {raw_body[:300]}") from exc

        if isinstance(data, dict) and data.get("error"):
            raise LLMClientError(f"接口返回错误: {data['error']}")
        if not isinstance(data, dict):
            raise LLMClientError("接口返回格式非法，预期为 JSON object。")
        return data

    def _should_retry_http_error(self, status_code: int, error_body: str, attempt: int) -> bool:
        if attempt >= self.config.retry_attempts:
            return False
        if status_code in {429, 500, 502, 503, 504}:
            return True
        return False
