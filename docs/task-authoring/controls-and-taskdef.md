# Controls and TaskDef

`TaskControls` 是任务作者能影响 Conductor TaskDef 执行控制字段的唯一入口。业务 input/output、workspace schema、TaskDef key 列表和 JSON Schema 都由 Perago 从函数签名与 Pydantic model 生成，不在 `@task(...)` 里重复声明。

## 最小形状

没有特殊控制需求时，可以省略 `controls`。Perago 会使用 `TaskControls()` 的默认值：

```python
from pathlib import Path

from pydantic import BaseModel, Field

from perago import WorkspaceSpec, task


class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)


class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/audio/render"),
)
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...
```

需要覆盖 retry、timeout 或 execution limit 时，把 `TaskControls` 作为 decorator metadata 传入：

```python
from perago import ExecutionLimits, RetryPolicy, TaskControls, TimeoutPolicy


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/audio/render"),
    controls=TaskControls(
        retry=RetryPolicy(count=4, logic="FIXED", delay_seconds=30),
        timeout=TimeoutPolicy(response_seconds=900),
        limits=ExecutionLimits(concurrent_exec_limit=2),
    ),
)
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...
```

Required/optional/generated 字段边界：

- required: `name` 和 `owner_email` 必须由任务作者显式提供。
- optional: `description` 和 `controls` 可以省略；`controls` 省略时使用 `TaskControls()`。
- conditional: `workspace=WorkspaceSpec(...)` 只在 workspace task 上 required，workspace-free task 上 forbidden。
- generated: `inputKeys`、`outputKeys`、`inputSchema`、`outputSchema`、`responseTimeoutSeconds` 的最终值和业务 schema 由 Perago 生成。
- forbidden: 任务作者不能在 decorator 里提供 `params`、`output`、`inputTemplate` 或 Conductor schema 字段。

## 控制字段映射

`perago extract` 会把 `TaskControls` 展开到 Conductor TaskDef 顶层字段。值为 `None` 的 execution limit 字段会从生成的 JSON 中省略。

| Perago 字段 | Conductor TaskDef 字段 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `name` | `name` | 无 | 任务名，必须显式提供。 |
| `owner_email` | `ownerEmail` | 无 | Conductor owner email，必须显式提供。 |
| `description` | `description` | `None` | 为 `None` 时省略。 |
| `controls.retry.count` | `retryCount` | `3` | 允许 `0..10`。 |
| `controls.retry.logic` | `retryLogic` | `"FIXED"` | 可选 `"FIXED"`、`"EXPONENTIAL_BACKOFF"`、`"LINEAR_BACKOFF"`。 |
| `controls.retry.delay_seconds` | `retryDelaySeconds` | `60` | 非负整数。 |
| `controls.retry.max_delay_seconds` | `maxRetryDelaySeconds` | `0` | 非负整数。 |
| `controls.retry.jitter_ms` | `backoffJitterMs` | `0` | 非负整数。 |
| `controls.timeout.total_seconds` | `totalTimeoutSeconds` | `0` | 非负整数。 |
| `controls.timeout.policy` | `timeoutPolicy` | `"TIME_OUT_WF"` | 可选 `"RETRY"`、`"TIME_OUT_WF"`、`"ALERT_ONLY"`。 |
| `controls.timeout.seconds` | `timeoutSeconds` | `0` | 非负整数。 |
| `controls.timeout.response_seconds` | `responseTimeoutSeconds` | `600` | 没有 publish budget 时使用。 |
| `controls.timeout.poll_seconds` | `pollTimeoutSeconds` | `0` | 非负整数。 |
| `controls.limits.concurrent_exec_limit` | `concurrentExecLimit` | `None` | 为 `None` 时省略。 |
| `controls.limits.rate_limit_frequency_in_seconds` | `rateLimitFrequencyInSeconds` | `None` | 必须和 `rate_limit_per_frequency` 成对配置。 |
| `controls.limits.rate_limit_per_frequency` | `rateLimitPerFrequency` | `None` | 必须和 `rate_limit_frequency_in_seconds` 成对配置。 |
| `controls.publish_budget` | derives `responseTimeoutSeconds` | `None` | 只允许 workspace task 使用，不直接写入 TaskDef。 |

所有 control model 都使用 Pydantic 校验并拒绝未知字段。校验失败会在模块导入、`perago check` 或 `perago extract` 阶段暴露为 task definition 错误。

## 生成的 TaskDef 形状

workspace task 的 TaskDef input/output key 固定为：

```text
inputKeys = ["workspace", "params"]
outputKeys = ["workspace", "result"]
```

workspace-free task 的 TaskDef input/output key 固定为：

```text
inputKeys = ["params"]
outputKeys = ["result"]
```

`workspace` schema 来自 Perago 的 `WorkspaceInput` / `WorkspaceOutput`，`params` 和 `result` schema 来自任务函数的 Pydantic 类型注解。`perago extract` 还会把嵌套 schema inline，删除 Pydantic `title`，并把 object schema 关闭为 `additionalProperties: false`。

Perago 不生成 Conductor `inputTemplate`。Pydantic 字段默认值会保留在 JSON Schema 里，但不会被复制到 TaskDef 顶层的 input template 中。

Guardrail 也不会写入 TaskDef。`require_file`、`require_dir`、`require_glob` 和 `forbid_glob` 是 Perago runtime metadata，只影响 workspace 准备前后的本地检查。

## Publish budget

`PublishBudget` 用来把 workspace publication 的本地运行时预算折算成 Conductor `responseTimeoutSeconds`：

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

上面的 `responseTimeoutSeconds` 会生成成 `100`，而不是 `999`：

```text
45 + 15 + 30 + 10 = 100
```

`PublishBudget` 本身不会写入 TaskDef JSON。它给 runtime 提供 LakeFS merge request timeout，并把 Conductor completion 阶段预留计入 `responseTimeoutSeconds`。SDK `TaskRunner` owns completion result update；Perago 当前不把 `conductor_completion_timeout_seconds` 写成 SDK 内部 HTTP request timeout。`lakefs_merge_timeout_seconds` 必须覆盖 `observed_merge_p99_seconds + safety_margin_seconds`，否则校验失败。

`publish_budget` 只适用于 workspace task。workspace-free task 没有 LakeFS publication 阶段，配置 `TaskControls(publish_budget=...)` 会被拒绝。

## 常见拒绝形状

只配置 rate limit 的一半会失败：

```python
TaskControls(
    limits=ExecutionLimits(rate_limit_frequency_in_seconds=60),
)
```

workspace-free task 配置 publish budget 会失败：

```python
@task(
    name="metadata.validate",
    owner_email="data@example.com",
    controls=TaskControls(publish_budget=budget),
)
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    ...
```

把 Conductor 生成字段塞回 decorator 也会失败：

```python
@task(
    name="features.build",
    owner_email="data@example.com",
    params=BuildFeaturesParams,
    output=BuildFeaturesOutput,
)
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...
```

这些字段的真源分别是函数签名、Pydantic model 和 `TaskControls`。任务作者只维护这些 Python 类型和 metadata，再用 `perago check` 验证，用 `perago extract` 生成 TaskDef JSON。
