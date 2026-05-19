from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_tools.openai_compatible import LLMClientError, LLMConfig, OpenAICompatibleClient


DEFAULT_MODELS = [
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "o4-mini",
    "qwen-plus",
    "qwen-max",
    "glm-4-flash",
    "gemini-2.0-flash",
    "claude-3-5-sonnet-20241022",
    "deepseek-v3",
    "deepseek-r1",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 OpenAI 兼容网关上可用的常见模型。")
    parser.add_argument("--base-url", required=True, help="兼容接口 base URL")
    parser.add_argument("--endpoint-path", default="/v1/chat/completions", help="接口路径")
    parser.add_argument("--api-key-env", default="GEEK_API_KEY", help="API Key 环境变量名")
    parser.add_argument("--prompt-key", action="store_true", help="运行时以不回显方式输入 API Key")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS), help="候选模型，逗号分隔")
    parser.add_argument("--timeout-seconds", type=int, default=60, help="超时秒数")
    args = parser.parse_args()

    api_key_value = None
    if args.prompt_key:
        api_key_value = getpass.getpass("请输入 API Key: ")

    candidates = [item.strip() for item in args.models.split(",") if item.strip()]
    if not candidates:
        print("[错误] 候选模型列表不能为空。")
        return 2

    for model in candidates:
        print(f"--- probing: {model} ---")
        try:
            config = LLMConfig.from_sources(
                base_url=args.base_url,
                model=model,
                endpoint_path=args.endpoint_path,
                api_key=api_key_value,
                api_key_env=args.api_key_env,
                temperature=0.0,
                max_tokens=16,
                timeout_seconds=args.timeout_seconds,
            )
            client = OpenAICompatibleClient(config)
            result = client.generate_text(
                system_prompt="You are a health check endpoint.",
                user_prompt="Reply with exactly OK.",
            )
            print(f"SUCCESS {model}: {result.strip()}")
            return 0
        except LLMClientError as exc:
            print(f"FAILED {model}: {exc}")
        except Exception as exc:
            print(f"FAILED {model}: {exc}")

    print("没有探测到可用模型。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())