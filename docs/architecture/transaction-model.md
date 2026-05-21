# Workspace Transaction Model

Perago 的 workspace transaction 是一个由 runtime 执行的 TCC-inspired 发布模型。它把任务函数限制在 attempt-local workspace 内，让 worker runtime 负责 LakeFS staging、发布 fence、目标 branch 更新、Conductor result 和清理。

正式 LakeFS 操作步骤见 [LakeFS 发布协议](../lakefs-publication-protocol.md)。本页说明该模型提供的保证以及不涵盖的范围。

## 设计目标

Perago 的事务模型解决 workspace task attempt 的发布安全问题：

| 目标 | Perago 的做法 |
| --- | --- |
| 任务函数保持简单 | task body 只接收本机 `Path` 和 typed params，返回 typed result。 |
| 未确认写入不直接污染目标 branch | 所有写入先进入 execution-scoped staging branch。 |
| stale attempt 不应继续发布 | runtime 在 stage 前和 publish 前各执行一次 attempt fence。 |
| retry 可以处理一个 abandoned publication | 当 target head 是 input ref 的直接子提交时，runtime 使用 replacement publication。 |
| 不确定状态 fail closed | 无法用 Conductor attempt 状态和 LakeFS HEAD 状态解释时，让当前 attempt 失败。 |

Perago 不把 workspace transaction 暴露成用户 API。任务作者不需要写 `try`、`confirm` 或 `cancel` 函数，也不需要在业务代码里直接操作 staging branch。

runtime 的 staged reference 必须完整携带 LakeFS repository、staging branch 和 staging commit。Cancel/cleanup、Confirm/publish 和 retry 判断都应由显式 staged reference 与 workspace input 驱动，不能依赖 worker-local mutable state 来补齐 repository identity。

## TCC 映射

Perago 借用 TCC 的阶段名称，但阶段由 runtime 完成：

| 阶段 | Runtime 行为 | 成功产物 |
| --- | --- | --- |
| Try | 从 input `workspace.ref` 创建本次 execution 专属 staging branch，把本机 workspace 的 `WorkspaceSpec.prefix` 投影同步进去，并提交 staging commit。 | staging commit。 |
| Confirm | 通过 attempt fence 和 publish fence 后，按协议 merge staging branch 或 hard-reset / relocate target branch 到 staged commit。 | target branch 上的 published workspace ref。 |
| Cancel | attempt 失败、stale 或完成后清理 staging branch 和本机 attempt-local workspace。 | 清理日志；不会回滚已成功 publish 的目标 branch。 |

其他事务模型的取舍：

| 模型 | 取舍 |
| --- | --- |
| XA | Conductor task completion 与 LakeFS branch 更新不属于同一个 XA resource manager。 |
| AT | Perago 没有透明代理业务写入，也没有可自动回滚 LakeFS commit 的 undo log。 |
| Saga | Saga 要求业务补偿或恢复动作，和 Perago 让 task body 保持普通 typed Python 函数的目标冲突。 |

## Attempt Fence

Attempt fence 防止失效 worker 继续推进 workspace。Perago 在两个位置检查它：

1. task body 和 post guardrails 成功之后、stage 之前。
2. stage 成功之后、publish 之前。

fresh attempt 必须仍然匹配已 poll 到的 Conductor attempt：`status` 是 `IN_PROGRESS`，`workflow_instance_id`、`task_id` 和 `retry_count` 都没有变化。任一条件失败，attempt 返回普通 `FAILED`，runtime 继续尝试清理 staging branch 和本机 workspace。

双 fence 的原因是 stage 本身可能耗时。第一次 fence 避免失效 attempt 上传 staging workspace；第二次 fence 避免 stage 完成后已经失效的 attempt 继续更新目标 branch。

## Publish Fence

Publish fence 判断目标 branch 当前 head 是否仍可被本次 attempt 推进。MVP 接受两种状态：

| 目标 branch 状态 | 行为 |
| --- | --- |
| current head 等于 input `workspace.ref` | merge staging branch，允许首次发布。 |
| current head 的直接 parent 等于 input `workspace.ref` | 视为 abandoned publication，使用 replacement publication。 |

其他 branch advancement 都会触发 `PublishFenceError`。这通常表示目标 branch 被其他 workflow step、其他 workflow instance、人工写入或更复杂历史推进；runtime 不发布 workspace output，让 Conductor 按普通失败路径处理。

这个 publish fence 是 client-side soft fence。它在 publish 前读取和判断目标 branch head；它不提供 LakeFS server-side compare-and-swap，也不证明严格 exactly-once publication。

## Metadata

不需要任何 commit metadata。

commit message 可以用于人工排查。发布判断只看 Conductor attempt fence 和 HEAD 状态。

## 故障与恢复边界

Perago 的 MVP 事务边界是 operationally bounded：

| 场景 | 行为 |
| --- | --- |
| pre guardrail 失败 | 返回 `FAILED_WITH_TERMINAL_ERROR`，不运行 task body，不发布 workspace。 |
| task body、post guardrail、download、stage 失败 | 返回普通 `FAILED`，不发布 workspace。 |
| attempt fence 失败 | 返回普通 `FAILED`，尝试清理 staging 和本机 workspace。 |
| publish fence 或 publish 失败 | 返回普通 `FAILED`，不发布 workspace output。 |
| cleanup 失败 | 保留原始 result，只写日志。 |
| publish 已成功但 worker 死亡 | 不补发旧 completion；由 Conductor timeout/fail/retry，后续 execution 按 LakeFS HEAD 状态决定是否 replacement publish。 |

Fail closed 后，让 Conductor 按失败或重试策略推进；需要人工恢复时，从当前 target branch head 发起新的工作流。

## 运行时假设

这个模型依赖以下运营约束：

- workspace 写入 workflow 在定义上保持串行，不允许并行分支同时写同一个 LakeFS target branch。
- 同一时间只有一个活跃 workflow instance 写入给定 workspace branch。
- human、TS、Python 节点都必须遵守同一套 Conductor 权限边界。
- runtime 必须具备执行 target branch merge 和 replacement publish 所需的 LakeFS 权限。
- `PublishBudget` 应使用真实 LakeFS publish 观测值设置 timeout、heartbeat 和 shutdown grace。

更细的执行顺序见 [Workspace Publication](../runtime/workspace-publication.md)，LakeFS object 同步规则见 [LakeFS Runtime](../runtime/lakefs.md)。
