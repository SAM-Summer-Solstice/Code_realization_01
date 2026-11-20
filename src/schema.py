"""输出结构与模型响应解析/规范化。

模型响应可能夹带 Markdown 围栏或解释文字，这里做三层解析：
1) 直接 json.loads；2) 去代码围栏；3) 括号配平截取首个 JSON 对象。
解析成功后按赛题协议做结构校验与清洗（source_ids 必须属于本例）。
"""
from __future__ import annotations

import json
import re

TEXT_FIELDS = ("case_features", "diagnostic_discussion", "plan")
REQUIRED_FIELDS = TEXT_FIELDS + ("evidence", "review_items")
VALID_KINDS = {"fact", "inference"}
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


class SchemaError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_model_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise SchemaError("RESPONSE_EMPTY", "模型返回为空")

    candidates = [text]
    fenced = FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    extracted = _extract_balanced(text)
    if extracted:
        candidates.append(extracted)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise SchemaError("RESPONSE_UNPARSEABLE", "模型返回无法解析为 JSON 对象")


def _extract_balanced(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def normalize_record(raw: dict, valid_source_ids: set[str]) -> tuple[dict, list[str]]:
    """按协议清洗并校验；不合法直接抛 SchemaError（该例记 failed）。"""
    warnings: list[str] = []
    if not isinstance(raw, dict):
        raise SchemaError("SCHEMA_INVALID", "medical_record 必须是对象")

    record: dict = {}
    for field in TEXT_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SchemaError("SCHEMA_INVALID", f"字段 {field} 缺失或为空")
        record[field] = value.strip()

    evidence_raw = raw.get("evidence")
    if evidence_raw is None:
        raise SchemaError("SCHEMA_INVALID", "字段 evidence 缺失")
    if not isinstance(evidence_raw, list):
        raise SchemaError("SCHEMA_INVALID", "evidence 必须是数组")

    evidence: list[dict] = []
    for index, item in enumerate(evidence_raw):
        if not isinstance(item, dict):
            warnings.append(f"evidence[{index}] 不是对象，已丢弃")
            continue
        statement = item.get("statement")
        kind = item.get("kind")
        ids = item.get("source_ids")
        if not isinstance(statement, str) or not statement.strip():
            warnings.append(f"evidence[{index}] 缺少 statement，已丢弃")
            continue
        if not isinstance(ids, list) or not ids:
            warnings.append(f"evidence[{index}] 缺少 source_ids，已丢弃")
            continue
        kept_ids = [str(i) for i in ids if str(i) in valid_source_ids]
        if not kept_ids:
            warnings.append(f"evidence[{index}] 的 source_ids 不属于本例，已丢弃")
            continue
        if len(kept_ids) != len(ids):
            warnings.append(f"evidence[{index}] 含非本例 source_id，已剔除")
        if kind not in VALID_KINDS:
            warnings.append(f"evidence[{index}] kind={kind!r} 非法，按 fact 处理")
            kind = "fact"
        evidence.append({"statement": statement.strip(), "source_ids": kept_ids, "kind": kind})

    if not evidence:
        raise SchemaError("SCHEMA_INVALID", "evidence 清洗后为空，无法溯源")
    if len(evidence) > 5:
        warnings.append(f"evidence 超过 5 条（{len(evidence)}），已截断")
        evidence = evidence[:5]
    if len(evidence) < 3:
        warnings.append(f"evidence 少于 3 条（{len(evidence)}），按实际保留")

    review_raw = raw.get("review_items")
    if review_raw is None:
        raise SchemaError("SCHEMA_INVALID", "字段 review_items 缺失（无内容时应为 []）")
    if not isinstance(review_raw, list):
        raise SchemaError("SCHEMA_INVALID", "review_items 必须是数组")
    review_items = [str(x).strip() for x in review_raw if str(x).strip()]
    if len(review_items) != len(review_raw):
        warnings.append("review_items 中存在空字符串，已剔除")

    record["evidence"] = evidence
    record["review_items"] = review_items
    return record, warnings
