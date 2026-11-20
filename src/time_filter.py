"""时序过滤：按资料截止点剔除"截止点之后才发生"的表述。

只做保守规则（仅识别明确带"入院后第 N 天/小时"的表述），对全部病例一致执行，
不做逐例人工干预。关闭方式：TIME_FILTER=0。
"""
from __future__ import annotations

import os
import re

_CN_NUM = "0-9一二三四五六七八九十两"

POST_CUTOFF_PATTERNS = (
    re.compile(rf"入院(?:后)?第?\s*[{_CN_NUM}]+\s*(?:天|日|小时|h)"),
    re.compile(rf"第\s*[{_CN_NUM}]+\s*(?:天|日)(?:后|的)?\s*(?:复查|复测|检查|结果|CT|MRI|超声|化验|出院)"),
    re.compile(r"出院(?:时|前|后)"),
)

# 这些截止点表示"入院即刻/资料到手时"，可用于判断是否存在越界表述
_T0_LIKE = {"", "t0", "t0（入院时）", "入院时", "入院", "admission", "baseline"}


def enabled() -> bool:
    return os.environ.get("TIME_FILTER", "1").strip() not in {"0", "off", "false"}


def is_t0_like(cutoff: str) -> bool:
    return cutoff.strip().lower() in _T0_LIKE


def split_by_cutoff(case: dict) -> tuple[dict, list[dict]]:
    """返回 (过滤后的病例, 被剔除的片段说明)。非 T0 类截止点不做过滤。"""
    if not enabled() or not is_t0_like(case.get("record_cutoff", "")):
        return case, []

    kept: list[dict] = []
    dropped: list[dict] = []
    for src in case["sources"]:
        if any(pattern.search(src["content"]) for pattern in POST_CUTOFF_PATTERNS):
            dropped.append({"source_id": src["source_id"], "reason": "超出自资料截止点"})
            continue
        kept.append(src)

    if not kept:  # 全部被剔除时保守回退，避免产出空病例
        return case, []

    filtered = dict(case)
    filtered["sources"] = kept
    return filtered, dropped
