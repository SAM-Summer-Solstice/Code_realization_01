"""主流程编排：逐例隔离、并发受控、失败不扩散。

可替换点：
- 模型通道：build_client(settings) 决定，主流程只依赖 chat() 接口；
- 输入材料：load_cases() 决定，主流程只依赖规范化的 case 结构。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import prompts
from .config import Settings
from .input_source import CaseError
from .logging_utils import now_iso
from .model_client import ModelError, build_client
from .schema import SchemaError, normalize_record, parse_model_json
from .time_filter import split_by_cutoff
from .validators import check_record


def _generate_record(case: dict, settings: Settings, mode: str, client) -> dict:
    prompt_dir = settings.prompt_dir

    if mode == "single":
        raw = client.chat(prompts.build_messages(case, "single", prompt_dir), case=case, call_index=0)
        return parse_model_json(raw)

    facts_raw = client.chat(prompts.build_messages(case, "pipeline", prompt_dir, stage="extract"), case=case, call_index=0)
    facts = facts_raw if isinstance(facts_raw, str) else ""
    gen_raw = client.chat(
        prompts.build_messages(case, "pipeline", prompt_dir, stage="generate", facts=facts),
        case=case, call_index=1,
    )
    if settings.max_calls_per_case >= 3:
        verify_raw = client.chat(
            prompts.build_messages(case, "pipeline", prompt_dir, stage="verify", draft=gen_raw),
            case=case, call_index=2,
        )
        if verify_raw and verify_raw.strip():
            return parse_model_json(verify_raw)
    return parse_model_json(gen_raw)


def process_case(case: dict, settings: Settings, prompt_version: str) -> tuple[dict, dict]:
    started_at = now_iso()
    started = time.perf_counter()
    warnings: list[str] = []
    dropped: list[dict] = []
    stats: dict = {}
    mode = settings.pipeline_mode
    client = build_client(settings)

    try:
        if case.get("_invalid"):
            raise CaseError(str(case["_invalid"]))
        filtered, dropped = split_by_cutoff(case)
        if mode == "pipeline" and settings.max_calls_per_case < 3:
            warnings.append(f"调用预算 {settings.max_calls_per_case} 不足，已降级为 single 模式")
            mode = "single"

        valid_ids = {src["source_id"] for src in filtered["sources"]}
        if not valid_ids:
            raise CaseError("病例在截止点过滤后没有可用资料")

        raw_record = _generate_record(filtered, settings, mode, client)
        record, schema_warnings = normalize_record(raw_record, valid_ids)
        warnings.extend(schema_warnings)
        stats = check_record(record, filtered, dropped)
        warnings.extend(stats["warnings"])

        entry = {"case_id": case["case_id"], "status": "success", "medical_record": record, "error": None}
        log = {
            "case_id": case["case_id"],
            "status": "success",
            "started_at": started_at,
            "ended_at": now_iso(),
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "calls": client.calls,
            "retries": client.retries,
            "prompt_version": prompts.prompt_version(mode),
            "dropped_sources": dropped,
            "char_count": stats.get("char_count", 0),
            "evidence_count": stats.get("evidence_count", 0),
            "fact_count": stats.get("fact_count", 0),
            "inference_count": stats.get("inference_count", 0),
            "unknown_numbers": stats.get("unknown_numbers", []),
            "warnings": warnings,
        }
        return entry, log

    except (ModelError, SchemaError, CaseError) as exc:
        code = getattr(exc, "code", "CASE_INVALID")
        message = getattr(exc, "message", str(exc))
        return _failed(case, code, message, started_at, started, warnings, dropped, settings, client)
    except Exception as exc:  # noqa: BLE001 —— 单例异常必须隔离
        return _failed(case, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", started_at, started, warnings, dropped, settings, client)


def _failed(case, code, message, started_at, started, warnings, dropped, settings, client=None) -> tuple[dict, dict]:
    entry = {
        "case_id": case["case_id"],
        "status": "failed",
        "medical_record": None,
        "error": {"code": code, "message": message},
    }
    log = {
        "case_id": case["case_id"],
        "status": "failed",
        "started_at": started_at,
        "ended_at": now_iso(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "calls": getattr(client, "calls", 0),
        "retries": getattr(client, "retries", 0),
        "prompt_version": prompts.prompt_version(settings.pipeline_mode),
        "dropped_sources": dropped,
        "warnings": warnings + [f"{code}: {message}"],
    }
    return entry, log


def run(cases: list[dict], settings: Settings) -> tuple[list[dict], list[dict], dict]:
    """并发处理全部病例，返回（按输入顺序的结果、run_log 明细、汇总）。"""
    prompt_version = prompts.prompt_version(settings.pipeline_mode)
    entries: list[dict | None] = [None] * len(cases)
    logs: list[dict | None] = [None] * len(cases)

    if cases:
        with ThreadPoolExecutor(max_workers=min(settings.max_workers, len(cases))) as pool:
            futures = {pool.submit(process_case, case, settings, prompt_version): index
                       for index, case in enumerate(cases)}
            for future in as_completed(futures):
                index = futures[future]
                entries[index], logs[index] = future.result()

    summary = {
        "prompt_version": prompt_version,
        "total_calls": sum(int(log.get("calls", 0)) for log in logs if log),
        "total_retries": sum(int(log.get("retries", 0)) for log in logs if log),
        "time_filter": "on" if any(bool(log.get("dropped_sources")) for log in logs if log) else "on(无命中)",
    }
    return entries, logs, summary
