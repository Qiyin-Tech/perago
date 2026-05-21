# Workspace Transaction Model

Perago 的 workspace transaction 是一个由 runtime 执行的 TCC-inspired 发布模型。它把任务函数限制在 attempt-local workspace 内，让 worker runtime 负责 LakeFS staging、发布 fence、目标 branch merge、Conductor result 和清理。

这个模型来自 ADR-0001，目的是在不要求任务作者实现事务回调、不依赖 LakeFS Enterprise-only 功能、也不引入外部事务协调器的前提下，让 workspace task 的发布边界可解释、可重试、可排查。

## 设计目标

Perago 的事务模型解决的是 workspace task attempt 的发布安全问题，而不是所有分布式一致性问题：

| 目标 | Perago 的做法 |
| --- | --- |
| 任务函数保持简单 | task body 只接收本机 `Path` 和 typed params，返回 typed result。 |
| 未确认写入不污染目标 branch | 所有写入先进入 execution-scoped staging branch。 |
| stale attempt 不应继续发布 | runtime 在 stage 前和 publish 前各执行一次 attempt fence。 |
| branch advancement 可分类 | confirm commit 写入 Perago metadata，publish fence 只接受已知安全状态。 |
| 不确定状态 fail closed | 无法用 Conductor attempt 状态和 LakeFS metadata 分类时，让当前 attempt 失败。 |

Perago 不把 workspace transaction 暴露成用户 API。任务作者不需要写 `try`、`confirm` 或 `cancel` 函数，也不需要在业务代码里直接操作 staging branch。

runtime 的 staged reference 必须完整携带 LakeFS repository、staging branch 和 staging commit。Cancel/cleanup、Confirm/publish 和 retry 判断都应由显式 staged reference 与 workspace input 驱动，不能依赖 worker-local mutable state 来补齐 repository identity。

## TCC 映射

Perago 借用 TCC 的阶段名称，但阶段由 runtime 完成：

| 阶段 | Runtime 行为 | 成功产物 |
| --- | --- | --- |
| Try | 从 input `workspace.ref` 创建本次 execution 专属 staging branch，把本机 workspace 的 `WorkspaceSpec.prefix` 投影同步进去，并提交 staging commit。 | 带 `perago.phase=try` metadata 的 staging commit。 |
| Confirm | 通过 attempt fence 和 publish fence 后，把 staging branch squash merge 到目标 branch。 | 带 `perago.phase=confirm` metadata 的目标 branch commit。 |
| Cancel | attempt 失败、stale 或完成后清理 staging branch 和本机 attempt-local workspace。 | 清理日志；不会回滚已成功 merge 的目标 branch。 |

这不是 XA、AT 或 Saga：

| 模型 | 为什么不是 Perago MVP 的主模型 |
| --- | --- |
| XA | Conductor task completion 与 LakeFS branch merge 不属于同一个 XA resource manager。 |
| AT | Perago 没有透明代理业务写入，也没有可自动回滚 LakeFS commit 的 undo log。 |
| Saga | Saga 要求业务补偿或恢复动作，和 Perago 让 task body 保持普通 typed Python 函数的目标冲突。 |

## Attempt fence

Attempt fence 防止已经不是当前 in-progress attempt 的 worker 继续推进 workspace。Perago 在两个位置检查它：

1. task body 和 post guardrails 成功之后、stage 之前。
2. stage 成功之后、publish 之前。

fresh attempt 必须仍然匹配已 poll 到的 Conductor attempt：`status` 是 `IN_PROGRESS`，`workflow_instance_id`、`task_id` 和 `retry_count` 都没有变化。任一条件失败，attempt 返回普通 `FAILED`，runtime 继续尝试清理 staging branch 和本机 workspace。

双 fence 的原因是 stage 本身可能耗时。第一次 fence 避免失效 attempt 上传 staging workspace；第二次 fence 避免 stage 完成后已经失效的 attempt 继续 merge 目标 branch。

## Publish fence

Publish fence 判断目标 branch 当前 head 是否仍可被本次 attempt 推进。MVP 接受两种状态：

| 目标 branch 状态 | 行为 |
| --- | --- |
| current head 等于 input `workspace.ref` | 以 input ref 作为 publish base，允许发布。 |
| current head 是同一个 `perago.logical_task_key` 之前发布的 confirm commit | 允许继续发布，并在 metadata 里记录 `perago.supersedes`。 |

其他 branch advancement 都会触发 `PublishFenceError`。这通常表示目标 branch 被其他 workflow step、其他 workflow instance 或非 Perago 写入推进；runtime 不发布 workspace output，让 Conductor 按普通失败路径处理。

runtime 会沿 first-parent history 从 current head 回溯到 input `workspace.ref`，但扫描最多 1024 个 commits。超过这个范围，或 history 已经不包含 input ref，都会被视为无法分类的 branch advancement 并 fail closed。

这个 publish fence 是 client-side soft fence。它在 merge 前读取和判断目标 branch head，但不是 LakeFS server-side compare-and-swap，因此不能证明严格 exactly-once publication。

## Metadata

Confirm commit metadata 是 retry 分类和人工排查的事实来源：

| Metadata | 用途 |
| --- | --- |
| `perago.phase` | 区分 try commit 与 confirm commit。 |
| `perago.logical_task_key` | 标识同一个 workflow step，retry attempt 共享这个 key。 |
| `perago.task_id` | 标识当前 Conductor attempt。 |
| `perago.retry_count` | 辅助定位 retry attempt。 |
| `perago.input_ref` | 记录本轮 attempt 读取的不可变输入 ref。 |
| `perago.target_branch` | 记录被推进的 LakeFS branch。 |
| `perago.prefix` | 记录 task 声明的 workspace prefix。 |
| `perago.staging_branch` | 记录被 merge 的 staging branch。 |
| `perago.staging_commit` | 记录被 merge 的 staging commit。 |
| `perago.expected_head` | 记录 publish fence 选择的发布基准。 |
| `perago.supersedes` | 记录同一 logical task 之前推进过的 commit；没有则为空字符串。 |

如果 worker 在 LakeFS merge 成功后、Conductor completion 前死亡，Perago 不从 LakeFS metadata 恢复旧 workflow 状态，也不补发 Conductor completion。Conductor 会按自己的 timeout/fail/retry 语义处理；后续 execution 使用新的 staging branch。publish fence 仍会用 metadata 区分“同一 logical task 的前一次发布”和“无关 branch advancement”，但这不是 workflow recovery。

## 故障与恢复边界

Perago 的 MVP 事务边界是 operationally bounded，而不是 exactly-once 证明：

| 场景 | 行为 |
| --- | --- |
| pre guardrail 失败 | 返回 `FAILED_WITH_TERMINAL_ERROR`，不运行 task body，不发布 workspace。 |
| task body、post guardrail、download、stage 失败 | 返回普通 `FAILED`，不发布 workspace。 |
| attempt fence 失败 | 返回普通 `FAILED`，尝试清理 staging 和本机 workspace。 |
| publish fence 或 merge 失败 | 返回普通 `FAILED`，不发布 workspace output。 |
| cleanup 失败 | 保留原始 result，只写日志。 |
| merge 已成功但 worker 死亡 | 不补发旧 completion；由 Conductor timeout/fail/retry，后续 execution 用 publish fence 分类目标 branch 状态。 |

Fail closed 的恢复方式不是在原 attempt 内猜测补偿动作，也不是从 LakeFS 反推 Conductor 状态，而是让 Conductor 按失败或重试策略推进；需要人工恢复时，从当前 protected branch head 发起新的工作流。

## 运行时假设

这个模型依赖以下运营约束：

- workspace 写入 workflow 在定义上保持串行，不允许并行分支同时写同一个 LakeFS target branch。
- 同一时间只有一个活跃 workflow instance 写入给定 workspace branch。
- target branch 应由 LakeFS branch protection 保护，workspace 更新只通过 runtime merge 进入。
- `PublishBudget` 应使用真实 LakeFS merge 观测值设置 timeout、heartbeat 和 shutdown grace，而不是作为 changed-object quota。
- LakeFS Community hook 可以作为未来 hard fence 候选，但必须先用部署版本的集成测试证明其语义；当前 runtime 不依赖它，也不把 LakeFS/Conductor 做成跨系统事务。

更细的执行顺序见 [Workspace Publication](../runtime/workspace-publication.md)，LakeFS object 同步规则见 [LakeFS Runtime](../runtime/lakefs.md)。
