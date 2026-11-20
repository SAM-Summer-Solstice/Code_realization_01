"""统一启动入口：python main.py（无命令行参数）。

流程：读配置 → 读 input.json → 逐例生成（并发、失败隔离）→ 原子写 output.json → 写 run_log.json
退出码：0 全部处理完成（允许单条失败）；1 任务级运行错误；2 配置缺失或输入不符合基本协议。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src import pipeline  # noqa: E402
from src.config import ConfigError, load_settings  # noqa: E402
from src.input_source import InputError, load_cases  # noqa: E402
from src.io_utils import write_json_atomic  # noqa: E402
from src.logging_utils import build_run_log, now_iso  # noqa: E402


def main() -> int:
    started_at = now_iso()

    try:
        settings = load_settings(BASE_DIR)
    except ConfigError as exc:
        print(f"[FATAL] 配置错误：{exc}", file=sys.stderr)
        return 2

    print(f"[INFO] 通道={settings.profile} 模型={settings.model} 模式={settings.pipeline_mode} 并发={settings.max_workers}")
    for note in settings.notes:
        print(f"[INFO] {note}")

    try:
        cases = load_cases(settings.input_path)
    except InputError as exc:
        print(f"[FATAL] 输入错误：{exc}", file=sys.stderr)
        return 2

    print(f"[INFO] 待处理病例 {len(cases)} 例，输入={settings.input_path}")

    try:
        entries, logs, summary = pipeline.run(cases, settings)
        write_json_atomic(settings.output_path, entries)
        # 官方问答：结果文件名 output.json / results.json 尚未统一，兼容两种
        if settings.output_alias_path:
            write_json_atomic(settings.output_alias_path, entries)
        run_log = build_run_log(settings, started_at, now_iso(), logs, summary)
        write_json_atomic(settings.run_log_path, run_log)
    except Exception as exc:  # noqa: BLE001 —— 任务级失败
        print(f"[FATAL] 任务级运行错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    success = sum(1 for item in entries if item.get("status") == "success")
    failed = len(entries) - success
    warn_total = sum(len(log.get("warnings", [])) for log in logs)
    print(f"[INFO] 完成：成功 {success} 例，失败 {failed} 例，模型调用 {summary['total_calls']} 次，告警 {warn_total} 条")
    print(f"[INFO] 输出：{settings.output_path}")
    print(f"[INFO] 日志：{settings.run_log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
