# Workspace Publication

Workspace publication 是 Perago 对一次 workspace task attempt 的事务边界。任务函数只读写本机 attempt-local workspace；下载、stage、publish fence、LakeFS publish、Conductor output 和 cleanup 都由 runtime 包起来执行。

正式 LakeFS 操作协议见 [LakeFS 发布协议](../lakefs-publication-protocol.md)。本页按 attempt 生命周期说明 runtime 如何执行该协议。LakeFS object 同步细节见 `lakefs.md`，Conductor poll/result 细节见 `conductor.md`。

## 生命周期

一次成功的 workspace task attempt 顺序是：

1. 校验 Conductor input 顶层只有 `workspace` 和 `params`。
2. 从 `PERAGO_WORKSPACE_ROOT` 创建 execution-local attempt workspace，并写入 `.perago-attempt.json` marker。
3. 从 input `workspace.ref` 下载 `WorkspaceSpec.prefix` 下的 LakeFS object。
4. 执行 pre guardrails。
5. 调用 task body。
6. 校验 result 模型并执行 post guardrails。
7. 重新读取当前 Conductor attempt，并执行第一次 attempt fence。
8. 将本机 workspace stage 到从 input ref 创建的 LakeFS staging branch。
9. 重新读取当前 Conductor attempt，并执行第二次 attempt fence。
10. 读取目标 branch head，并通过 publish fence。
11. 按协议 merge 或 replacement publish 到目标 branch。
12. 用 published commit ref 生成 `COMPLETED` output。
13. 尝试清理 staging branch。
14. 尝试清理 attempt-local workspace。

成功 output 会保留 input workspace 的 repository、branch 和 `ref_type`，只把 `ref` 改成发布后的 LakeFS commit：

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

## Try

Perago 的 try 阶段由 runtime 对 workspace 执行 staging 操作：

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

## Attempt Fence

Perago 在两个位置检查 attempt fence：

| 位置 | 目的 |
| --- | --- |
| task body 和 post guardrails 之后、stage 之前 | 避免已经失效的 attempt 上传和提交 staging workspace。 |
| stage 之后、publish 之前 | 避免已经失效的 attempt 推进目标 branch。 |

fresh attempt 必须仍满足：

- `status == "IN_PROGRESS"`。
- `workflow_instance_id` 与已 poll 到的 attempt 一致。
- `task_id` 与已 poll 到的 attempt 一致。
- `retry_count` 与已 poll 到的 attempt 一致。

任一检查失败都会变成普通 `FAILED` result。若 staging 已经创建，runtime 会先尝试清理 staging branch；本机 attempt-local workspace 也会被清理。

## Publish Fence

publish fence 决定当前 attempt 是否还能推进目标 branch。它只检查当前 HEAD 与 input ref 的关系：

- `HEAD == input_ref` 表示首次发布。
- `parent(HEAD) == input_ref` 表示当前 head 可被视为 abandoned publication，可用 replacement publish 覆盖。
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
| stage | symlink、删除/上传/commit 失败 | `FAILED` | 否 |
| second attempt fence | stage 后 attempt 失效 | `FAILED` | 否 |
| publish fence / publish | HEAD 状态不符合协议、merge/reset 失败或超时 | `FAILED` | 否 |
| staging cleanup | staging branch 删除失败 | 保留原始 result | 保留原始 result |
| local cleanup | 本机 workspace 删除失败 | 保留原始 result | 保留原始 result |

`FAILED_WITH_TERMINAL_ERROR` 目前只用于 pre guardrail 这类上游输入 workspace 契约错误。post guardrail、stale attempt 和 publish 失败都是普通 `FAILED`，由 Conductor 按 TaskDef retry 策略处理。

## 运维边界

MVP 的 workspace publication 依赖这些运行时假设：

- Conductor lease/heartbeat 必须覆盖 task body、stage、publish、cleanup 和 result update。
- `PublishBudget` 应来自真实 LakeFS publish 观测值和安全边界，用来约束 publish timeout，并把 Conductor completion budget reserve 计入 `responseTimeoutSeconds`。
- 不要把 LakeFS publish 与 Conductor completion 当成单个事务。publish 成功但 completion 未上报时，Perago 不做 workflow recovery。
- 如果 runtime 无法判断当前 attempt 是否仍可发布，应 fail closed，让 Conductor 按 timeout/fail/retry 策略处理。

Perago MVP 不提供严格 exactly-once publication。它提供的是 operationally bounded 的 attempt fence、publish fence、replacement publication 和 fail-closed 行为。
