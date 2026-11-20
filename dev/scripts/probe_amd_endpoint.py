"""AMD 接入点探测脚本（开发期诊断用，不进入提交包）。

用法：
    set AMD_BASE_URL=https://developer.amd.com.cn/radeon/api/v1
    set AMD_API_KEY=<rc-...>            # 可选；缺失时只做无鉴权探测
    set AMD_MODEL=Qwen3.8-27B          # 可选；缺失时只列模型
    python dev/scripts/probe_amd_endpoint.py

设计原则：
- 密钥只从环境变量读取，任何输出都不打印密钥本体；
- 错误原样打印（异常类型 + 文本 + HTTP 状态 + 响应体前 800 字符），不做推测性归因。
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = (os.environ.get("AMD_BASE_URL") or "https://developer.amd.com.cn/radeon/api/v1").rstrip("/")
KEY = os.environ.get("AMD_API_KEY", "").strip()
MODEL = (os.environ.get("AMD_MODEL") or "").strip()

TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "20"))


def _mask(key: str) -> str:
    if not key:
        return "<absent>"
    if len(key) <= 10:
        return f"<len={len(key)}>"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def _request(url: str, headers: dict[str, str], data: bytes | None = None, method: str = "GET"):
    """发一次请求，返回 (status, headers, body_text, elapsed_ms) 或抛异常。"""
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, dict(resp.headers), body, int((time.time() - started) * 1000)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, dict(exc.headers or {}), body, int((time.time() - started) * 1000)


def _show(tag: str, url: str, result) -> None:
    status, headers, body, ms = result
    snippet = body.replace("\n", " ")[:800]
    print(f"[{tag}] {url}")
    print(f"  status={status} elapsed={ms}ms")
    interesting = {k: v for k, v in headers.items() if k.lower() in {"content-type", "www-authenticate", "server", "x-request-id", "apim-request-id"}}
    print(f"  headers={json.dumps(interesting, ensure_ascii=False)}")
    print(f"  body={snippet}")
    print()


def main() -> int:
    print(f"base   = {BASE}")
    print(f"key    = {_mask(KEY)}")
    print(f"model  = {MODEL or '<absent>'}")
    print()

    # 1) 根路径（可能是网关首页或 404，仅用于确认可达）
    try:
        _show("root", BASE, _request(BASE, {}))
    except Exception as exc:  # noqa: BLE001 - 探测脚本要原样暴露错误
        print(f"[root] EXCEPTION {type(exc).__name__}: {exc}\n")

    # 2) /models 无鉴权
    try:
        _show("models-noauth", f"{BASE}/models", _request(f"{BASE}/models", {}))
    except Exception as exc:  # noqa: BLE001
        print(f"[models-noauth] EXCEPTION {type(exc).__name__}: {exc}\n")

    # 3) /models 两种鉴权方式对照（只有提供 key 时才做）
    if KEY:
        for tag, header, value in (
            ("models-bearer", "Authorization", f"Bearer {KEY}"),
            ("models-apim", "Ocp-Apim-Subscription-Key", KEY),
        ):
            try:
                _show(tag, f"{BASE}/models", _request(f"{BASE}/models", {header: value}))
            except Exception as exc:  # noqa: BLE001
                print(f"[{tag}] EXCEPTION {type(exc).__name__}: {exc}\n")
    else:
        print("[models-auth] 跳过：未设置 AMD_API_KEY\n")

    # 4) 模型清单摘要（Bearer 可用时）
    if KEY:
        try:
            status, _, body, _ = _request(f"{BASE}/models", {"Authorization": f"Bearer {KEY}"})
            if status == 200:
                items = json.loads(body).get("data", [])
                print(f"[models-summary] count={len(items)}")
                for item in items:
                    print(
                        f"  - id={item.get('id')} ctx={item.get('context_length')} "
                        f"json_output={item.get('json_output')} params={item.get('supported_parameters')}"
                    )
                print()
        except Exception as exc:  # noqa: BLE001
            print(f"[models-summary] EXCEPTION {type(exc).__name__}: {exc}\n")

    # 5) chat/completions 最小请求（验证模型名与鉴权组合）
    if KEY and MODEL:
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "只回复两个字：收到"}],
            "stream": False,
            "max_tokens": 512,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for tag, header, value in (
            ("chat-bearer", "Authorization", f"Bearer {KEY}"),
            ("chat-apim", "Ocp-Apim-Subscription-Key", KEY),
        ):
            try:
                _show(tag, f"{BASE}/chat/completions", _request(
                    f"{BASE}/chat/completions",
                    {header: value, "Content-Type": "application/json"},
                    data=data,
                    method="POST",
                ))
            except Exception as exc:  # noqa: BLE001
                print(f"[{tag}] EXCEPTION {type(exc).__name__}: {exc}\n")
    else:
        print("[chat] 跳过：需要同时设置 AMD_API_KEY 与 AMD_MODEL\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
