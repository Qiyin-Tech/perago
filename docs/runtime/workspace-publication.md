# Workspace Publication

Workspace publication 是 Perago 对一次 workspace task attempt 的事务边界。任务函数只读写本机 attempt-local workspace；下载、stage、publish fence、LakeFS merge、Conductor output 和 cleanup 都由 runtime 包起来执行。

这个页面按 attempt 生命周期说明发布顺序。LakeFS object 同步细节见 `lakefs.md`，Conductor poll/result 细节见 `conductor.md`。

## 生命周期

一次成功的 workspace task attempt 顺序是：

1. 校验 Conductor input 顶层只有 `workspace` 和 `params`。
2. 从 `PERAGO_WORKSPACE_ROOT` 创建 execution-local attempt workspace，并写入 `.perago-attempt.json` marker。
3. 从 input `workspace.ref` 下载 `WorkspaceSpec.prefix` 下的 LakeFS object。
4. 执行 pre guardrails。
5. 调用 task body。
6. 校验 result 模型并执行 post guardrails。
7. 重新读取当前 Conductor attempt，并执行第一次 attempt fence。
8. 将本机 workspace stage 到 LakeFS staging branch。
9. 重新读取当前 Conductor attempt，并执行第二次 attempt fence。
10. 通过 publish fence 后，将 staging branch squash merge 到目标 branch。
11. 用 published commit ref 生成 `COMPLETED` output。
12. 尝试清理 staging branch。
13. 尝试清理 attempt-local workspace。

成功 output 会保留 input workspace 的 repository、branch 和 `ref_type`，只把 `ref` 改成 LakeFS merge commit：

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

Perago 的 try 阶段不是用户函数里的 `try()` 方法，而是 runtime 对 workspace 的 staging 操作：

| 动作 | 说明 |
| --- | --- |
| create staging branch | 从 input `workspace.ref` 创建 execution-scoped staging branch；已有同名 branch 时 fail closed。 |
| sync prefix | 把本机 workspace 中 `WorkspaceSpec.prefix` 的完整投影同步到 staging branch。 |
| commit staging | 提交 staging branch，并写入 `perago.phase=try` metadata。 |

staging branch 名由 Conductor attempt 字段和本次 execution id 生成，包含 workflow、reference task、sequence、iteration、task id、retry count 和 execution id。它是内部 runtime 状态，不进入 Conductor input/output，也不要求任务作者手动管理。execution id 只隔离一次实际执行；`logical_task_key` 仍是 workflow step 级别的发布 fence key。

## Confirm

confirm 阶段把 staging commit 发布到目标 branch。Perago 先构造 `WorkspacePublicationPlan`，再把 staging branch squash merge 到 `workspace.branch`。

confirm metadata 会写入以下 Perago 字段：

| Metadata | 说明 |
| --- | --- |
| `perago.phase` | confirm 阶段固定为 `confirm`。 |
| `perago.logical_task_key` | 同一 workflow step 的稳定发布 key，不包含 Conductor task id。 |
| `perago.task_id` | 当前 Conductor attempt id。 |
| `perago.retry_count` | 当前 retry count。 |
| `perago.input_ref` | 本轮 attempt 的输入 ref。 |
| `perago.target_branch` | 被推进的目标 branch。 |
| `perago.prefix` | task 声明的 workspace prefix。 |
| `perago.staging_branch` | 被 merge 的 staging branch。 |
| `perago.staging_commit` | 被 merge 的 staging commit。 |
| `perago.expected_head` | publish fence 选择的发布基准。 |
| `perago.supersedes` | 同一 logical task 之前推进过的 commit；没有则为空字符串。 |

这些 metadata 是后续 retry、故障分类和人工排查的主要依据。`task_id` 标识一次 Conductor attempt；`logical_task_key` 标识同一个 workflow step，因此 retry attempt 会共享同一个 logical key。

## Attempt fence

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

## Publish fence

publish fence 决定当前 attempt 是否还能推进目标 branch。当前 MVP 接受两种状态：

| 状态 | 行为 |
| --- | --- |
| 目标 branch head 仍等于 input `workspace.ref` | 直接以 input ref 作为 publish base。 |
| 目标 branch 只被同一个 `perago.logical_task_key` 的提交推进 | 允许从 current head 继续发布，并记录 `perago.supersedes`。 |

runtime 判断第二种状态时，会从目标 branch 当前 head 沿 first-parent history 读取到 input `workspace.ref` 为止的 commit range，并把这段 range 交给 publish fence 分类。只检查 current head 的 metadata 不足以证明中间提交也属于同一个 logical task；如果这段 range 中任何 commit 缺少 `perago.logical_task_key` 或 key 不匹配，runtime 都会 fail closed。

这段扫描最多读取 1024 个 first-parent commits。超过上限会触发 `PublishFenceError`，错误文本包含 `advanced beyond supported publish range`；如果 first-parent history 中已经找不到 input `workspace.ref`，错误文本包含 `no longer contains workspace input ref`。这两个情况都表示 runtime 无法可靠分类 target branch advancement，因此当前 attempt 返回 `FAILED`。

其他 branch advancement 会触发 `PublishFenceError`，attempt 返回 `FAILED`。Perago 不会发布 workspace output，也不会把失败 attempt 的本机 workspace 作为下一次 retry 的输入。

这个 fence 是 client-side soft fence。它能在 merge 前发现 branch 已被其他 workflow step 或非 Perago 写入推进，但它不是 LakeFS server-side compare-and-swap，也不是 exactly-once publication 证明。

## Cancel

Perago 没有用户可见的 cancel hook。runtime 的 cancel 行为是清理内部状态：

| 对象 | 何时清理 | 失败影响 |
| --- | --- | --- |
| staging branch | staging 创建后，无论 publish 成功、publish 失败还是第二次 attempt fence 失败，都会尝试删除。 | 只记录 `failed to clean staging workspace`，不覆盖原始 task result。 |
| attempt-local workspace | workspace 目录创建后，无论 attempt 成功或失败，都会尝试删除。 | 只记录 cleanup 错误，不覆盖原始 task result。 |

如果 LakeFS merge 已经成功，cleanup 失败不会回滚目标 branch。Perago 不从 LakeFS metadata 恢复 Conductor completion，也不补发旧 attempt 的 result；当 publish 成功但 completion 未上报时，整体交给 Conductor timeout/fail/retry 处理。LakeFS staging branch 在异常路径允许残留；后续 execution 使用唯一 staging branch，不会复用残留 branch。

## 故障分类

| 阶段 | 典型原因 | Result | 是否发布 workspace output |
| --- | --- | --- | --- |
| input validation | input shape 错误、workspace 模型无效 | `FAILED` | 否 |
| download | LakeFS ref 不存在、连接失败、本机写入失败 | `FAILED` | 否 |
| pre guardrails | 输入 workspace 不满足 task 文件契约 | `FAILED_WITH_TERMINAL_ERROR` | 否 |
| task body | 用户函数抛异常、result 模型校验失败 | `FAILED` | 否 |
| post guardrails | 输出文件形状不满足 task 契约 | `FAILED` | 否 |
| first attempt fence | attempt 已不再是当前 in-progress attempt | `FAILED` | 否 |
| stage | symlink、删除/上传/commit 失败 | `FAILED` | 否 |
| second attempt fence | stage 后 attempt 失效 | `FAILED` | 否 |
| publish fence / merge | branch 被无关提交推进、merge 失败或超时 | `FAILED` | 否 |
| staging cleanup | staging branch 删除失败 | 保留原始 result | 保留原始 result |
| local cleanup | 本机 workspace 删除失败 | 保留原始 result | 保留原始 result |

`FAILED_WITH_TERMINAL_ERROR` 目前只用于 pre guardrail 这类上游输入 workspace 契约错误。post guardrail、stale attempt 和 publish 失败都是普通 `FAILED`，由 Conductor 按 TaskDef retry 策略处理。

## 运维边界

MVP 的 workspace publication 依赖这些运行时假设：

- Conductor lease/heartbeat 必须覆盖 task body、stage、publish、cleanup 和 result update。
- `PublishBudget` 应来自真实 LakeFS merge 观测值和安全边界，用来约束 merge timeout 与 Conductor completion timeout。
- 不要把 LakeFS merge 与 Conductor completion 当成单个事务。publish 成功但 completion 未上报时，Perago 不做 workflow recovery。
- 如果 runtime 无法判断当前 attempt 是否仍可发布，应 fail closed，让 Conductor 按 timeout/fail/retry 策略处理。

Perago MVP 不提供严格 exactly-once publication。它提供的是 operationally bounded 的 attempt fence、publish fence、metadata classification 和 fail-closed 行为。
