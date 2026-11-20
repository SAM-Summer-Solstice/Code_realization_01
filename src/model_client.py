"""模型通道适配层。

对外只暴露 `client.chat(messages, case=..., call_index=...)`，返回模型正文。
- RealClient：OpenAI ChatCompletions 兼容协议（official / dev-amd / dev-alt 共用）
- MockClient：确定性模板输出（离线联调、协议回归、校赛演示）

主流程不感知通道差异，切换只改 LLM_PROFILE。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .config import Settings
from .mock_composer import compose_record


class ModelError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _BaseClient:
    calls = 0
    retries = 0

    def chat(self, messages: list[dict], case: dict | None = None, call_index: int = 0) -> str:
        raise NotImplementedError


class RealClient(_BaseClient):
    """OpenAI ChatCompletions 客户端；是否带鉴权头完全由 Settings 决定。"""

    RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

    def __init__(self, settings: Settings):
        self.settings = settings

    def _post(self, payload: dict) -> tuple[int, str]:
        request = urllib.request.Request(
            self.settings.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        if self.settings.api_key:
            request.add_header(
                self.settings.auth_header,
                f"{self.settings.auth_prefix}{self.settings.api_key}",
            )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(2000).decode("utf-8", "replace")
        except TimeoutError as exc:
            return 0, f"Timeout: {exc}"
        except Exception as exc:  # 网络层错误按可重试处理
            return 0, f"{type(exc).__name__}: {exc}"

    def chat(self, messages: list[dict], case: dict | None = None, call_index: int = 0) -> str:
        payload: dict = {
            "model": self.settings.model,
            "messages": messages,
            "stream": False,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
        }
        if self.settings.send_seed:
            payload["seed"] = self.settings.seed
        if self.settings.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.settings.reasoning_effort:
            payload["reasoning_effort"] = self.settings.reasoning_effort
        # 官方问答：qwen3.8-27b 默认开启 Thinking，需显式传 chat_template_kwargs 关闭
        if self.settings.enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": self.settings.enable_thinking}

        # 推理模型的思考 token 也计入 max_tokens：预算不足会被截断成空 content，
        # 因此每次空内容都翻倍预算重试，直到用完重试次数。
        budget = self.settings.max_tokens

        last = "未知错误"
        for attempt in range(self.settings.max_retries + 1):
            if budget > 0:
                payload["max_tokens"] = budget
            self.calls += 1
            status, body = self._post(payload)
            if status == 200:
                try:
                    data = json.loads(body)
                    choice = data["choices"][0]
                    message = choice.get("message") or {}
                except Exception as exc:  # noqa: BLE001
                    raise ModelError("RESPONSE_MALFORMED", f"响应不是预期的 ChatCompletions 结构: {exc}") from exc
                content = (message.get("content") or "").strip()
                if content:
                    return content
                reasoning_tokens = (data.get("usage") or {}).get("reasoning_tokens")
                last = (
                    f"模型返回空 content（finish_reason={choice.get('finish_reason')}, "
                    f"reasoning_tokens={reasoning_tokens}, max_tokens={budget or 'default'}）"
                )
                if attempt >= self.settings.max_retries:
                    raise ModelError("EMPTY_CONTENT", last)
                budget = (budget or 2048) * 2
                self.retries += 1
                continue
            last = f"HTTP {status}: {body[:300]}"
            retryable = status in self.RETRY_STATUS or status == 0
            if not retryable or attempt >= self.settings.max_retries:
                break
            self.retries += 1
            # 429 是"模型容量不足"，退避要比普通 5xx 更保守
            delay = min(5 * (2 ** attempt), 40) if status == 429 else min(1.5 ** attempt, 8)
            time.sleep(delay)
        code = "MODEL_TIMEOUT" if "Timeout" in last or "timed out" in last.lower() else "MODEL_ERROR"
        raise ModelError(code, last)


class MockClient(_BaseClient):
    """确定性 mock：同一输入永远得到同一输出，用于协议回归与演示。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        forced = os.environ.get("MOCK_FORCE_FAIL", "")
        self.forced_fail = {x.strip() for x in forced.split(",") if x.strip()}
        self.latency_ms = int(os.environ.get("MOCK_LATENCY_MS", "0") or 0)

    def chat(self, messages: list[dict], case: dict | None = None, call_index: int = 0) -> str:
        self.calls += 1
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)
        if case is None:
            raise ModelError("MOCK_INTERNAL", "mock 通道需要 case 上下文")
        if case["case_id"] in self.forced_fail:
            raise ModelError("MODEL_TIMEOUT", "模型请求超时，已达到允许的重试次数。")
        if call_index == 0 and not case.get("sources"):
            raise ModelError("CASE_EMPTY", "病例没有可用资料")
        return json.dumps(compose_record(case), ensure_ascii=False)


def build_client(settings: Settings) -> _BaseClient:
    if settings.profile == "mock":
        return MockClient(settings)
    return RealClient(settings)
