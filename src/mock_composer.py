"""mock 通道的确定性病历合成器。

用途：在没有可用模型时，验证协议、跑通全链路、支撑校赛演示。
原则：只使用病例原文中出现的表述与数值，不引入新事实；输出结构与真通道完全一致。
注意：mock 只保证"结构与可溯源性"，不代表临床质量，真实质量必须靠真通道验证。
若 dev/fixtures/<case_id>.json 存在，则直接返回该 fixture（用于对齐赛题示例）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "dev" / "fixtures"

MISSING_MARKERS = ("未提供", "未查", "未行", "未做", "未明确", "未记录", "未测", "不详", "缺如")
NEGATIVE_MARKERS = ("未见", "未触及", "未闻及", "否认", "无水肿", "无反跳痛")
LAB_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*(?:g/L|mg/dL|μmol/L|umol/L|mmol/L|mIU/L|ng/mL|U/L|%|℃|mmHg|次/分|10\^9/L|×10\^9/L|kg)")
CHIEF_PATTERN = re.compile(r"因[“\"]([^”\"]+)[”\"]")
SPLIT_PATTERN = re.compile(r"[；;。\n]")


def _clauses(text: str) -> list[str]:
    return [c.strip(" ，,") for c in SPLIT_PATTERN.split(text) if c.strip(" ，,")]


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


def _clean_item(clause: str) -> str:
    """把"既往史、过敏史、用药史未提供"整理成待核条目。"""
    for marker in MISSING_MARKERS:
        if marker not in clause:
            continue
        left, right = clause.split(marker, 1)
        if right.strip(" ，,；;。:："):
            return right.strip(" ，,；;。:：")
        # 标记在句末：取最后一个逗号片段（"查体…，墨菲征未查" -> "墨菲征"）
        segments = [s for s in re.split(r"[，,]", left) if s.strip()]
        return segments[-1].strip(" ：:，,；;。") if segments else ""
    return clause.strip(" ，,；;。:：")


def _ends_sentence(text: str) -> str:
    return text if text.endswith(("。", "；", "！", "？")) else text + "。"


def compose_record(case: dict) -> dict:
    """基于病例原文拼装五字段记录（确定性，无随机）。"""
    fixture = FIXTURE_DIR / f"{case['case_id']}.json"
    if fixture.exists():
        return json.loads(fixture.read_text(encoding="utf-8"))

    sources = case["sources"]
    chief = ""
    buckets: dict[str, list[tuple[str, str]]] = {"history": [], "exam": [], "lab": [], "negative": [], "missing": [], "other": []}

    for src in sources:
        for clause in _clauses(src["content"]):
            pair = (clause, src["source_id"])
            if not chief and CHIEF_PATTERN.search(clause):
                chief = clause
                continue
            if _has_any(clause, MISSING_MARKERS):
                buckets["missing"].append(pair)
            elif LAB_PATTERN.search(clause):
                buckets["lab"].append(pair)
            elif clause.startswith("查体") or "查体：" in clause:
                buckets["exam"].append(pair)
            elif _has_any(clause, NEGATIVE_MARKERS):
                buckets["negative"].append(pair)
            elif "既往" in clause or "长期" in clause or "史" in clause:
                buckets["history"].append(pair)
            else:
                buckets["other"].append(pair)

    # ---- 病例特点：主诉 -> 既往/病程 -> 查体 -> 检验 -> 有意义的阴性 ----
    parts: list[str] = []
    if chief:
        parts.append(_ends_sentence(chief))
    for key in ("history", "other", "exam", "lab", "negative"):
        for clause, _ in buckets[key][:3]:
            parts.append(_ends_sentence(clause))
    if not parts:
        parts.append(_ends_sentence(sources[0]["content"].strip()))
    if buckets["missing"]:
        items = [_clean_item(c) for c, _ in buckets["missing"][:3] if _clean_item(c)]
        if items:
            parts.append("现有资料中" + "、".join(items) + "未提供。")
    features = "".join(parts)

    # ---- 拟诊讨论：列出可核对的异常线索，明确不确定性与鉴别方向 ----
    abnormalities = [c for c, _ in buckets["lab"] if any(k in c for k in ("升高", "降低", "异常", "高尖", "阳性", "示"))]
    if not abnormalities:
        abnormalities = [c for c, _ in buckets["lab"][:2]]
    discussion = ["现有资料提示以下需要进一步明确的异常线索："]
    if abnormalities:
        discussion.append("①" + "；②".join(_ends_sentence(x).rstrip("。") for x in abnormalities[:2]) + "。")
    else:
        discussion.append("①现有资料以症状为主，缺乏有诊断价值的检验结果。")
    discussion.append("上述异常可由多种原因引起，需结合病史、体征与补充检查逐一鉴别，目前不宜据此作出确定性判断。")
    if buckets["negative"]:
        discussion.append("资料中" + _ends_sentence(buckets["negative"][0][0]).rstrip("。") + "，该项有助缩小鉴别范围。")
    if buckets["missing"]:
        items = [_clean_item(c) for c, _ in buckets["missing"][:3] if _clean_item(c)]
        if items:
            discussion.append("由于" + "、".join(items) + "尚未提供，病因待查，需进一步检查明确。")
    else:
        discussion.append("现有资料尚不足以确定病因，需进一步检查明确。")

    # ---- 诊疗计划：只写计划，逐项对应上文问题 ----
    plan: list[str] = []
    if abnormalities:
        plan.append("拟复查相关检验及检查，明确上述异常的原因与变化趋势。")
    plan.append("拟由临床医生补充" + ("、".join(_clean_item(c) for c, _ in buckets["missing"][:3] if _clean_item(c)) or "病史及既往资料") + "。")
    symptom = CHIEF_PATTERN.search(chief).group(1) if chief else "主要症状"
    plan.append("拟观察" + symptom + "及生命体征变化，并记录每日出入量。")
    plan.append("拟依据检查结果调整分析与处理，具体治疗方案待完整评估后确定，目前尚未执行。")

    # ---- 依据：事实 + 推断，source_id 全部来自本例 ----
    evidence: list[dict] = []
    if chief:
        evidence.append({"statement": chief + "。", "source_ids": [next(s["source_id"] for s in sources if chief in s["content"])], "kind": "fact"})
    for clause, sid in (buckets["lab"] + buckets["exam"] + buckets["negative"])[:2]:
        evidence.append({"statement": _ends_sentence(clause), "source_ids": [sid], "kind": "fact"})
    if len(evidence) < 2 and buckets["other"]:
        for clause, sid in buckets["other"][:2]:
            evidence.append({"statement": _ends_sentence(clause), "source_ids": [sid], "kind": "fact"})
    anchor = [sid for _, sid in (buckets["lab"] + buckets["missing"] + buckets["exam"])[:2]] or [sources[0]["source_id"]]
    evidence.append({"statement": "现有资料不足以确定病因，需补充资料后进一步判断。", "source_ids": anchor, "kind": "inference"})

    review_items: list[str] = []
    for clause, _ in buckets["missing"]:
        item = _clean_item(clause)
        if item and item not in review_items:
            review_items.append(item)

    return {
        "case_features": features,
        "diagnostic_discussion": "".join(discussion),
        "plan": "".join(plan),
        "evidence": evidence[:5],
        "review_items": review_items[:5],
    }
