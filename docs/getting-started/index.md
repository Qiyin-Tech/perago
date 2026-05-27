# Getting Started

本页介绍 Perago task module 的最小上手路径：先看完整 `@task` 示例、三个核心命令和生成的 TaskDef，再按需要理解 workspace、controls、guardrail 和 workspace-free task。

## 完整 workspace task

Workspace task 适合需要读取 LakeFS workspace，或读取后按需发布变更的 Conductor task。函数签名是 `(workspace: Path, params: ParamsModel) -> OutputModel`。

```python
from pathlib import Path

from pydantic import BaseModel, Field

from perago import (
    ExecutionLimits,
    RetryPolicy,
    TaskControls,
    TimeoutPolicy,
    WorkspaceSpec,
    forbid_glob,
    require_dir,
    require_glob,
    task,
)


class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)


class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)


@task(
    name="features.build",
    description="Build feature parquet files.",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/audio/render",
        pre=[
            require_dir("raw"),
            require_glob("raw/**/*.parquet", min_count=1),
        ],
        post=[
            require_dir("features"),
            require_glob("features/**/*.parquet", min_count=1),
            forbid_glob("**/*.tmp"),
        ],
    ),
    controls=TaskControls(
        retry=RetryPolicy(count=4, logic="FIXED", delay_seconds=30),
        timeout=TimeoutPolicy(response_seconds=900),
        limits=ExecutionLimits(concurrent_exec_limit=2),
    ),
)
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    features = workspace / "features"
    features.mkdir(exist_ok=True)
    (features / f"{params.feature_set}.parquet").write_text("ok", encoding="utf-8")
    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

任务作者需要维护的是 `name`、`owner_email`、可选 `description`、workspace 声明、controls、Pydantic params/output 和函数体。`inputKeys`、`outputKeys`、JSON Schema、retry/timeout 字段和 execution limit 字段由 Perago 生成。

深入阅读：{doc}`workspace-task` 和 {doc}`examples`。

## Task failure signaling

Task return value 表示成功的业务 `Result Output`。如果函数正常返回
`OutputModel(...)`，Perago 会把 attempt 当成 `COMPLETED`，并把返回值写到
Conductor `output.result`。业务字段例如 `status="REJECTED"` 或
`status="NEEDS_ACTION"` 不会让 Conductor task 失败；它们应该由 WorkflowDef
分支处理。

Perago 使用异常表达执行失败：

```python
from perago import TaskFailed, TaskTerminalError


def call_model(params: GenerateParams) -> GenerateOutput:
    if prompt_is_blocked(params.prompt):
        return GenerateOutput(status="REJECTED", reason_code="PROMPT_POLICY_VIOLATION")

    if temporary_rate_limit():
        raise TaskFailed("model service rate limited this attempt")

    if params.song_id == "missing":
        raise TaskTerminalError("song_id does not exist")

    return GenerateOutput(status="READY")
```

| 情况 | 推荐机制 | Conductor status |
| --- | --- | --- |
| 同一 input 稍后重跑可能成功 | `raise TaskFailed("...")` | `FAILED`，按 retry policy 重试 |
| 用户、上游或 workflow 分支可恢复 | `return Output(status="REJECTED" / "NEEDS_ACTION")` | `COMPLETED`，由 workflow 分支处理 |
| 同一 input 自动重试没有意义 | `raise TaskTerminalError("...")` | `FAILED_WITH_TERMINAL_ERROR`，不重试 |

失败 reason 是短字符串诊断。MVP 使用 `PERAGO_FAILURE_REASON_MAX_LENGTH`
限制写入 Conductor `reasonForIncompletion` 的文本长度，完整细节进入 worker
JSONL 日志。

深入阅读：{doc}`../reference/failure-classification` 和
{doc}`../architecture/adr/0005-use-exceptions-for-task-execution-failures`。

## 三个核心命令

```bash
perago check app.workers.features_build
perago extract app.workers.features_build --output generated/features.build.json
perago start app.workers.features_build -j 2
```

`perago check` 会导入 module、校验 task contract、加载 runtime config，并确认 TaskDef 可以生成。它不连接 Conductor 或 LakeFS。

`perago extract` 使用同一套校验，把 generated Conductor TaskDef 写到指定 `.json` 文件。它不会注册 TaskDef。

`perago start` 是长运行 worker 入口。启动前需要 `CONDUCTOR_SERVER_URL`、LakeFS endpoint、LakeFS access key、LakeFS secret key 已配置，并且 Conductor 中已经注册同名 TaskDef。

本仓库 fixture 示例在 `tests/fixtures` 下，本地验证 fixture 时用：

```bash
PYTHONPATH=tests/fixtures uv run perago check app.workers.features_build
PYTHONPATH=tests/fixtures uv run perago extract app.workers.features_build --output /tmp/features.build.json
```

深入阅读：{ref}`development-runtime`，以及 {doc}`../runtime/cli`、{doc}`../runtime/configuration`、{doc}`../runtime/conductor`。

## 生成的 TaskDef

上面的 task 会生成类似下面的 Conductor TaskDef。下面的示例保留 task 作者最常核对的核心结构：

```json
{
  "name": "features.build",
  "ownerEmail": "data@example.com",
  "description": "Build feature parquet files.",
  "retryCount": 4,
  "retryLogic": "FIXED",
  "retryDelaySeconds": 30,
  "maxRetryDelaySeconds": 0,
  "backoffJitterMs": 0,
  "totalTimeoutSeconds": 0,
  "timeoutPolicy": "TIME_OUT_WF",
  "timeoutSeconds": 0,
  "responseTimeoutSeconds": 900,
  "pollTimeoutSeconds": 0,
  "concurrentExecLimit": 2,
  "inputKeys": ["workspace", "params"],
  "outputKeys": ["workspace", "result"],
  "inputSchema": {
    "name": "features.build.input",
    "version": 1,
    "type": "JSON",
    "data": {
      "type": "object",
      "required": ["workspace", "params"],
      "additionalProperties": false,
      "properties": {
        "workspace": {
          "type": "object",
          "required": ["repository", "branch", "ref_type", "ref"],
          "properties": {
            "repository": {"type": "string"},
            "branch": {"type": "string"},
            "ref_type": {"const": "commit", "type": "string"},
            "ref": {"type": "string"}
          }
        },
        "params": {
          "type": "object",
          "required": ["feature_set", "min_rows"],
          "additionalProperties": false,
          "properties": {
            "feature_set": {"type": "string"},
            "min_rows": {"type": "integer", "minimum": 1}
          }
        }
      }
    }
  },
  "outputSchema": {
    "name": "features.build.output",
    "version": 1,
    "type": "JSON",
    "data": {
      "type": "object",
      "required": ["workspace", "result"],
      "additionalProperties": false
    }
  }
}
```

`WorkspaceSpec.prefix`、`WorkspaceSpec.read_only`、`pre` / `post` guardrail、LakeFS endpoint、credentials、attempt branch、publish fence 和 `publish_budget` 原始字段不会写入 TaskDef。

深入阅读：{doc}`controls-and-taskdef`、{doc}`../reference/conductor-taskdef` 和 {doc}`../reference/input-output-contract`。

## Control 参数

`TaskControls` 是 task 作者影响 Conductor 执行控制字段的唯一入口。没有特殊控制需求时可以省略 `controls`，默认等价于 `TaskControls()`。

| Perago 参数 | TaskDef 字段 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `retry.count` | `retryCount` | `3` | 允许 `0..10`。 |
| `retry.logic` | `retryLogic` | `"FIXED"` | 可选 `"FIXED"`、`"EXPONENTIAL_BACKOFF"`、`"LINEAR_BACKOFF"`。 |
| `retry.delay_seconds` | `retryDelaySeconds` | `60` | 初始 retry delay。 |
| `retry.max_delay_seconds` | `maxRetryDelaySeconds` | `0` | 最大 retry delay。 |
| `retry.jitter_ms` | `backoffJitterMs` | `0` | backoff jitter。 |
| `timeout.policy` | `timeoutPolicy` | `"TIME_OUT_WF"` | 可选 `"RETRY"`、`"TIME_OUT_WF"`、`"ALERT_ONLY"`。 |
| `timeout.seconds` | `timeoutSeconds` | `0` | Conductor task timeout。 |
| `timeout.response_seconds` | `responseTimeoutSeconds` | `600` | 没有 publish budget 时使用。 |
| `timeout.poll_seconds` | `pollTimeoutSeconds` | `0` | Conductor poll timeout。 |
| `timeout.total_seconds` | `totalTimeoutSeconds` | `0` | Conductor total timeout。 |
| `limits.concurrent_exec_limit` | `concurrentExecLimit` | omitted | 为 `None` 时不写入 JSON。 |
| `limits.rate_limit_frequency_in_seconds` | `rateLimitFrequencyInSeconds` | omitted | 必须和 `rate_limit_per_frequency` 成对配置。 |
| `limits.rate_limit_per_frequency` | `rateLimitPerFrequency` | omitted | 必须和 `rate_limit_frequency_in_seconds` 成对配置。 |
| `publish_budget` | derives `responseTimeoutSeconds` | `None` | 只允许 workspace task 使用；read-only workspace task 上会被忽略并发出 warning。 |

`PublishBudget` 用 workspace publication 的运行时预算派生 `responseTimeoutSeconds`：

```python
from perago import PublishBudget, TaskControls, TimeoutPolicy


controls = TaskControls(
    timeout=TimeoutPolicy(response_seconds=999),
    publish_budget=PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=45,
        conductor_completion_timeout_seconds=15,
        worker_shutdown_grace_seconds=30,
        heartbeat_interval_seconds=10,
    ),
)
```

上例的 `responseTimeoutSeconds` 取 `45 + 15 + 30 + 10 = 100`。`lakefs_merge_timeout_seconds` 必须覆盖 `observed_merge_p99_seconds + safety_margin_seconds`。

深入阅读：{doc}`controls-and-taskdef` 和 {doc}`../runtime/publish-budget`。

## Workspace 和 guardrail

`WorkspaceSpec(prefix=...)` 决定从 LakeFS repository 的哪个 object prefix 投影到本地 attempt workspace。`"/"` 表示 repository root，其他值会归一化成相对 prefix。

`WorkspaceSpec(read_only=True)` 表示 workspace task 只读取版本化 workspace，不发布新 workspace ref。成功 output 的 `workspace.ref` 保持为 input ref；runtime 不检查 target branch HEAD、不创建 staging branch、不提交 LakeFS commit。默认 `read_only=False`，可写 workspace task 在 diff 为空时 Perago 不会创建 empty commit。

`pre` guardrail 在 task body 运行前检查下载后的 workspace，`post` guardrail 在 task body 运行后、completion 路径选择前检查输出。常用 guardrail：

| 函数 | 用途 |
| --- | --- |
| `require_file("path")` | 要求文件存在。 |
| `require_dir("path")` | 要求目录存在。 |
| `require_glob("pattern", min_count=1)` | 要求 glob 至少匹配指定数量。 |
| `forbid_glob("pattern")` | 禁止 glob 匹配任何路径。 |

Guardrail 失败会阻止 task body 或 workspace completion 继续执行；guardrail 本身不写入 Conductor TaskDef。

深入阅读：{doc}`guardrails`、{doc}`../runtime/lakefs` 和 {doc}`../runtime/workspace-publication`。

## Workspace-free task

不需要 LakeFS workspace 的 task 使用 `(params: ParamsModel) -> OutputModel` 签名，并且不能声明 `workspace=WorkspaceSpec(...)`。需要读取 LakeFS workspace 但不发布变更的节点仍然是 workspace task，应声明 `WorkspaceSpec(read_only=True)`。

```python
from pydantic import BaseModel, Field

from perago import task


class ValidateMetadataParams(BaseModel):
    song_id: str
    min_duration_seconds: int = Field(ge=1)


class ValidateMetadataOutput(BaseModel):
    valid: bool
    reason: str | None = None


@task(
    name="metadata.validate",
    description="Validate song metadata.",
    owner_email="data@example.com",
)
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    return ValidateMetadataOutput(valid=True)
```

Workspace-free TaskDef 的 `inputKeys` 是 `["params"]`，`outputKeys` 是 `["result"]`。它可以使用 retry、timeout 和 execution limit controls，但不能配置 `publish_budget`。

深入阅读：{doc}`workspace-free-task` 和 {doc}`pydantic-contracts`。

## 常见边界

- 一个 module 只能声明一个 Perago task。
- `params` 和返回值必须是 Pydantic model。
- workspace task 的第一个参数必须是 `workspace: Path`。
- 业务参数必须收敛到单个 `params` model，不能拆成多个函数参数。
- decorator 不能接收 `inputKeys`、`outputKeys`、`inputSchema`、`outputSchema`、`params` 或 `output` 这类生成字段。

深入阅读：{doc}`../concepts/task-module`、{doc}`../concepts/task-contract` 和 {doc}`../reference/troubleshooting`。

## 继续阅读

LakeFS publication 失败语义和 fence 模型见 {doc}`../lakefs-publication-protocol`。继续深入时，可从 {doc}`../development` 进入 runtime、reference、architecture 和 API 维护资料。

```{toctree}
:maxdepth: 1

workspace-task
workspace-free-task
pydantic-contracts
guardrails
controls-and-taskdef
examples
```
