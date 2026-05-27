# Publish Fences

Perago 的 publish fence 解决的是可写 workspace task 在发布或 no-op branch 校正前能不能继续推进 target branch 的问题。正式操作协议见 [LakeFS 发布协议](../lakefs-publication-protocol.md)。

## Fence 层次

Perago 有两类 fence：

| Fence | 判断对象 | 运行位置 | 失败结果 |
| --- | --- | --- | --- |
| Attempt fence | Conductor 当前 attempt snapshot | task body 后、stage 前；stage 后、publish 前 | `FAILED` |
| Publish fence | LakeFS target branch HEAD 状态 | staging branch publish 前；可写 no-op completion 前 | `FAILED` |

Attempt fence 看 Conductor 侧的 task 权限。Publish fence 看 LakeFS 侧的 HEAD 状态。任一侧不满足，runtime 都不会生成 workspace output。

## Attempt Fence

Attempt fence 由 `assert_current_attempt_snapshot(...)` 执行。fresh attempt 必须仍然满足：

| 字段 | 要求 |
| --- | --- |
| `status` | 必须是 `IN_PROGRESS`。 |
| `workflow_instance_id` | 必须等于 worker 已 poll 到的 attempt。 |
| `task_id` | 必须等于 worker 已 poll 到的 attempt。 |
| `retry_count` | 必须等于 worker 已 poll 到的 attempt。 |

可写 workspace task 在两个位置检查它：

1. task body 和 post Workspace Check 成功之后、stage workspace 或 no-op branch relocation 之前。
2. stage workspace 成功之后、publish workspace 之前。

第一次检查避免已经失效的 attempt 上传本机 workspace；第二次检查覆盖 stage 耗时窗口，避免 stage 成功后已经失效的 attempt 推进目标 branch。失败会抛出 `StaleAttemptError`，最终映射为普通 `FAILED`。

Attempt fence 是 client-side 检查。它依赖 Conductor 当前 attempt 查询结果，不能替代 Conductor lease、heartbeat 和 TaskDef timeout 配置。

## Publish Fence

Publish fence 只接受两种 LakeFS target branch 状态：

| Target branch 状态 | 行为 |
| --- | --- |
| current head 等于 input `workspace.ref` | diff 非空时允许 merge staging branch；diff 为空时允许 no-op completion。 |
| current head 的直接 parent 等于 input `workspace.ref` | diff 非空时允许 replacement publish，把 target branch hard-reset / relocate 到本次 staged commit；diff 为空时允许 relocate 回 input ref。 |

其他状态都会触发 `PublishFenceError`。这表示 target branch 已经进入当前 attempt 无法安全解释的状态；runtime fail closed，不发布 workspace output。

`WorkspaceSpec(read_only=True)` 不进入 publish fence。read-only workspace task 的成功 output ref 保持 input ref，Conductor result 接受与幂等性由 Conductor 负责。

不需要任何 commit metadata。`input_ref` 来自 Conductor input；retry 是否有权限来自 Conductor attempt fence；LakeFS 提供当前 HEAD 状态。

## Operational Soft Fence

当前 publish fence 是 operational soft fence：

| 能做到 | 做不到 |
| --- | --- |
| 发布前发现 target branch 不在允许状态。 | 在 LakeFS 服务端原子声明 expected destination head。 |
| 允许 retry 覆盖一个 abandoned publication，并保持可见历史为 `input_ref -> staged_commit`；可写 no-op retry 可把 abandoned publication relocate 回 `input_ref`。 | 证明 worker 崩溃窗口内不会发生重复 publication。 |
| 对无法解释的 branch advancement fail closed。 | 在 Conductor completion 与 LakeFS publish 之间提供 XA 事务。 |

最重要的崩溃窗口是 LakeFS publish 已成功、但 worker 在 Conductor completion 前死亡。后续 retry 从 Conductor 角度仍然可能有效；如果 target branch 当前 head 是 input ref 的直接子提交，runtime 可以用 replacement publish 把可见历史替换为本次 staged commit。若后续 retry 是可写 no-op completion，则 runtime 可以把 target branch relocate 回 input ref，避免把 abandoned publication 包装成本次成功 output。

如果 target branch 状态不满足 `HEAD == input_ref` 或 `parent(HEAD) == input_ref`，runtime 必须 fail closed。恢复方式是从当前 target branch head 发起新的 workflow，或由运维按 LakeFS 侧事实处理。

## Hard Fence 候选

Hard fence 只有在 LakeFS 侧能把 expected destination head 判断和 branch 更新放进同一个服务端决策时才成立。MVP 没有把它作为依赖。

| 候选 | 需要证明的语义 | MVP 状态 |
| --- | --- | --- |
| LakeFS server-side compare-and-swap update | publish 时原子验证 target head 仍是 runtime 读到的值。 | 当前不依赖。 |
| Perago 外部 transaction ledger | ledger 持久记录 attempt、publish decision 和 result reconciliation。 | 超出 MVP；只有 strict exactly-once 成为需求时再考虑。 |

Hard fence 即使成立，也只解决 target branch head 的原子判断问题。Conductor result update、worker cleanup 和业务函数重跑仍需要单独的恢复模型。

## 运维约束

Soft fence 依赖以下约束保持可解释：

- workspace 写入 workflow 必须保持串行，不允许并行分支写同一个 LakeFS target branch。
- 同一时间只应有一个活跃 workflow instance 写入给定 workspace branch。
- human、TS、Python 节点都必须通过同一套 Conductor 权限边界。
- `PublishBudget` 必须覆盖真实 LakeFS publish 观测值、Conductor completion budget reserve、heartbeat 和 shutdown grace。
- workflow 结束后的 LakeFS GC 属于运维清理，不属于 task publish protocol。

这些约束被破坏时，Perago 的正确行为是失败并保留排查证据；不要把不确定状态包装成成功。
