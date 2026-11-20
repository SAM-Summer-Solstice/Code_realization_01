"""run_log 组装：字段齐全、可对应到病例、绝不含密钥。"""
from __future__ import annotations

from datetime import datetime

from .config import Settings


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_run_log(
    settings: Settings,
    started_at: str,
    ended_at: str,
    cases: list[dict],
    pipeline_summary: dict,
) -> dict:
    config = settings.public_config()
    return {
        "schema_version": "1.0",
        "run": {
            "started_at": started_at,
            "ended_at": ended_at,
            "total_cases": len(cases),
            "success": sum(1 for c in cases if c["status"] == "success"),
            "failed": sum(1 for c in cases if c["status"] == "failed"),
            "model": config["model"],
            "profile": config["profile"],
            "pipeline_mode": config["pipeline_mode"],
            "prompt_version": pipeline_summary.get("prompt_version", ""),
            "generation_params": {
                "temperature": config["temperature"],
                "top_p": config["top_p"],
                "seed": config["seed"],
                "stream": False,
            },
            "limits": {
                "max_workers": config["max_workers"],
                "timeout": config["timeout"],
                "max_retries": config["max_retries"],
                "max_calls_per_case": config["max_calls_per_case"],
            },
            "auth": config["auth"],
            "total_model_calls": pipeline_summary.get("total_calls", 0),
            "total_retries": pipeline_summary.get("total_retries", 0),
            "time_filter": pipeline_summary.get("time_filter", ""),
            "notes": settings.notes,
        },
        "cases": cases,
    }
