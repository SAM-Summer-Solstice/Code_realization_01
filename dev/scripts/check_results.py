"""结果检查器：把赛题协议与忠实性要求变成可自动判定的规则。

用法（在项目根目录）：
    python dev/scripts/check_results.py [input.json] [output.json]

默认读取 CLINICAL_INPUT 或 input.json，以及 output.json。
输出：控制台摘要 + dev/reports/check_report.md / check_report.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.input_source import load_cases  # noqa: E402
from src.schema import TEXT_FIELDS, VALID_KINDS  # noqa: E402
from src.validators import check_record  # noqa: E402


def _resolve_input(argv: list[str]) -> Path:
    if argv:
        return Path(argv[0])
    return Path(os.environ.get("CLINICAL_INPUT") or (ROOT / "input.json"))


def check_case(case: dict, entry: dict) -> dict:
    issues: list[str] = []
    stats: dict = {}

    if entry.get("case_id") != case["case_id"]:
        issues.append(f"case_id 不一致：期望 {case['case_id']}，实际 {entry.get('case_id')}")
    status = entry.get("status")
    if status not in {"success", "failed"}:
        issues.append(f"status 非法：{status!r}")
    if status == "failed":
        error = entry.get("error") or {}
        if not error.get("code") or not error.get("message"):
            issues.append("failed 记录缺少 error.code / error.message")
        if entry.get("medical_record") is not None:
            issues.append("failed 记录的 medical_record 必须为 null")
        return {"case_id": case["case_id"], "issues": issues, "stats": stats}

    record = entry.get("medical_record")
    if not isinstance(record, dict):
        issues.append("success 记录缺少 medical_record 对象")
        return {"case_id": case["case_id"], "issues": issues, "stats": stats}

    for field in TEXT_FIELDS:
        if not isinstance(record.get(field), str) or not record[field].strip():
            issues.append(f"字段 {field} 缺失或为空")

    valid_ids = {src["source_id"] for src in case["sources"]}
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        issues.append("evidence 必须是非空数组")
    else:
        if len(evidence) > 5:
            issues.append(f"evidence 条数 {len(evidence)} 超过 5")
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                issues.append(f"evidence[{index}] 不是对象")
                continue
            if not str(item.get("statement", "")).strip():
                issues.append(f"evidence[{index}] 缺少 statement")
            if item.get("kind") not in VALID_KINDS:
                issues.append(f"evidence[{index}] kind 非法：{item.get('kind')!r}")
            ids = item.get("source_ids")
            if not isinstance(ids, list) or not ids:
                issues.append(f"evidence[{index}] source_ids 非法")
            else:
                unknown = [i for i in ids if str(i) not in valid_ids]
                if unknown:
                    issues.append(f"evidence[{index}] 含非本例 source_id：{unknown}")

    if not isinstance(record.get("review_items"), list):
        issues.append("review_items 必须是数组（无内容时为 []）")

    result = check_record(record, case)
    stats = {k: v for k, v in result.items() if k != "warnings"}
    # 协议问题与内容忠实性问题分开统计：前者决定"能否被读取"，后者决定"质量分"
    fidelity = [w for w in result["warnings"] if w.startswith(("数值回溯", "补写风险", "计划/已实施", "时序"))]
    return {"case_id": case["case_id"], "issues": issues, "fidelity_warnings": fidelity, "stats": stats}


def main() -> int:
    argv = sys.argv[1:]
    input_path = _resolve_input(argv)
    output_path = Path(argv[1]) if len(argv) > 1 else ROOT / "output.json"
    log_path = ROOT / "run_log.json"

    cases = load_cases(input_path)
    entries = json.loads(output_path.read_text(encoding="utf-8"))
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}

    issues_top: list[str] = []
    if len(entries) != len(cases):
        issues_top.append(f"条数不一致：输入 {len(cases)} 条，输出 {len(entries)} 条")
    for index, case in enumerate(cases[:len(entries)]):
        if entries[index].get("case_id") != case["case_id"]:
            issues_top.append(f"第 {index + 1} 条顺序错位：期望 {case['case_id']}，实际 {entries[index].get('case_id')}")

    per_case = [check_case(case, entries[i]) for i, case in enumerate(cases[:len(entries)])]
    hard_issue_cases = [item for item in per_case if item["issues"]]
    fidelity_cases = [item for item in per_case if item.get("fidelity_warnings")]
    success = sum(1 for e in entries if e.get("status") == "success")
    protocol_pass = len(cases) - len(hard_issue_cases)
    fidelity_ok = len(cases) - len(fidelity_cases)

    report = {
        "input": str(input_path),
        "output": str(output_path),
        "total": len(cases),
        "success": success,
        "failed": len(entries) - success,
        "protocol_pass": protocol_pass,
        "protocol_pass_rate": round(protocol_pass / len(cases) * 100, 1) if cases else 100.0,
        "fidelity_ok": fidelity_ok,
        "fidelity_ok_rate": round(fidelity_ok / len(cases) * 100, 1) if cases else 100.0,
        "top_issues": issues_top,
        "cases": per_case,
        "run_summary": log.get("run", {}),
    }

    reports_dir = ROOT / "dev" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "check_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 结果检查报告",
        "",
        f"- 输入：`{input_path}`",
        f"- 输出：`{output_path}`",
        f"- 病例总数：{report['total']}，成功 {report['success']}，失败 {report['failed']}",
        f"- **协议通过率：{report['protocol_pass_rate']}%**（{report['protocol_pass']}/{report['total']}）",
        f"- **忠实性无告警：{report['fidelity_ok_rate']}%**（{report['fidelity_ok']}/{report['total']}）",
        "",
    ]
    if issues_top:
        lines += ["## 结构性问题", ""] + [f"- {x}" for x in issues_top] + [""]
    lines += ["## 逐例结果", "", "| 病例 | 协议 | 忠实性 | 字数 | evidence | 问题 |", "|---|---|---|---|---|---|"]
    for item in per_case:
        stats = item["stats"]
        verdict = "通过" if not item["issues"] else "不通过"
        fidelity = "无告警" if not item.get("fidelity_warnings") else "；".join(item["fidelity_warnings"])
        detail = "；".join(item["issues"]) if item["issues"] else "—"
        lines.append(
            f"| {item['case_id']} | {verdict} | {fidelity} | {stats.get('char_count', '—')} "
            f"| {stats.get('evidence_count', '—')} | {detail} |"
        )
    (reports_dir / "check_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"协议通过率：{report['protocol_pass_rate']}%（{protocol_pass}/{len(cases)}）")
    print(f"忠实性无告警：{report['fidelity_ok_rate']}%（{fidelity_ok}/{len(cases)}）")
    print(f"成功 {success} 例，失败 {report['failed']} 例")
    if issues_top:
        print("结构性问题：" + "；".join(issues_top))
    for item in hard_issue_cases:
        print(f"  [{item['case_id']}] " + "；".join(item["issues"]))
    for item in fidelity_cases:
        print(f"  [{item['case_id']}] " + "；".join(item["fidelity_warnings"]))
    print(f"报告：{reports_dir / 'check_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
