"""HTTP 通道端到端验证：证明"注入环境变量即可切换真通道，主流程不改"。

做法：本地起一个 OpenAI ChatCompletions 兼容服务（要求 Bearer 鉴权），
用 LLM_PROFILE=dev-alt 让 main.py 走真实 HTTP 路径（发请求、带鉴权头、解析响应），
再校验输出结构与协议。

用法：python dev/scripts/verify_http_channel.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = 18090
EXPECTED_KEY = "local-test-key"
SOURCE_LINE = re.compile(r"^\[(S\d+)\]\s*(.+)$", re.M)
REQUIRED = {"case_features", "diagnostic_discussion", "plan", "evidence", "review_items"}


def build_record(prompt: str) -> dict:
    pairs = SOURCE_LINE.findall(prompt)
    texts = [text.strip() for _, text in pairs]
    if not texts:
        texts = ["资料未提供。"]
    evidence = [{"statement": t, "source_ids": [sid], "kind": "fact"} for sid, t in pairs[:3]]
    anchor = [sid for sid, _ in pairs[:1]] or ["S01"]
    evidence.append({"statement": "现有资料不足，需补充后进一步判断。", "source_ids": anchor, "kind": "inference"})
    return {
        "case_features": "；".join(texts[:3]) + "。",
        "diagnostic_discussion": "现有资料提示上述表现，具体原因尚待明确，需结合补充检查鉴别。",
        "plan": "拟完善相关检查，观察症状变化，并依据结果调整处理。",
        "evidence": evidence[:5],
        "review_items": [],
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if self.headers.get("Authorization") != f"Bearer {EXPECTED_KEY}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')
            return
        payload = json.loads(self.rfile.read(length) or b"{}")
        user_text = "\n".join(m.get("content", "") for m in payload.get("messages", []) if m.get("role") == "user")
        body = {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "model": payload.get("model", "unknown"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(build_record(user_text), ensure_ascii=False)},
                "finish_reason": "stop",
            }],
        }
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "ALT_", "CLINICAL_", "MAX_", "PIPELINE_", "MOCK_"))}
    env.update({
        "LLM_PROFILE": "dev-alt",
        "ALT_BASE_URL": f"http://127.0.0.1:{PORT}/v1",
        "ALT_API_KEY": EXPECTED_KEY,
        "LLM_MODEL": "local-openai-compatible",
        "CLINICAL_INPUT": str(ROOT / "dev" / "datasets" / "demo_cases.json"),
        "MAX_WORKERS": "2",
    })

    try:
        proc = subprocess.run(
            [sys.executable, "main.py"], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
    finally:
        server.shutdown()

    if proc.returncode != 0:
        print("[FAIL] main.py 退出码", proc.returncode)
        print(proc.stdout[-800:])
        print(proc.stderr[-800:])
        return 1

    data = json.loads((ROOT / "output.json").read_text(encoding="utf-8"))
    problems = []
    if len(data) != 10:
        problems.append(f"病例数 {len(data)} != 10")
    for entry in data:
        if entry.get("status") != "success":
            problems.append(f"{entry.get('case_id')} 状态 {entry.get('status')}")
            continue
        missing = REQUIRED - set(entry.get("medical_record") or {})
        if missing:
            problems.append(f"{entry['case_id']} 缺字段 {missing}")

    log = json.loads((ROOT / "run_log.json").read_text(encoding="utf-8"))
    if log["run"]["profile"] != "dev-alt":
        problems.append(f"run_log 通道记录错误：{log['run']['profile']}")
    if log["run"]["total_model_calls"] != 10:
        problems.append(f"调用计数异常：{log['run']['total_model_calls']}")
    if EXPECTED_KEY in json.dumps(log, ensure_ascii=False):
        problems.append("run_log 中出现明文密钥")

    if problems:
        print("[FAIL] " + "；".join(problems))
        return 1
    print("[PASS] dev-alt 通道走真实 HTTP：10/10 成功，鉴权头生效，run_log 不含密钥")
    return 0


if __name__ == "__main__":
    sys.exit(main())
