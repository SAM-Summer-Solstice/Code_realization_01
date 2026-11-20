"""Prompt 组装。Prompt 文件带版本号，版本会写入 run_log，保证"代码/提示词/结果"可对应。"""
from __future__ import annotations

from pathlib import Path

PROMPT_FILES = {
    "single": "single_v2.txt",
    "extract": "extract_v1.txt",
    "generate": "generate_v1.txt",
    "verify": "verify_v1.txt",
}


def prompt_version(mode: str) -> str:
    names = {"single": ["single"], "pipeline": ["extract", "generate", "verify"]}[mode]
    return "+".join(PROMPT_FILES[n].removesuffix(".txt") for n in names)


def _read(prompt_dir: Path, key: str) -> str:
    path = prompt_dir / PROMPT_FILES[key]
    if not path.exists():
        raise FileNotFoundError(f"缺少 Prompt 文件: {path}")
    return path.read_text(encoding="utf-8").strip()


def render_case(case: dict, sources: list[dict] | None = None) -> str:
    lines = [
        f"病例编号：{case['case_id']}",
        f"资料截止点：{case.get('record_cutoff') or '未标注'}",
        "以下资料在截止点及之前可获得：",
    ]
    for src in sources if sources is not None else case["sources"]:
        lines.append(f"[{src['source_id']}] {src['content']}")
    return "\n".join(lines)


def build_messages(
    case: dict,
    mode: str,
    prompt_dir: Path,
    stage: str = "single",
    facts: str = "",
    draft: str = "",
) -> list[dict]:
    if mode == "single":
        system = _read(prompt_dir, "single")
        return [{"role": "system", "content": system}, {"role": "user", "content": render_case(case)}]

    if stage == "extract":
        system = _read(prompt_dir, "extract")
        return [{"role": "system", "content": system}, {"role": "user", "content": render_case(case)}]

    if stage == "generate":
        system = _read(prompt_dir, "generate")
        user = f"{render_case(case)}\n\n【抽取结果】\n{facts.strip() or '（无）'}"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    if stage == "verify":
        system = _read(prompt_dir, "verify")
        user = f"{render_case(case)}\n\n【待校验草稿】\n{draft.strip()}"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    raise ValueError(f"未知 stage: {stage}")
