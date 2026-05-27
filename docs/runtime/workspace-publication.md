# Workspace Publication

Workspace completion 是 Perago 对一次 workspace task attempt 的运行时边界。任务函数只读写本机 attempt-local workspace；下载、guardrail、按需 stage、publish fence、LakeFS publish、Conductor output 和 cleanup 都由 runtime 包起来执行。

正式 LakeFS 操作协议见 [LakeFS 发布协议](../lakefs-publication-protocol.md)。本文档按 attempt 生命周期说明 runtime 如何执行 read-only、no-op 和 publication 路径。LakeFS object 同步细节见 `lakefs.md`，Conductor poll/result 细节见 `conductor.md`。

## 生命周期

一次 workspace task attempt 的公共顺序是：

1. 校验 Conductor input 顶层只有 `workspace` 和 `params`。
2. 从 `PERAGO_WORKSPACE_ROOT` 创建 execution-local attempt workspace，并写入 `.perago-attempt.json` marker。
3. 从 input `workspace.ref` 下载 `WorkspaceSpec.prefix` 下的 LakeFS object。
4. 执行 pre guardrails。
5. 调用 task body。
6. 校验 result 模型并执行 post guardrails。
7. 根据 `WorkspaceSpec.read_only` 和 workspace diff 选择 read-only、no-op 或 publication 路径。
8. 生成 `COMPLETED` output。
9. 尝试清理已创建的 staging branch。
10. 尝试清理 attempt-local workspace。

成功 output 会保留 input workspace 的 repository、branch 和 `ref_type`。`ref` 可能保持 input ref，也可能改成发布后的 LakeFS commit：

```json
{
  "workspace": {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "published-commit"
  },
  "result": {
    "row_count": 100
  }
}
```

## Workspace access mode

`WorkspaceSpec(read_only=True)` 声明 read-only workspace task。它下载 workspace 并执行 task body，但不检查 workspace diff、不读取 target branch HEAD、不创建 staging branch、不提交 LakeFS commit，也不发布新 ref。成功 output 的 `workspace.ref` 等于 input `workspace.ref`。

Read-only completion 没有 LakeFS 写入或 branch relocation，因此不调用 Perago 的 attempt fence。最终 `TaskResult` 仍按 Conductor worker 的普通 completion contract 回写；旧 `task_id`、已 terminal task 或 retry 后的新 attempt 是否接受 completion，由 Conductor 服务端的 task update 语义处理。

`read_only=True` 不是 OS-level readonly mount。如果业务函数写了本机 attempt workspace，这些写入不会发布，会随 attempt-local cleanup 丢弃。

默认 `read_only=False`。可写 workspace task 执行完 task body 和 post guardrails 后，runtime 根据 workspace diff 决定：

| 状态 | 行为 | Output ref |
| --- | --- | --- |
| diff 为空，`HEAD == input_ref` | 不创建 staging branch；Perago 不会创建 empty commit，直接完成。 | `input_ref` |
| diff 为空，`parent(HEAD) == input_ref` | 把 `HEAD` 视为 abandoned publication，将 target branch relocate 回 `input_ref` 后完成。 | `input_ref` |
| diff 为空，其他 HEAD 状态 | fail closed。 | 不生成 output |
| diff 非空 | 进入 Try/Confirm publication 路径。 | published ref |

Perago 不会创建 LakeFS empty commit。节点是否执行成功属于 Conductor result 和 worker 日志；LakeFS commit 只表达 workspace 内容变化。

## Try

Perago 的 try 阶段只适用于 `read_only=False` 且 workspace diff 非空的 attempt，由 runtime 对 workspace 执行 staging 操作：

| 动作 | 说明 |
| --- | --- |
| create staging branch | 从 input `workspace.ref` 创建 execution-scoped staging branch；已有同名 branch 时 fail closed。 |
| sync prefix | 把本机 workspace 中 `WorkspaceSpec.prefix` 的完整投影同步到 staging branch。 |
| commit staging | 提交 staging branch，得到本次 attempt 的 staged commit。 |

staging branch 名由 Conductor attempt 字段和本次 execution id 生成，包含 workflow、reference task、sequence、iteration、task id、retry count 和 execution id。它是内部 runtime 状态，不进入 Conductor input/output，也不要求任务作者手动管理。runtime 内部的 staged reference 必须同时携带 repository、branch 和 commit；cleanup 由这个显式引用驱动，不能依赖 executor-local 状态。

## Confirm

confirm 阶段把 staged commit 发布到目标 branch。不需要任何 commit metadata。发布权限来自 Conductor attempt fence 和 HEAD 状态。

runtime 先读取目标 branch 当前 head：

| Target branch state | Runtime action | Visible history |
| --- | --- | --- |
| `HEAD == workspace.ref` | 将 staging branch merge 到 `workspace.branch`。 | `input_ref -> published` |
| `parent(HEAD) == workspace.ref` | 将 `HEAD` 视为 abandoned publication，并 hard-reset / relocate `workspace.branch` 到本次 staged commit。 | `input_ref -> staged_commit` |
| 其他状态 | 抛出 `PublishFenceError`，不发布 workspace output。 | 无变化 |

replacement publish 后，目标 branch 的可见历史必须是 `input_ref -> staged_commit`，不能把 abandoned commit 作为新 commit 的 parent。

No-op writable completion 也要保持 output ref 与 target branch 可见 head 一致。如果 target head 是 input ref 的直接子提交，runtime 不把 abandoned commit 包装成本次成功 output，而是把 target branch relocate 回 input ref。

## Attempt Fence

Perago 在可能执行 workspace publication 或 no-op branch relocation 的可写路径上检查 attempt fence：

| 位置 | 目的 |
| --- | --- |
| task body 和 post guardrails 之后、stage 或 no-op branch relocation 之前 | 避免已经失效的 attempt 上传、提交 staging workspace 或回拨 target branch。 |
| stage 之后、publish 之前 | 避免已经失效的 attempt 推进目标 branch。 |

fresh attempt 必须仍满足：

- `status == "IN_PROGRESS"`。
- `workflow_instance_id` 与已 poll 到的 attempt 一致。
- `task_id` 与已 poll 到的 attempt 一致。
- `retry_count` 与已 poll 到的 attempt 一致。

任一检查失败都会变成普通 `FAILED` result。若 staging 已经创建，runtime 会先尝试清理 staging branch；本机 attempt-local workspace 也会被清理。

## Publish Fence

publish fence 决定当前 attempt 是否还能推进或校正目标 branch。它只检查当前 HEAD 与 input ref 的关系：

- `HEAD == input_ref` 表示首次发布，或 no-op writable completion 可直接完成。
- `parent(HEAD) == input_ref` 表示当前 head 可被视为 abandoned publication；diff 非空时可用 replacement publish 覆盖，diff 为空时可 relocate 回 input ref。
- 其他状态都 fail closed。

这个 fence 是 operational soft fence。runtime 会在进入目标 branch 操作前重新确认 Conductor attempt 仍然有效，但不要求 LakeFS 提供 server-side compare-and-swap。组织层面必须保证同一 repository/branch 的 workspace 写入串行，且 human / TS / Python 节点都遵守同一 Conductor 权限边界。

## Cancel

Perago 没有用户可见的 cancel hook。runtime 的 cancel 行为是清理内部状态：

| 对象 | 何时清理 | 失败影响 |
| --- | --- | --- |
| staging branch | staging 创建后，无论 publish 成功、publish 失败还是第二次 attempt fence 失败，都会尝试删除。 | 只记录 `failed to clean staging workspace`，不覆盖原始 task result。 |
| attempt-local workspace | workspace 目录创建后，无论 attempt 成功或失败，都会尝试删除。 | 只记录 cleanup 错误，不覆盖原始 task result。 |

如果 LakeFS publish 已经成功，cleanup 失败不会回滚目标 branch。Perago 不从 LakeFS metadata 恢复 Conductor completion，也不补发旧 attempt 的 result；当 publish 成功但 completion 未上报时，整体交给 Conductor timeout/fail/retry 处理。LakeFS staging branch 在异常路径允许残留；后续 execution 使用唯一 staging branch，不会复用残留 branch。

Abandoned target commits 不在 task publish 路径中删除。replacement publish 只移动 target branch 的可见 head；后续 LakeFS GC 属于 workflow-end 或运维清理策略，不属于 Perago task protocol。

## 故障分类

| 阶段 | 典型原因 | Result | 是否发布 workspace output |
| --- | --- | --- | --- |
| input validation | input shape 错误、workspace 模型无效 | `FAILED` | 否 |
| download | LakeFS ref 不存在、连接失败、本机写入失败 | `FAILED` | 否 |
| pre guardrails | 输入 workspace 不满足 task 文件契约 | `FAILED_WITH_TERMINAL_ERROR` | 否 |
| task body | 用户函数抛异常、result 模型校验失败 | `FAILED` | 否 |
| post guardrails | 输出文件不满足 task 契约 | `FAILED` | 否 |
| first attempt fence | attempt 已不再是当前 in-progress attempt | `FAILED` | 否 |
| no-op HEAD check | 可写 no-op completion 中 target HEAD 状态不符合协议 | `FAILED` | 否 |
| stage | symlink、删除/上传/commit 失败 | `FAILED` | 否 |
| second attempt fence | stage 后 attempt 失效 | `FAILED` | 否 |
| publish fence / publish | HEAD 状态不符合协议、merge/reset 失败或超时 | `FAILED` | 否 |
| staging cleanup | staging branch 删除失败 | 保留原始 result | 保留原始 result |
| local cleanup | 本机 workspace 删除失败 | 保留原始 result | 保留原始 result |

`FAILED_WITH_TERMINAL_ERROR` 用于 pre guardrail 这类上游输入 workspace 契约错误，以及任务函数显式抛出的 `TaskTerminalError`。post guardrail、`TaskFailed`、未知业务异常、stale attempt 和 publish 失败都是普通 `FAILED`，由 Conductor 按 TaskDef retry 策略处理。

## 运维边界

MVP 的 workspace publication 依赖这些运行时假设：

- Conductor lease/heartbeat 必须覆盖 task body、stage、publish、cleanup 和 result update。
- `PublishBudget` 应来自真实 LakeFS publish 观测值和安全边界，用来约束 publish timeout，并保留 Conductor completion budget reserve；task 的 `responseTimeoutSeconds` 应单独覆盖完整 attempt 生命周期。
- 不要把 LakeFS publish 与 Conductor completion 当成单个事务。publish 成功但 completion 未上报时，Perago 不做 workflow recovery。
- 如果 runtime 无法判断当前 attempt 是否仍可发布，应 fail closed，让 Conductor 按 timeout/fail/retry 策略处理。

Perago MVP 不提供严格 exactly-once publication。它提供的是 operationally bounded 的 attempt fence、publish fence、replacement publication 和 fail-closed 行为。
