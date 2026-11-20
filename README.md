# 临床智录 —— 住院首次病程记录智能生成

> 2026 LLM 应用工程赛备赛项目的**代码仓库**。读取带"资料截止点"的教学病例，调用主办方指定模型，产出符合固定 JSON Schema 的首次病程记录草稿。

本仓库根目录即提交包根目录（附件 3 要求 `main.py` 直接位于根层，不得多套一层目录）。

## 库外资料（不进本仓库）

赛题分析、题目原文与规划文档不属于代码，为避免无关改动污染历史，它们保留在仓库上一级目录的 `Q_analysis_00\` 下（与本草稿仓库平级）：

| 内容 | 位置 |
|---|---|
| 赛题拆解与风险分析 | `..\Q_analysis_00\赛题分析.md` |
| 题目原文（21 页 PDF） | `..\Q_analysis_00\题目.pdf` |
| 各阶段规划与中间产物 | `..\Q_analysis_00\2026-11-18\` |

本仓库只放：可执行的代码、依赖清单、提示词、测试与提交说明。

## 赛题硬约束（来自附件 3，代码必须遵守）

- Python 3.11，单入口 `python main.py`，工作目录以 `main.py` 所在目录为基准
- 输入输出固定为同目录 `input.json` → `output.json`（UTF-8，标准 JSON 数组，条数与顺序一致）
- 只从 `.env` 读取 `MODEL_NAME`、`ENDPOINT` 两项；不得硬编码模型名/地址，不得要求 API Key
- 不调用指定模型以外的任何模型（含本地模型）
- 退出码：0 成功 / 1 运行失败 / 2 输入错误；输出文件原子写入
- 提交包根目录直接含 `main.py`、`requires.txt`、`.env.example`、`README.md`，可选 `src/`、`prompts/`

## 目录结构

```text
main.py                # 唯一入口：读配置 → 读 input.json → 逐例生成 → 原子写 output.json
requires.txt           # 依赖清单（本项目零第三方依赖，仅满足协议要求）
.env.example           # 配置模板（仅 MODEL_NAME / ENDPOINT）
src/
  config.py            # 四通道配置解析（official / dev-amd / dev-alt / mock）
  input_source.py      # 输入源适配：json / jsonl / 目录 + 字段别名归一
  model_client.py      # 模型通道适配：HTTP 客户端与 mock 客户端同接口
  mock_composer.py     # mock 通道的确定性合成器
  time_filter.py       # 按资料截止点剔除越界表述
  prompts.py           # Prompt 组装（带版本号）
  pipeline.py          # 并发编排、逐例失败隔离
  schema.py            # 模型响应三层解析 + 结构校验清洗
  validators.py        # 数值回溯、否定/缺失保护、计划措辞检查
  io_utils.py          # 临时文件 + 原子替换
  logging_utils.py     # run_log 组装（不含密钥）
prompts/               # single_v1 / extract_v1 / generate_v1 / verify_v1
dev/                   # 开发期资产，不打包：自测集、fixture、检查器、协议自测
  datasets/            # 10 例自测集（含否定、跨截止点、全缺失、多病共存等陷阱）
  fixtures/            # DEMO001 参考输出
  scripts/             # check_results.py / selftest_protocol.py / verify_http_channel.py
  reports/             # 检查报告（运行时生成）
```

## 快速开始

```bash
pip install -r requires.txt          # 零依赖，命令成功即可

# 1) 离线演示（无需任何模型与密钥）
set LLM_PROFILE=mock
set CLINICAL_INPUT=dev/datasets/demo_cases.json
python main.py

# 2) 正式评测（主办方提供 .env）
copy .env.example .env               # 填入 MODEL_NAME / ENDPOINT
python main.py

# 3) 结果自检
python dev/scripts/check_results.py
python dev/scripts/selftest_protocol.py
```

## 通道与可替换点

| `LLM_PROFILE` | 用途 | 鉴权 | 配置来源 |
|---|---|---|---|
| `official`（默认，.env 存在时） | 正式评测、线下复现 | **不发任何鉴权头** | `.env` 的 `MODEL_NAME` / `ENDPOINT` |
| `mock`（默认，无 .env 时） | 离线联调、协议回归、演示 | 无 | 无需配置 |
| `dev-amd` | 开发期 AMD 网关对照（已实测连通） | `Authorization: Bearer` | `AMD_API_KEY`（必填）/ `AMD_BASE_URL` / `LLM_MODEL` |
| `dev-alt` | 开发期任意 OpenAI 兼容服务 | `Authorization: Bearer` | `ALT_BASE_URL` / `ALT_API_KEY` / `LLM_MODEL` |

AMD 通道（2026-11-18 实测）：网关地址与密钥**只从环境变量读取**（`AMD_BASE_URL` / `AMD_API_KEY`），本仓库与提交包内不保存任何端点或密钥。该网关用 `Authorization: Bearer <key>`，`Ocp-Apim-Subscription-Key` 会返回 401；若换成 Azure APIM 形态的网关，设 `AMD_AUTH_MODE=apim` 即可，无需改代码。实测模型 id `Qwen3.8-27B`（服务端回包为 `self-dploy/Qwen3.8-27B`），详细纪要见赛题分析目录下的 `amd_gateway_接入说明.md`。

`Qwen3.8-27B` 是推理模型：思考 token 占用 `max_tokens`，预算过小会得到 `finish_reason=length` 且 `content` 为空。客户端已对空 `content` 做预算翻倍重试（`EMPTY_CONTENT`），并支持三个相关开关：

- `LLM_MAX_TOKENS`：单次生成上限，`0` 表示用服务端默认；接 AMD 推理模型建议 `4096`
- `LLM_JSON_MODE=1`：发送 `response_format={"type":"json_object"}`（仅当网关声明 `json_output` 支持时开启）
- `LLM_REASONING_EFFORT`：透传 `reasoning_effort`，可压思考长度以降低延迟

其他可替换点：

- **输入材料**：`CLINICAL_INPUT` 可指向 `.json`、`.jsonl` 或目录；字段名不同（`id`/`materials`/`text` 等）由 `input_source.normalize_case` 统一，主流程不变。
- **流水线**：`PIPELINE_MODE=single|pipeline`；`MAX_CALLS_PER_CASE` 低于 3 时 pipeline 自动降级为单次调用。
- **并发与超时**：`MAX_WORKERS`、`LLM_TIMEOUT`、`LLM_MAX_RETRIES`。
- **时序过滤**：默认开启，`TIME_FILTER=0` 关闭。

## 评测前提与参数约定（依据赛题组官方问答，2026-11-18）

| 前提 | 对代码的影响 |
|---|---|
| 评测病例只有 **1 例**（企业赛、总决赛各一份，全队同一份） | 目标从"平均分"改为"这一例不出错"；默认 `PIPELINE_MODE=single`，`pipeline` 保留为可切换的增强项 |
| 官方模型 `qwen3.8-27b` **默认开启 Thinking** | 必须显式管理：`LLM_ENABLE_THINKING=0`（默认，关）/ `1`（开）/ `auto`（不传，交由服务端） |
| 单次请求超时、单病例总时长、调用与重试上限**尚未发布** | 默认 `MAX_WORKERS=1`、`LLM_TIMEOUT=300`、`LLM_MAX_RETRIES=1`（实测单例最长 269s），全部可用环境变量收紧 |
| **重试计入模型调用预算** | `run_log` 的 `calls` 是真实尝试次数（含重试），不是"逻辑调用次数" |
| `output.json` / `results.json` 命名未统一 | 默认**同时产出两个文件**（内容一致），`OUTPUT_ALIAS=0` 关闭别名 |
| 禁止任何第三方 API/在线服务，病例不得外传 | `dev-amd` / `dev-alt` 仅在开发期自用，地址与密钥只走环境变量；`dev/` 不进提交包 |
| 允许本地知识库/规则库，需登记；未使用填"无" | 本项目**未使用**外部知识库（登记为"无"），零第三方依赖 |
| 支持 `temperature` / `max_tokens` / `response_format` / 顶层 `seed` / `stream` | 均已实现：`LLM_TEMPERATURE`、`LLM_MAX_TOKENS`、`LLM_JSON_MODE`、`LLM_SEND_SEED=1` |

关闭 Thinking 的请求体字段（vLLM 原生）：

```json
{"chat_template_kwargs": {"enable_thinking": false}}
```

## 提交包排除清单

打包时只放：`main.py`、`requires.txt`、`.env.example`、`README.md`、`src/`、`prompts/`。
以下内容严禁进入 zip：`.env`、`.env.local`、`dev/`、`output.json`、`run_log.json`、`__pycache__/`、任何密钥或真实病例数据。

## 版本控制约定

**分支**：比赛期间只用 `main` 一条长期分支，且始终可 `python main.py` 跑通。功能开发开短分支 `feat/xxx`、`fix/xxx`，完成后合回 `main` 并删除；里程碑打 tag（如 `submission-v1`），tag 名与归档 zip 对应。

**提交粒度**：一个提交只做一件事（一次重构、一个模块、一次 prompt 版本更新）。不提交半成品，也不把无关改动混在一起。

**提交信息**（Conventional Commits 中文版）：

```text
<type>(<scope>): <简述>

feat(pipeline): 增加按截止点过滤时序
fix(io): 修正原子写入在 Windows 上的替换失败
prompt(gen): 生成提示词升到 v2，强化数值照抄约束
test(validators): 补充否定词保留用例
docs: 补充 README 运行说明
chore: 更新 .gitignore
```

**提交前自检**：`git status` 确认没有 `.env`、日志、`output.json` 混入；`git diff --cached` 过一遍改动。

## 红线

- `.env`、任何密钥、真实病例数据、`run_log` 一律不入库
- 赛题分析、题目原文、规划文档不入库，改动它们不应产生提交
- 提示词改动必须同步更新文件名版本号，并在 `run_log` 中记录，保证"代码 / 提示词 / 参数 / 结果"可复现
- 仓库保持 private，比赛期间不公开代码与病例数据
