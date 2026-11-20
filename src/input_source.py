"""输入源适配层：把"材料从哪来、字段叫什么"与主流程解耦。

默认读取 main.py 同目录的 input.json（赛题规定）。开发期可用 CLINICAL_INPUT
指向别的文件或目录；字段名不同（如 id/caseId、materials/records）时由
normalize_case 统一成规范结构，主流程无需改动。
"""
from __future__ import annotations

import json
from pathlib import Path

ALIAS_CASE_ID = ("case_id", "caseId", "id", "case_no", "病例编号")
ALIAS_CUTOFF = ("record_cutoff", "cutoff", "cutoff_point", "t0", "资料截止点")
ALIAS_SOURCES = ("sources", "materials", "records", "fragments", "资料")
ALIAS_SOURCE_ID = ("source_id", "sourceId", "sid", "id", "编号")
ALIAS_CONTENT = ("content", "text", "body", "正文", "内容")


class InputError(RuntimeError):
    """任务级输入错误，对应退出码 2。"""


class CaseError(RuntimeError):
    """单条病例的资料无效，对应该条 failed，不影响其它病例。"""


class CaseIdError(InputError):
    """病例标识缺失/重复，属于基本协议错误，对应退出码 2。"""


def _pick(obj: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return None


def normalize_case(raw: object, index: int) -> dict:
    """把任意来源的单条病例映射成规范结构 {case_id, record_cutoff, sources}。"""
    if not isinstance(raw, dict):
        raise CaseIdError(f"第 {index} 条不是 JSON 对象")

    case_id = _pick(raw, ALIAS_CASE_ID)
    if case_id is None or not str(case_id).strip():
        raise CaseIdError(f"第 {index} 条缺少 case_id")
    case_id = str(case_id).strip()

    cutoff_raw = _pick(raw, ALIAS_CUTOFF)
    cutoff = str(cutoff_raw).strip() if cutoff_raw is not None else ""

    raw_sources = _pick(raw, ALIAS_SOURCES)
    if raw_sources is None:
        raise CaseError(f"病例 {case_id} 缺少 sources")
    if isinstance(raw_sources, str):
        raw_sources = [{"source_id": "S01", "content": raw_sources}]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise CaseError(f"病例 {case_id} 的 sources 必须是非空数组")

    sources: list[dict] = []
    seen: set[str] = set()
    for j, item in enumerate(raw_sources):
        if isinstance(item, str):
            item = {"source_id": f"S{j + 1:02d}", "content": item}
        if not isinstance(item, dict):
            raise CaseError(f"病例 {case_id} 的 sources[{j}] 不是对象")
        sid = _pick(item, ALIAS_SOURCE_ID)
        content = _pick(item, ALIAS_CONTENT)
        if sid is None or not str(sid).strip():
            raise CaseError(f"病例 {case_id} 的 sources[{j}] 缺少 source_id")
        if content is None or not str(content).strip():
            raise CaseError(f"病例 {case_id} 的 sources[{j}] 缺少 content")
        sid = str(sid).strip()
        if sid in seen:
            raise CaseError(f"病例 {case_id} 的 source_id 重复: {sid}")
        seen.add(sid)
        sources.append({"source_id": sid, "content": str(content).strip()})

    return {"case_id": case_id, "record_cutoff": cutoff, "sources": sources}


def _load_json_array(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise InputError(f"无法读取输入文件 {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"输入文件不是合法 JSON: {exc}") from exc
    if not isinstance(data, list):
        raise InputError("输入文件顶层必须是 JSON 数组")
    return data


def _load_jsonl(path: Path) -> list:
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise InputError(f"第 {lineno} 行不是合法 JSON: {exc}") from exc
    return rows


def load_cases(path: Path) -> list[dict]:
    """读取并规范化全部病例。任何任务级问题都抛 InputError（退出码 2）。"""
    if not path.exists():
        raise InputError(f"输入文件不存在: {path}")

    if path.is_dir():
        raw: list = []
        for item in sorted(path.glob("*.json")):
            raw.extend(_load_json_array(item))
    elif path.suffix.lower() in {".jsonl", ".ndjson"}:
        raw = _load_jsonl(path)
    else:
        raw = _load_json_array(path)

    cases: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        try:
            case = normalize_case(item, index)
        except CaseIdError:
            raise
        except CaseError as exc:
            # 资料结构有问题：保留 case_id 占位，让该条记为 failed，不拖垮整批
            fallback_id = str(_pick(item, ALIAS_CASE_ID) or f"INVALID_{index}").strip()
            case = {
                "case_id": fallback_id,
                "record_cutoff": "",
                "sources": [],
                "_invalid": str(exc),
            }
        if case["case_id"] in seen:
            raise InputError(f"case_id 重复: {case['case_id']}")
        seen.add(case["case_id"])
        cases.append(case)
    return cases
