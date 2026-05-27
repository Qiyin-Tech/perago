# Publish Budget

`PublishBudget` 把 workspace publication 的运维时间边界写进 task metadata。它只对可写 workspace task 生效，用来约束 LakeFS merge，并记录 Conductor completion 阶段预留、worker shutdown grace 和 heartbeat slack。

这个页面面向运行时维护者和需要给 workspace task 配置发布预算的任务作者。TaskDef 字段映射见 `../getting-started/controls-and-taskdef.md`；LakeFS 发布顺序见 `workspace-publication.md`。

## 何时需要配置

默认 `TaskControls(timeout=TimeoutPolicy(response_seconds=600))` 只表达 Conductor response timeout。对短任务或没有真实 LakeFS publication 压力的任务，这通常足够。

当可写 workspace task 会修改大量 object、LakeFS merge latency 已经有观测值，或 worker shutdown 与 Conductor completion 阶段需要明确留量时，配置 `publish_budget`：

```python
from pathlib import Path

from pydantic import BaseModel, Field

from perago import PublishBudget, TaskControls, WorkspaceSpec, task


class BuildFeaturesParams(BaseModel):
    feature_set: str


class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/audio/render"),
    controls=TaskControls(
        publish_budget=PublishBudget(
            observed_merge_p99_seconds=20,
            safety_margin_seconds=10,
            lakefs_merge_timeout_seconds=45,
            conductor_completion_timeout_seconds=15,
            worker_shutdown_grace_seconds=30,
            heartbeat_interval_seconds=10,
        ),
    ),
)
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...
```

Required/optional/generated 字段边界：

- required: `PublishBudget` 的 6 个字段都必须显式提供。
- optional: `TaskControls.publish_budget` 可以省略。
- conditional: `publish_budget` 只允许配置在带 `WorkspaceSpec(...)` 的 workspace task 上；`read_only=True` 时会被忽略并发出 warning。
- generated: `responseTimeoutSeconds` 来自 `TimeoutPolicy.response_seconds`；如果它小于 `PublishBudget.response_timeout_seconds`，TaskDef 生成会发出 warning。
- forbidden: 不能把 `publish_budget` 配在 workspace-free task 上；不能在 `PublishBudget` 里声明未知字段或 exactly-once 语义。

## 字段语义

| 字段 | 约束 | 说明 |
| --- | --- | --- |
| `observed_merge_p99_seconds` | `>= 0` | 目标 workload 下已观测到的 LakeFS merge 高分位延迟。 |
| `safety_margin_seconds` | `>= 0` | 覆盖观测抖动、网络抖动和小幅数据量增长的安全余量。 |
| `lakefs_merge_timeout_seconds` | `>= 1` | 传给 LakeFS merge SDK request 的 timeout。必须覆盖观测 p99 加安全余量。 |
| `conductor_completion_timeout_seconds` | `>= 1` | Conductor completion 阶段的预算预留。当前 SDK `TaskRunner` owns result update，Perago 不把该值作为 SDK 内部 HTTP request timeout。 |
| `worker_shutdown_grace_seconds` | `>= 1` | publication 后预留给 worker 停止、清理和进程退出的时间。 |
| `heartbeat_interval_seconds` | `>= 1` | 预留给 Conductor response timeout/heartbeat 机制的 slack。 |

`PublishBudget` 是 frozen Pydantic model，并拒绝额外字段。配置错误会在模块导入、`perago check` 或 `perago extract` 阶段暴露为 validation error。

## Response Timeout 计算

`PublishBudget.response_timeout_seconds` 的公式是：

```text
lakefs_merge_timeout_seconds
+ conductor_completion_timeout_seconds
+ worker_shutdown_grace_seconds
+ heartbeat_interval_seconds
```

上面的示例会生成：

```text
45 + 15 + 30 + 10 = 100
```

`perago extract` 写出的 TaskDef 仍然使用 `TimeoutPolicy.response_seconds`：

```json
{
  "responseTimeoutSeconds": 600
}
```

如果同时配置了 `TimeoutPolicy(response_seconds=999)` 和有效 `publish_budget`，TaskDef 会写入 `999`。如果 `TimeoutPolicy.response_seconds` 小于 `PublishBudget.response_timeout_seconds`，TaskDef 生成会发出 warning，但不会用 publish budget 覆盖 task timeout。`PublishBudget` 本身不会写入 TaskDef JSON，也不会出现在 Conductor input/output 中。read-only workspace task 没有 publication 阶段；它的 `publish_budget` 会被忽略，`responseTimeoutSeconds` 使用 `TimeoutPolicy.response_seconds`。

## Runtime 使用位置

`PublishBudget` 同时影响 TaskDef 和 worker runtime：

| 位置 | 使用字段 | 行为 |
| --- | --- | --- |
| TaskDef generation | `response_timeout_seconds` | 只用于 warning：当 `TimeoutPolicy.response_seconds` 小于派生值时提示配置过短。 |
| LakeFS publish | `lakefs_merge_timeout_seconds` | 作为 merge request timeout 传给 LakeFS SDK。 |
| Conductor completion reserve | `conductor_completion_timeout_seconds` | 作为 publication 预算预留；SDK `TaskRunner` 当前 owns completion result update。 |

Perago 当前不直接发送 Conductor completion update，也不接管 SDK 的 `update_task_v2` / `update_task` fallback。`conductor-python 1.3.11` 当前没有公开的 `TaskRunner` completion update HTTP timeout 配置入口；如果后续 SDK 提供正式 public option，再把该字段接到 SDK 公开配置上。

`observed_merge_p99_seconds` 和 `safety_margin_seconds` 不直接传给外部系统。它们只用于校验 `lakefs_merge_timeout_seconds` 是否覆盖 `observed_merge_p99_seconds + safety_margin_seconds`。

## Read-only workspace task

`WorkspaceSpec(read_only=True)` 禁用 workspace publication。此时 `TaskControls(publish_budget=...)` 不参与 TaskDef 生成，也不影响运行时 LakeFS request timeout。`perago check`、`perago extract` 和 `perago start` 应在校验或启动阶段 warning 一次，避免每次 task execution 重复刷日志：

```text
WorkspaceSpec(read_only=True) disables workspace publication; TaskControls.publish_budget is ignored.
```

## 配置流程

1. 在接近真实数据量的 workspace task 上观测 LakeFS merge latency。
2. 选取稳定窗口内的 p99 或更保守分位，填入 `observed_merge_p99_seconds`。
3. 根据网络、object 数量增长和 LakeFS 负载变化选择 `safety_margin_seconds`。
4. 设置 `lakefs_merge_timeout_seconds >= observed_merge_p99_seconds + safety_margin_seconds`。
5. 为 Conductor completion 阶段和 worker shutdown 分别设置明确预算。
6. 运行 `perago check` 验证 task definition，再运行 `perago extract` 检查生成的 `responseTimeoutSeconds`。

避免把 `lakefs_merge_timeout_seconds` 设成远小于观测值的探测性 timeout。publish timeout 或连接错误后，runtime 不能假设 publish 一定没有发生；下一次 retry 按 [LakeFS 发布协议](../lakefs-publication-protocol.md) 检查 target HEAD 状态。

## 常见拒绝场景

merge timeout 小于观测值加安全余量会失败：

```python
PublishBudget(
    observed_merge_p99_seconds=20,
    safety_margin_seconds=10,
    lakefs_merge_timeout_seconds=29,
    conductor_completion_timeout_seconds=15,
    worker_shutdown_grace_seconds=30,
    heartbeat_interval_seconds=10,
)
```

workspace-free task 配置 `publish_budget` 会失败：

```python
@task(
    name="metadata.validate",
    owner_email="data@example.com",
    controls=TaskControls(publish_budget=budget),
)
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    ...
```

额外字段会失败：

```python
PublishBudget(
    observed_merge_p99_seconds=20,
    safety_margin_seconds=10,
    lakefs_merge_timeout_seconds=45,
    conductor_completion_timeout_seconds=15,
    worker_shutdown_grace_seconds=30,
    heartbeat_interval_seconds=10,
    exact_once=True,
)
```

`PublishBudget` 是运维时间预算，不提供 exactly-once publication 证明。Perago MVP 的恢复边界仍然是 attempt fence、publish fence、replacement publication 和 fail closed。
