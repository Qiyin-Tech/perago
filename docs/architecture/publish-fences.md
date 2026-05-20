# Publish Fences

Perago 的 fence 设计解决的是 workspace task 发布前后的可分类性问题。它不把 LakeFS merge 和 Conductor completion 变成一个原子事务，也不证明 exactly-once publication；它让 stale attempt、无关 branch advancement 和同一 logical task retry 能在 runtime 里被明确区分。

这页解释为什么 MVP 使用 client-side soft fence、当前能保证什么、不能保证什么，以及未来 hard fence 需要满足的条件。执行顺序见 [Workspace Publication](../runtime/workspace-publication.md)，事务阶段见 [Workspace Transaction Model](transaction-model.md)。

## Fence 层次

Perago 有两类 fence：

| Fence | 判断对象 | 运行位置 | 失败结果 |
| --- | --- | --- | --- |
| Attempt fence | Conductor 当前 attempt snapshot | task body 后、stage 前；stage 后、publish 前 | `FAILED` |
| Publish fence | LakeFS target branch head 和 commit metadata | staging branch publish 前 | `FAILED` |

Attempt fence 防止旧 attempt 继续写 staging 或发布。Publish fence 防止当前 attempt 在无法解释的 target branch advancement 上继续 merge。

这两者是互补关系：attempt fence 看 Conductor 侧的 task lease 和 retry 身份，publish fence 看 LakeFS 侧的 branch history。任一侧不满足，runtime 都不会生成 workspace output。

## Attempt fence

Attempt fence 由 `assert_current_attempt_snapshot(...)` 执行。fresh attempt 必须仍然满足：

| 字段 | 要求 |
| --- | --- |
| `status` | 必须是 `IN_PROGRESS`。 |
| `workflow_instance_id` | 必须等于 worker 已 poll 到的 attempt。 |
| `task_id` | 必须等于 worker 已 poll 到的 attempt。 |
| `retry_count` | 必须等于 worker 已 poll 到的 attempt。 |

Perago 在两个位置检查它：

1. task body 和 post Workspace Check 成功之后、stage workspace 之前。
2. stage workspace 成功之后、publish workspace 之前。

第一次检查避免已经失效的 attempt 上传本机 workspace；第二次检查覆盖 stage 耗时窗口，避免 stage 成功后已经失效的 attempt 推进目标 branch。失败会抛出 `StaleAttemptError`，最终映射为普通 `FAILED`。

Attempt fence 是 client-side 检查。它依赖 Conductor 当前 attempt 查询结果，不能替代 Conductor lease、heartbeat 和 TaskDef timeout 配置。

## Publish fence

Publish fence 由 `choose_publish_base(...)` 执行。它比较 input `workspace.ref`、target branch 当前 head，以及从 input ref 到 current head 的 commit metadata。

MVP 接受两种状态：

| Target branch 状态 | 行为 | `perago.supersedes` |
| --- | --- | --- |
| current head 等于 input `workspace.ref` | 允许以 input ref 作为 publish base。 | 空字符串 |
| current head 只包含同一 `perago.logical_task_key` 的 confirm commits | 允许继续发布，并把 current head 记录为被覆盖的同一 logical task commit。 | current head |

其他状态都会触发 `PublishFenceError`，错误文本形如 `<branch> advanced from <input-ref> to <current-head>`。这表示 target branch 已经被其他 workflow step、其他 workflow instance、人工写入或 metadata 不完整的 commit 推进；runtime fail closed，不发布 workspace output。

`perago.logical_task_key` 不包含 Conductor `task_id`，因此同一个 workflow step 的 retry attempts 会共享它。`perago.task_id` 和 `perago.staging_commit` 仍会写入 confirm metadata，用于排查单次 attempt 或识别已经发生的 publish。

## Soft Fence 边界

当前 publish fence 是 client-side soft fence：

| 能做到 | 做不到 |
| --- | --- |
| merge 前发现 target branch 已经不是可解释状态。 | 在 LakeFS 服务端原子地声明 expected destination head。 |
| 将同一 logical task 的前序 confirm commit 分类为可 supersede。 | 证明 worker 崩溃窗口内不会发生重复 publication。 |
| 对 metadata 不完整或无关 advancement fail closed。 | 在 Conductor completion 与 LakeFS merge 之间提供 XA 事务。 |
| 用 commit metadata 支持人工排查和后续 retry 分类。 | 自动补偿或回滚已经成功 merge 的 target branch commit。 |

最重要的崩溃窗口是 LakeFS merge 已成功、但 worker 在 Conductor completion 前死亡。后续 retry 可能重新执行 task body。Perago 的 MVP 允许后续 retry 发布一个新的线性 commit，并通过 `perago.supersedes` 记录它覆盖了同一 logical task 的前序 commit。

如果 runtime 无法从 metadata 判定 current head 是否属于同一 logical task，它必须 fail closed。恢复方式是从当前 protected branch head 发起新的 workflow，而不是继续重试同一个不确定 attempt。

## Hard Fence 候选

Hard fence 只有在 LakeFS 侧能把 expected destination head 判断和 merge 放进同一个服务端决策时才成立。MVP 没有把它作为依赖。

| 候选 | 需要证明的语义 | MVP 状态 |
| --- | --- | --- |
| LakeFS Community pre-merge hook/action | hook 能读取 expected head，且能作为快速 gate 拒绝 head 不匹配的 merge；不能变成长时间锁等待。 | 可作为未来选项，需按部署版本集成测试。 |
| LakeFS server-side compare-and-swap merge | merge API 原生支持 expected destination head，并在 head 改变时原子失败。 | 当前 Python SDK 边界未采用。 |
| Perago 外部 transaction ledger | ledger 持久记录 attempt、staging commit、publish decision 和 result reconciliation。 | 超出 MVP；只有 strict exactly-once 成为需求时再考虑。 |

Hard fence 即使成立，也只解决 target branch head 的原子判断问题。Conductor result update、worker cleanup 和业务函数重跑仍需要单独的恢复模型。

## 运维约束

Soft fence 依赖以下约束保持可解释：

- workspace 写入 workflow 必须保持串行，不允许并行分支写同一个 LakeFS target branch。
- 同一时间只应有一个活跃 workflow instance 写入给定 workspace branch。
- target branch 应配置为 protected branch，并约束 workspace 更新只通过 Perago runtime merge 进入。
- `PublishBudget` 必须覆盖真实 merge 观测值、Conductor completion timeout、heartbeat 和 shutdown grace。
- merge timeout 或连接错误后，先按 `perago.logical_task_key`、`perago.task_id` 和 `perago.staging_commit` 检查 commit metadata，再决定是否从当前 branch head 发起新 workflow。

这些约束被破坏时，Perago 的正确行为是失败并保留排查证据，而不是把不确定状态包装成成功。
