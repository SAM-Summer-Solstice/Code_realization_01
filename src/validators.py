"""生成后校验：数值回溯、否定/缺失保护、计划-已实施混写检查。

只产出告警（不擅自改正文），告警写入 run_log 并进入检查器报告，
用于校赛演示"改进可用数据说明收益"。
"""
from __future__ import annotations

import re

NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])")
# 列表序号（"1."、"2、"、"（3）"）不是临床数值，回溯前先剔除，避免误报
ORDINAL = re.compile(r"(?P<lead>^|[。；;：:\n])\s*(?:[（(]\d{1,2}[)）]|\d{1,2}(?:\.(?!\d)|[、)）]))\s*")
MISSING_WORDS = ("未提供", "未查", "未行", "未做", "不详", "未记录", "未测")
FABRICATED_NORMAL = ("均正常", "各项正常", "未见异常", "一切正常", "阴性")
DONE_WORDS = ("已予", "已行", "已给", "已完成", "已复查", "术后给予")


def extract_numbers(text: str) -> set[str]:
    cleaned = ORDINAL.sub(lambda m: m.group("lead"), text or "")
    return set(NUMBER.findall(cleaned))


def check_record(record: dict, case: dict, dropped: list[dict] | None = None) -> dict:
    sources_text = "；".join(src["content"] for src in case["sources"])
    body = "".join(record.get(f, "") for f in ("case_features", "diagnostic_discussion", "plan"))
    warnings: list[str] = []

    # 1) 数值回溯：事实段与计划段分开报告（事实段凭空多出数值风险最高）
    source_numbers = extract_numbers(sources_text)
    fact_unknown = sorted(extract_numbers(record.get("case_features", "") + record.get("diagnostic_discussion", "")) - source_numbers)
    plan_unknown = sorted(extract_numbers(record.get("plan", "")) - source_numbers)
    unknown = sorted(set(fact_unknown) | set(plan_unknown))
    if fact_unknown:
        warnings.append(f"数值回溯（事实段）：正文出现资料中没有的数值 {fact_unknown}")
    if plan_unknown:
        warnings.append(f"数值回溯（计划段）：计划出现资料中没有的数值 {plan_unknown}")

    # 2) 缺失/否定保护：资料里有"未提供"，正文不能一个字不提
    if any(word in sources_text for word in MISSING_WORDS) and not any(word in body for word in MISSING_WORDS):
        warnings.append("缺失保护：资料含缺失表述，但正文未体现'未提供/不详'")

    # 3) 禁止凭空补写"正常/阴性"
    for phrase in FABRICATED_NORMAL:
        if phrase in body and phrase not in sources_text:
            warnings.append(f"补写风险：正文出现资料中没有的'{phrase}'")

    # 4) 计划不得写成已实施
    for phrase in DONE_WORDS:
        if phrase in body and phrase not in sources_text:
            warnings.append(f"计划/已实施混写：正文出现'{phrase}'")

    # 5) 时序：引用被剔除的片段
    for item in dropped or []:
        if item["source_id"] in body:
            warnings.append(f"时序：正文引用了截止点之后的片段 {item['source_id']}")

    char_count = len(body)
    if not (800 <= char_count <= 1600):
        warnings.append(f"字数：三段合计 {char_count} 字，不在建议区间 800–1600")

    return {
        "warnings": warnings,
        "char_count": char_count,
        "evidence_count": len(record.get("evidence", [])),
        "fact_count": sum(1 for e in record.get("evidence", []) if e.get("kind") == "fact"),
        "inference_count": sum(1 for e in record.get("evidence", []) if e.get("kind") == "inference"),
        "unknown_numbers": unknown,
    }
