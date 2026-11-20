"""协议自测：把赛题硬性要求逐条变成可执行断言。

用法（项目根目录）：
    python dev/scripts/selftest_protocol.py

每条用例都在临时目录里跑一份项目副本，互不污染；不访问网络（mock 通道）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORE = shutil.ignore_patterns("__pycache__", ".env", "output.json", "run_log.json", "reports", "*.pyc")

DEMO = [
    {
        "case_id": "T001",
        "record_cutoff": "T0",
        "sources": [
            {"source_id": "S01", "content": "患者男，50岁，因“咳嗽3天”入院。"},
            {"source_id": "S02", "content": "查体：体温37.8℃，咽部充血。血常规未提供。"},
        ],
    },
    {
        "case_id": "T002",
        "record_cutoff": "T0",
        "sources": [{"source_id": "S01", "content": "患者女，28岁，因“头痛1周”入院。"}],
    },
]


def run_case(name: str, files: dict[str, str], env_extra: dict[str, str]) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "proj"
        shutil.copytree(ROOT, work, ignore=IGNORE)
        for rel, content in files.items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "AMD_", "ALT_", "MAX_", "PIPELINE_", "MOCK_", "CLINICAL_", "TIME_FILTER"))}
        env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, "main.py"], cwd=work, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        expected = int(env_extra.pop("_EXPECT_EXIT", "0"))
        ok = proc.returncode == expected
        detail = f"退出码={proc.returncode}（期望 {expected}）"
        if not ok:
            detail += f"；stderr={proc.stderr.strip()[:200]}"
        return ok, detail + _verify(work, env_extra)


def _verify(work: Path, env_extra: dict[str, str]) -> str:
    """按用例附带的后置断言检查产物。"""
    check = env_extra.get("_VERIFY", "")
    output = work / "output.json"
    if check == "no_output":
        return "" if not output.exists() else "；但发现了不应存在的 output.json"
    if check == "empty_array":
        if not output.exists():
            return "；未生成 output.json"
        return "" if json.loads(output.read_text(encoding="utf-8")) == [] else "；output.json 不是 []"
    if check == "one_failed":
        if not output.exists():
            return "；未生成 output.json"
        data = json.loads(output.read_text(encoding="utf-8"))
        failed = [d for d in data if d.get("status") == "failed"]
        if len(failed) != 1:
            return f"；失败条数={len(failed)}（期望 1）"
        if [d["case_id"] for d in data] != ["T001", "T002"]:
            return "；顺序与输入不一致"
        return "" if failed[0].get("medical_record") is None else "；failed 记录不应带 medical_record"
    if check == "schema_ok":
        data = json.loads(output.read_text(encoding="utf-8")) if output.exists() else []
        if not data:
            return "；output.json 为空"
        for entry in data:
            if entry.get("status") != "success":
                return f"；{entry.get('case_id')} 状态为 {entry.get('status')}"
            record = entry.get("medical_record") or {}
            missing = {"case_features", "diagnostic_discussion", "plan", "evidence", "review_items"} - set(record)
            if missing:
                return f"；{entry.get('case_id')} 缺字段 {missing}"
        return ""
    return ""


def main() -> int:
    demo = json.dumps(DEMO, ensure_ascii=False)
    cases = [
        ("空输入写出 []", {"input.json": "[]"}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "input.json", "_EXPECT_EXIT": "0", "_VERIFY": "empty_array"}),
        ("非法 JSON 退出码 2", {"input.json": "{not json"}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "input.json", "_EXPECT_EXIT": "2", "_VERIFY": "no_output"}),
        ("顶层不是数组退出码 2", {"input.json": '{"case_id":"X"}'}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "input.json", "_EXPECT_EXIT": "2", "_VERIFY": "no_output"}),
        ("缺 case_id 退出码 2", {"input.json": json.dumps([{"sources": [{"source_id": "S01", "content": "内容"}]}], ensure_ascii=False)}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "input.json", "_EXPECT_EXIT": "2", "_VERIFY": "no_output"}),
        ("case_id 重复退出码 2", {"input.json": json.dumps([DEMO[0], DEMO[0]], ensure_ascii=False)}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "input.json", "_EXPECT_EXIT": "2", "_VERIFY": "no_output"}),
        ("official 缺 .env 退出码 2", {"input.json": demo}, {"LLM_PROFILE": "official", "CLINICAL_INPUT": "input.json", "_EXPECT_EXIT": "2", "_VERIFY": "no_output"}),
        ("单例失败隔离 + 顺序保持", {"input.json": demo}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "input.json", "MOCK_FORCE_FAIL": "T001", "_EXPECT_EXIT": "0", "_VERIFY": "one_failed"}),
        ("JSONL + 字段别名兼容", {"cases.jsonl": "\n".join([
            json.dumps({"id": "A1", "materials": [{"id": "S01", "text": "患者男，40岁，因“腹痛2天”入院。"}]}, ensure_ascii=False),
        ])}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "cases.jsonl", "_EXPECT_EXIT": "0", "_VERIFY": "schema_ok"}),
        ("pipeline 预算不足自动降级", {"input.json": demo}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "input.json", "PIPELINE_MODE": "pipeline", "MAX_CALLS_PER_CASE": "1", "_EXPECT_EXIT": "0", "_VERIFY": "schema_ok"}),
        ("空数组 + pipeline 模式", {"input.json": "[]"}, {"LLM_PROFILE": "mock", "CLINICAL_INPUT": "input.json", "PIPELINE_MODE": "pipeline", "_EXPECT_EXIT": "0", "_VERIFY": "empty_array"}),
    ]

    passed = 0
    for name, files, env in cases:
        expected = env.get("_EXPECT_EXIT", "0")
        verify = env.get("_VERIFY", "")
        clean_env = {k: v for k, v in env.items() if not k.startswith("_")}
        clean_env["_EXPECT_EXIT"] = expected
        clean_env["_VERIFY"] = verify
        ok, detail = run_case(name, files, clean_env)
        print(("PASS  " if ok else "FAIL  ") + name + " -> " + detail)
        passed += int(ok)

    print(f"\n协议自测：{passed}/{len(cases)} 通过")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
