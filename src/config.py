"""配置层：四通道 + 运行参数。

所有可变项都来自环境变量或 main.py 同目录的 .env，源码中不出现任何真实地址与密钥。
通道切换只改 LLM_PROFILE，主流程代码零改动。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_OFFICIAL = "official"
PROFILE_DEV_AMD = "dev-amd"
PROFILE_DEV_ALT = "dev-alt"
PROFILE_MOCK = "mock"

# 开发替身通道的网关地址只从环境变量读取：提交包内不得出现任何第三方端点
DEFAULT_AMD_MODEL = "Qwen3.8-27B"

# 开发替身通道的鉴权方式，以实测为准；可用 AMD_AUTH_MODE 切换，无需改代码。
AMD_AUTH_MODES = {
    "bearer": ("Authorization", "Bearer "),
    "apim": ("Ocp-Apim-Subscription-Key", ""),
}


class ConfigError(RuntimeError):
    """配置缺失或非法，对应退出码 2。"""


def read_dotenv(path: Path) -> dict[str, str]:
    """极简 .env 解析（只支持 KEY=VALUE 与 # 注释），避免为读两行配置引入依赖。"""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


@dataclass
class Settings:
    base_dir: Path
    profile: str
    model: str
    endpoint: str = ""
    api_key: str = ""
    auth_header: str = ""
    auth_prefix: str = ""
    pipeline_mode: str = "single"
    max_workers: int = 4
    timeout: float = 120.0
    max_retries: int = 2
    max_calls_per_case: int = 3
    send_seed: bool = False
    seed: int = 20261118
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 0
    json_mode: bool = False
    reasoning_effort: str = ""
    enable_thinking: bool | None = False
    output_alias_path: Path | None = None
    input_path: Path = field(default_factory=Path)
    output_path: Path = field(default_factory=Path)
    run_log_path: Path = field(default_factory=Path)
    prompt_dir: Path = field(default_factory=Path)
    notes: list[str] = field(default_factory=list)

    def masked_key(self) -> str:
        if not self.api_key:
            return "<none>"
        return f"{self.api_key[:5]}...{self.api_key[-4:]}" if len(self.api_key) > 12 else "<set>"

    def public_config(self) -> dict:
        """可安全写入日志的配置快照：不含任何密钥。"""
        return {
            "profile": self.profile,
            "model": self.model,
            "pipeline_mode": self.pipeline_mode,
            "max_workers": self.max_workers,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "max_calls_per_case": self.max_calls_per_case,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens or None,
            "json_mode": self.json_mode,
            "reasoning_effort": self.reasoning_effort or None,
            "seed": self.seed if self.send_seed else None,
            "auth": "none" if not self.api_key else f"header:{self.auth_header}",
        }


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是整数，当前为 {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是数字，当前为 {raw!r}") from exc


def _parse_thinking(raw: str) -> bool | None:
    """Qwen Thinking 三态：0 关闭（默认，控时长）/ 1 开启 / auto 不传交由服务端。"""
    value = (raw or "").strip().lower()
    if value in {"auto", "unset"}:
        return None
    if value in {"1", "true", "on", "yes"}:
        return True
    return False


def load_settings(base_dir: Path | None = None) -> Settings:
    """解析运行配置。

    通道选择规则：
      LLM_PROFILE=official  -> 必须提供 .env 的 MODEL_NAME/ENDPOINT，缺失即退出码 2
      LLM_PROFILE=mock      -> 不需要任何配置，用于离线联调
      LLM_PROFILE=dev-amd   -> 需要 AMD_API_KEY（只从环境变量读）
      LLM_PROFILE=dev-alt   -> 需要 ALT_BASE_URL / ALT_API_KEY / LLM_MODEL
      未设置                -> .env 存在且完整则 official，否则 mock（打印提示）
    """
    base = (base_dir or Path(__file__).resolve().parent.parent).resolve()
    dotenv = read_dotenv(base / ".env")
    profile = (os.environ.get("LLM_PROFILE") or "").strip().lower()

    if not profile:
        has_env_file = (base / ".env").exists()
        profile = PROFILE_OFFICIAL if (has_env_file and dotenv.get("MODEL_NAME") and dotenv.get("ENDPOINT")) else PROFILE_MOCK

    pipeline_mode = (os.environ.get("PIPELINE_MODE") or "single").strip().lower()
    if pipeline_mode not in {"single", "pipeline"}:
        raise ConfigError(f"PIPELINE_MODE 只能是 single 或 pipeline，当前为 {pipeline_mode!r}")

    settings = Settings(
        base_dir=base,
        profile=profile,
        model="",
        pipeline_mode=pipeline_mode,
        max_workers=max(1, _env_int("MAX_WORKERS", 1)),
        timeout=_env_float("LLM_TIMEOUT", 300.0),
        max_retries=max(0, _env_int("LLM_MAX_RETRIES", 1)),
        max_calls_per_case=max(1, _env_int("MAX_CALLS_PER_CASE", 1 if pipeline_mode == "single" else 3)),
        send_seed=(os.environ.get("LLM_SEND_SEED", "").strip() == "1"),
        temperature=_env_float("LLM_TEMPERATURE", 0.0),
        top_p=_env_float("LLM_TOP_P", 1.0),
        max_tokens=max(0, _env_int("LLM_MAX_TOKENS", 0)),
        json_mode=(os.environ.get("LLM_JSON_MODE", "").strip() == "1"),
        reasoning_effort=(os.environ.get("LLM_REASONING_EFFORT") or "").strip(),
        enable_thinking=_parse_thinking(os.environ.get("LLM_ENABLE_THINKING", "")),
        output_alias_path=None if os.environ.get("OUTPUT_ALIAS", "1").strip() == "0" else base / "results.json",
        input_path=Path(os.environ.get("CLINICAL_INPUT") or (base / "input.json")),
        output_path=base / "output.json",
        run_log_path=base / "run_log.json",
        prompt_dir=base / "prompts",
    )

    if profile == PROFILE_OFFICIAL:
        model = (dotenv.get("MODEL_NAME") or "").strip()
        endpoint = (dotenv.get("ENDPOINT") or "").strip()
        if not model or not endpoint:
            raise ConfigError(".env 必须提供非空的 MODEL_NAME 与 ENDPOINT")
        settings.model, settings.endpoint = model, endpoint
        settings.notes.append("official 通道：不发送任何鉴权头")

    elif profile == PROFILE_DEV_AMD:
        api_key = os.environ.get("AMD_API_KEY", "").strip()
        if not api_key:
            raise ConfigError("dev-amd 通道需要环境变量 AMD_API_KEY")
        base_url = (os.environ.get("AMD_BASE_URL") or "").strip().rstrip("/")
        if not base_url:
            raise ConfigError("dev-amd 通道需要环境变量 AMD_BASE_URL（仓库内不保存任何端点）")
        auth_mode = (os.environ.get("AMD_AUTH_MODE") or "bearer").strip().lower()
        if auth_mode not in AMD_AUTH_MODES:
            raise ConfigError(f"AMD_AUTH_MODE 只能是 {'/'.join(AMD_AUTH_MODES)}，当前为 {auth_mode!r}")
        settings.model = (os.environ.get("LLM_MODEL") or os.environ.get("AMD_MODEL") or DEFAULT_AMD_MODEL).strip()
        settings.endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        settings.api_key = api_key
        settings.auth_header, settings.auth_prefix = AMD_AUTH_MODES[auth_mode]
        settings.notes.append(f"dev-amd 通道：鉴权方式 {auth_mode}，仅用于开发期自测，禁止进入提交包")

    elif profile == PROFILE_DEV_ALT:
        base_url = (os.environ.get("ALT_BASE_URL") or "").strip().rstrip("/")
        api_key = os.environ.get("ALT_API_KEY", "").strip()
        model = (os.environ.get("LLM_MODEL") or "").strip()
        if not (base_url and api_key and model):
            raise ConfigError("dev-alt 通道需要 ALT_BASE_URL / ALT_API_KEY / LLM_MODEL")
        settings.endpoint = f"{base_url}/chat/completions"
        settings.model = model
        settings.api_key = api_key
        settings.auth_header = "Authorization"
        settings.auth_prefix = "Bearer "
        settings.notes.append("dev-alt 通道：仅用于开发期自测，禁止进入提交包")

    elif profile == PROFILE_MOCK:
        settings.model = "mock-deterministic"
        settings.notes.append("mock 通道：确定性模板输出，仅验证协议，不代表模型能力")

    else:
        raise ConfigError(f"未知 LLM_PROFILE: {profile!r}")

    if settings.model.startswith("mock") and settings.max_calls_per_case < 1:
        raise ConfigError("MAX_CALLS_PER_CASE 必须 >= 1")

    return settings
