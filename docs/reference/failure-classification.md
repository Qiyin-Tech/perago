# Failure Classification

本页提供 Perago task attempt 结果状态的精确参考，说明各类失败对应的 Conductor status，以及失败结果是否携带 workspace output。

Perago 的运行时结果只有三个状态：

| 状态 | 载荷结构 | Conductor 语义 |
| --- | --- | --- |
| `COMPLETED` | `output` required; `reasonForIncompletion` forbidden | attempt 成功完成。workspace task 已生成 workspace output；workspace-free task 已返回业务 `result`。 |
| `FAILED` | `reasonForIncompletion` required; `output` forbidden | attempt 执行失败，Perago 认为同一 input 自动重试仍可能有意义。 |
| `FAILED_WITH_TERMINAL_ERROR` | `reasonForIncompletion` required; `output` forbidden | attempt 执行失败，Perago 认为同一 input 自动重试没有意义。 |

`RuntimeTaskResult` 会拒绝不一致的载荷结构：完成状态必须有 `output`，失败状态必须有 `reasonForIncompletion`，失败状态不能携带 `output`。

## Classification Rule

运行时的内部异常 classifier 当前有这些规则：

| 异常类型 | Result status | 说明 |
| --- | --- | --- |
| `PreGuardrailViolation` | `FAILED_WITH_TERMINAL_ERROR` | task body 尚未运行，输入 workspace 未满足 task 的 pre guardrail。 |
| 其他异常 | `FAILED` | 包括 bad input、Pydantic 校验失败、业务异常、post guardrail、attempt fence、publish fence 和 LakeFS 操作失败。 |

Perago 把这个分类扩展为显式 task failure API：

- `raise TaskFailed("...")` 表示可自动恢复、值得重试的执行失败，映射为 `FAILED`。
- `raise TaskTerminalError("...")` 表示同一 input 自动重试没有意义的执行失败，映射为 `FAILED_WITH_TERMINAL_ERROR`。
- 未知、未处理的普通异常仍映射为 `FAILED`，让 Conductor 按 retry policy 处理。

业务可恢复但不应自动重试的情况不使用 Conductor failed 机制。业务函数应返回成功的 `Result Output`，例如 `status="REJECTED"` 或 `status="NEEDS_ACTION"`，再由 WorkflowDef 的分支逻辑处理。

## Workspace Task Attempt

Workspace task 的 attempt 生命周期中，失败分类如下。

| 阶段 | 典型原因 | Result status | 是否发布 workspace output |
| --- | --- | --- | --- |
| input validation | input 顶层字段缺少 `workspace` 或 `params`；`WorkspaceInput` 无效；`params` 额外字段或类型错误 | `FAILED` | 否 |
| download | LakeFS repository/ref 不存在、连接失败、本机 workspace 写入失败 | `FAILED` | 否 |
| pre guardrails | 输入 workspace 缺少必需文件/目录/glob，或命中 forbidden glob | `FAILED_WITH_TERMINAL_ERROR` | 否 |
| task body retryable failure | 用户函数抛出普通异常、`TaskFailed`，或返回值不能通过 output Pydantic model 校验 | `FAILED` | 否 |
| task body terminal failure | 用户函数抛出 `TaskTerminalError`，表示可检测且不可重试的执行前提错误 | `FAILED_WITH_TERMINAL_ERROR` | 否 |
| task body business branch | 用户函数成功判定业务无法继续自动执行，但 workflow 可处理该分支 | `COMPLETED` | 视 workspace access mode 而定 |
| post guardrails | 输出 workspace 文件未通过 task 的 post guardrail | `FAILED` | 否 |
| read-only completion | `WorkspaceSpec(read_only=True)` 的 task 成功完成 | `COMPLETED` | 否，output ref 保持 input ref |
| no-op writable completion | `read_only=False` 且 workspace diff 为空，target HEAD 状态可解释 | `COMPLETED` | 否，output ref 保持 input ref |
| first attempt fence | task body 后、stage 前发现 Conductor attempt 已不再是当前 attempt | `FAILED` | 否 |
| stage | workspace 含 symlink、上传/删除/commit 失败 | `FAILED` | 否 |
| second attempt fence | stage 后、publish 前发现 attempt 已失效 | `FAILED` | 否 |
| publish fence / merge | target branch 被无关提交推进、merge 失败或 merge timeout | `FAILED` | 否 |
| staging cleanup | staging branch 删除失败 | 保留原始 result | 保留原始 result |
| local cleanup | attempt-local workspace 删除失败 | 保留原始 result | 保留原始 result |

Workspace task 在 body 成功、post guardrail 通过后，按 workspace access mode 和 diff 决定完成路径。`read_only=True` 不进入 LakeFS HEAD 检查或 publication；`read_only=False` 且 diff 为空时 Perago 不会创建 empty commit，但必须通过 no-op HEAD 状态检查；`read_only=False` 且 diff 非空时必须完成 stage 和 publish。完成 output 会包含 `workspace` 和 `result`；失败 output 不会带 workspace，也不会把未发布的 attempt-local workspace 暴露给下游。

## Workspace-Free Task Attempt

Workspace-free task 不下载、不发布 workspace，也没有 guardrail 或 publish fence。

| 阶段 | 典型原因 | Result status |
| --- | --- | --- |
| input validation | input 顶层字段缺少 `params`；`params` 额外字段或类型错误 | `FAILED` |
| task body retryable failure | 用户函数抛出普通异常或 `TaskFailed` | `FAILED` |
| task body terminal failure | 用户函数抛出 `TaskTerminalError` | `FAILED_WITH_TERMINAL_ERROR` |
| task body business branch | 用户函数成功判定业务无法继续自动执行，但 workflow 可处理该分支 | `COMPLETED` |
| result validation | 返回值不能通过 output Pydantic model 校验 | `FAILED` |
| success | 业务函数返回值通过 output model 校验 | `COMPLETED` |

Workspace-free task 的 `COMPLETED` output 只包含 `result`。它不会生成 `workspace` output，也不能通过 `TaskControls.publish_budget` 配置发布预算。

## Conductor Result Payload

Perago 内部先构造 `RuntimeTaskResult`，再转换成 Conductor SDK 的 `TaskResult`。

`COMPLETED` 会写入：

```json
{
  "status": "COMPLETED",
  "output": {
    "result": {
      "valid": true
    }
  }
}
```

`FAILED` 会写入：

```json
{
  "status": "FAILED",
  "reasonForIncompletion": "workspace task input must contain only workspace and params"
}
```

`FAILED_WITH_TERMINAL_ERROR` 会写入：

```json
{
  "status": "FAILED_WITH_TERMINAL_ERROR",
  "reasonForIncompletion": "pre guardrail require_glob('raw/**/*.parquet') matched 0 files; min_count=1"
}
```

`worker_id` 不属于 `RuntimeTaskResult` payload。worker 向 Conductor 回写结果时会把 `worker_id` 写入 SDK `TaskResult` 字段，用于从 Conductor attempt 反查本机 worker 日志目录。

## Recovery Boundary

Perago 的失败分类是 fail-closed 的：

- publish fence、stale attempt 和 LakeFS merge 错误不会被包装成成功。
- 业务可恢复分支不会被包装成 Conductor failure；它们应作为成功的 `Result Output` 交给 workflow 分支处理。
- staging cleanup 和 local cleanup 的错误只写日志，不覆盖原始 task result。
- publish timeout 或 result update 失败后，不应直接假设 publish 没发生；下一次 retry 按 [LakeFS 发布协议](../lakefs-publication-protocol.md) 检查 target HEAD 状态。

MVP 不提供严格 exactly-once publication 证明。Reference 中的失败分类只描述 worker 如何回写当前 attempt result；跨 attempt 的发布判断来自 Conductor attempt fence 和 LakeFS HEAD 状态。
