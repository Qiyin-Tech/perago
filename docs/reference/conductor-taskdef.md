# Conductor TaskDef

Perago 只生成 Conductor `SIMPLE` task 的 TaskDef。TaskDef 必须先注册到 Conductor，workflow 才能调度同名 task；运行时 worker 不会在 `perago start` 时自动创建或更新 TaskDef。

TaskDef 的真源是 task module：

- decorator metadata 提供 `name`、`owner_email`、可选 `description` 和 `controls`。
- 函数签名决定 task 是 workspace task 还是 workspace-free task。
- Pydantic params/output model 生成 `inputSchema` 和 `outputSchema`。
- `WorkspaceSpec`、guardrail、LakeFS prefix 和本地运行配置不直接写入 Conductor TaskDef。

## 生成入口

任务作者通常通过 CLI 生成 JSON：

```bash
perago extract app.workers.features_build --output generated/features.build.json
```

库调用入口是：

```python
from pathlib import Path

from perago import build_taskdef, load_module_task, write_taskdef

task_def = build_taskdef(load_module_task("app.workers.features_build"))
write_taskdef(load_module_task("app.workers.features_build"), Path("generated/features.build.json"))
```

`write_taskdef` 的输出路径必须以 `.json` 结尾，并且不能指向目录。

## 顶层字段

| TaskDef 字段 | 来源 | Required | 默认值 / 省略规则 |
| --- | --- | --- | --- |
| `name` | `@task(name=...)` | yes | 无默认值；空白和非法路径形状会被拒绝。 |
| `ownerEmail` | `@task(owner_email=...)` | yes | 无默认值；空白会被拒绝。 |
| `description` | `@task(description=...)` | no | `None` 时省略。 |
| `retryCount` | `controls.retry.count` | generated | 默认 `3`，允许 `0..10`。 |
| `retryLogic` | `controls.retry.logic` | generated | 默认 `"FIXED"`；可选 `"FIXED"`、`"EXPONENTIAL_BACKOFF"`、`"LINEAR_BACKOFF"`。 |
| `retryDelaySeconds` | `controls.retry.delay_seconds` | generated | 默认 `60`；必须非负。 |
| `maxRetryDelaySeconds` | `controls.retry.max_delay_seconds` | generated | 默认 `0`；必须非负。 |
| `backoffJitterMs` | `controls.retry.jitter_ms` | generated | 默认 `0`；必须非负。 |
| `totalTimeoutSeconds` | `controls.timeout.total_seconds` | generated | 默认 `0`；必须非负。 |
| `timeoutPolicy` | `controls.timeout.policy` | generated | 默认 `"TIME_OUT_WF"`；可选 `"RETRY"`、`"TIME_OUT_WF"`、`"ALERT_ONLY"`。 |
| `timeoutSeconds` | `controls.timeout.seconds` | generated | 默认 `0`；必须非负。 |
| `responseTimeoutSeconds` | `controls.response_timeout_seconds` | generated | 默认 `600`；有 `publish_budget` 时使用预算派生值。 |
| `pollTimeoutSeconds` | `controls.timeout.poll_seconds` | generated | 默认 `0`；必须非负。 |
| `concurrentExecLimit` | `controls.limits.concurrent_exec_limit` | no | `None` 时省略；非 `None` 时必须非负。 |
| `rateLimitFrequencyInSeconds` | `controls.limits.rate_limit_frequency_in_seconds` | no | `None` 时省略；必须和 `rateLimitPerFrequency` 成对配置。 |
| `rateLimitPerFrequency` | `controls.limits.rate_limit_per_frequency` | no | `None` 时省略；必须和 `rateLimitFrequencyInSeconds` 成对配置。 |
| `inputKeys` | task 类型 | generated | workspace task 为 `["workspace", "params"]`；workspace-free task 为 `["params"]`。 |
| `outputKeys` | task 类型 | generated | workspace task 为 `["workspace", "result"]`；workspace-free task 为 `["result"]`。 |
| `inputSchema` | Pydantic model + workspace model | generated | 始终生成 JSON Schema wrapper。 |
| `outputSchema` | Pydantic model + workspace model | generated | 始终生成 JSON Schema wrapper。 |

值为 `None` 的 control 字段不会写入 JSON。普通数字默认值会写入 JSON，例如默认 `retryCount`、`timeoutSeconds` 和 `pollTimeoutSeconds`。

## Input keys and schema

workspace task 的 input 顶层字段固定为：

```json
{
  "workspace": {
    "repository": "repo",
    "branch": "main",
    "ref_type": "commit",
    "ref": "abc123"
  },
  "params": {}
}
```

对应 TaskDef：

```json
{
  "inputKeys": ["workspace", "params"],
  "inputSchema": {
    "name": "features.build.input",
    "version": 1,
    "type": "JSON",
    "data": {
      "type": "object",
      "properties": {
        "workspace": {},
        "params": {}
      },
      "required": ["workspace", "params"],
      "additionalProperties": false
    }
  }
}
```

workspace-free task 没有 `workspace` 字段：

```json
{
  "inputKeys": ["params"]
}
```

`params` schema 来自任务函数第二个参数或唯一参数的 Pydantic 类型注解。Perago 会 inline `$ref`、删除 Pydantic `title`，并把所有 object schema 设置为 `additionalProperties: false`。

## Output keys and schema

workspace task 的 output 顶层字段固定为：

```json
{
  "workspace": {
    "repository": "repo",
    "branch": "main",
    "ref_type": "commit",
    "ref": "def456"
  },
  "result": {}
}
```

对应 TaskDef 的 `outputKeys` 为 `["workspace", "result"]`。workspace-free task 的 `outputKeys` 为 `["result"]`。

`result` schema 来自任务函数返回值的 Pydantic 类型注解。Pydantic 字段默认值会保留在 JSON Schema 内，但 Perago 不生成 Conductor `inputTemplate`，也不会把业务默认值提升到 TaskDef 顶层。

## Publish budget

`PublishBudget` 不会作为字段写入 TaskDef。它只覆盖生成出来的 `responseTimeoutSeconds`：

```text
responseTimeoutSeconds =
  lakefs_merge_timeout_seconds
  + conductor_completion_timeout_seconds
  + worker_shutdown_grace_seconds
  + heartbeat_interval_seconds
```

如果没有 `publish_budget`，`responseTimeoutSeconds` 来自 `controls.timeout.response_seconds`，默认是 `600`。

## 不写入 TaskDef 的 Perago 信息

以下信息属于 Perago runtime 或 task authoring 边界，不属于 Conductor TaskDef：

- `WorkspaceSpec.prefix`
- `WorkspaceSpec.pre` / `WorkspaceSpec.post`
- `require_file`、`require_dir`、`require_glob`、`forbid_glob`
- LakeFS endpoint、credentials、repository 默认值
- workspace root、log root、worker id prefix
- `publish_budget` 的各个原始字段
- attempt snapshot、publish fence metadata、staging branch

这些信息分别在 task module、运行时环境变量、Conductor input、LakeFS commit metadata 或 worker 本地状态中生效。

## 常见拒绝形状

`description` 以外的 TaskDef schema 字段不能从 decorator 传入：

```python
@task(
    name="features.build",
    owner_email="data@example.com",
    inputKeys=["workspace", "params"],
)
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...
```

只配置一半 rate limit 会被拒绝：

```python
TaskControls(
    limits=ExecutionLimits(rate_limit_frequency_in_seconds=60),
)
```

workspace-free task 配置 publish budget 也会被拒绝，因为它没有 LakeFS publication 阶段：

```python
@task(
    name="metadata.validate",
    owner_email="data@example.com",
    controls=TaskControls(publish_budget=budget),
)
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    ...
```
