# LakeFS Runtime

Perago workspace task 使用 LakeFS 存放输入和发布输出。Conductor task input 只携带 `repository`、`branch`、`ref_type` 和 `ref`；LakeFS endpoint 与 credentials 来自 worker-local runtime config，不进入 Conductor payload，也不写入 TaskDef。

这个页面说明 worker child process 如何把一次 Conductor attempt 映射到 LakeFS download、stage、merge 和 cleanup。发布事务边界和 soft fence 的设计取舍在后续 architecture 页面展开。

## 输入边界

workspace task 的 Conductor input 必须包含 `workspace`：

```json
{
  "workspace": {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "input-commit"
  },
  "params": {
    "stem": "vocal"
  }
}
```

| 字段 | Required | 说明 |
| --- | --- | --- |
| `repository` | required | LakeFS repository id。 |
| `branch` | required | 成功发布后要推进的目标 branch。 |
| `ref_type` | required | 当前契约值是 `commit`。 |
| `ref` | required | 本次 attempt 读取的不可变输入 ref，也是 staging branch 的创建基准。 |

`WorkspaceSpec(prefix=...)` 由 task module 定义，不来自 Conductor input。它决定 LakeFS object path 和本机业务路径之间的映射：

```text
LakeFS object path              Local workspace path
audio/render/raw/input.txt  ->  raw/input.txt
audio/render/features/out.txt -> features/out.txt
```

当 `prefix="/"` 时，LakeFS repository 根目录映射为本机 workspace 根目录。非根 prefix 只允许 task 看到该 prefix 下的对象。

## Download

workspace task attempt 开始后，Perago 会先创建 attempt-local workspace 目录，再从 LakeFS 下载输入文件：

1. 读取 `WorkspaceInput.repository` 中的 repository。
2. 读取 `WorkspaceInput.ref` 对应的 LakeFS ref。
3. 列出 `WorkspaceSpec.prefix` 下的 object。
4. 把 prefix 内 object 映射为本机相对路径。
5. 写入 attempt-local workspace。

download 只处理 LakeFS 返回的 object。`path_type` 不是 `object` 的条目会被忽略。prefix 外对象不会出现在本机 workspace 中，`.perago-attempt.json` 这类 Perago marker 文件也不会从 LakeFS 映射为业务文件。

本机 workspace 是 attempt-local 目录，不是 worker 进程的固定工作目录。目录名包含 Conductor `task_id` 和 Perago execution id，因此同一个 Conductor attempt 被重复派发或 retry 时也不会复用旧 execution 的本机目录。

## Stage

task body 成功并通过第一次 attempt fence 后，Perago 会把本机 workspace 同步到 staging branch。stage 的顺序是：

1. 根据 Conductor attempt 字段和本次 execution id 生成 staging branch 名。
2. 从 `WorkspaceInput.ref` 创建 staging branch，`exist_ok=False`。
3. 列出 staging branch 上当前 prefix 下已有 object。
4. 根据本机 workspace 构建 sync plan。
5. 删除 prefix 内已不再存在的 remote object。
6. 上传 prefix 内新增或更新的本机文件。
7. 提交 staging branch，metadata 中写入 `perago.phase=try`。

stage 同步的是整个 `WorkspaceSpec.prefix` 投影，而不是只追加本轮新增文件。也就是说，本机 workspace 中删除某个文件后，stage 会删除 staging branch 上 prefix 内对应 object。prefix 外对象不受影响。

本机 workspace 中的 symlink 会在构建 upload plan 时被拒绝：

```text
workspace publication does not support symlinks: path/to/link
```

这是发布前的输入错误防护，避免 symlink 指向 attempt-local workspace 外部路径。

## Staging branch

staging branch 是 runtime 内部状态，不进入 Conductor task output。branch 名包含 workflow、reference task、sequence、iteration、task id、retry count 和 execution id：

```text
perago-staging-<workflow>-<reference>-seq-<seq>-iteration-<iteration>-task-id-<task_id>-retry-<retry_count>-exec-<execution_id>
```

execution id 表示“一次 executor 实际执行 assignment”，不是 Conductor logical task。它只用于隔离本机 workspace 和 staging branch；publish fence 仍使用不含 `task_id` 和 execution id 的 `perago.logical_task_key` 判断目标 branch 是否仍属于同一个 workflow step。

staging branch 必须是新 branch。如果同名 branch 已存在，stage 会 fail closed，当前 attempt 返回 `FAILED` 并进入正常 cleanup。stage 成功后，runtime 只把 staging branch 和 staging commit id 保存在 `StagedWorkspace` 中，供后续 publish 和 cleanup 使用。

## Publish

stage 成功并通过第二次 attempt fence 后，Perago 会发布 staging commit：

1. 读取目标 branch 的 current head。
2. 生成 workspace publication plan。
3. 检查目标 branch 是否仍可作为发布基准。
4. 将 staging branch squash merge 到 `WorkspaceInput.branch`。
5. 在 merge metadata 中写入 `perago.phase=confirm`、`perago.staging_branch`、`perago.staging_commit` 和 `perago.expected_head`。
6. 返回 LakeFS merge commit id。

成功发布后的 Conductor output 会把 input workspace 改写为新的 published ref：

```json
{
  "workspace": {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "published-commit"
  },
  "result": {}
}
```

如果 task 配置了 `PublishBudget`，LakeFS merge 会使用 `lakefs_merge_timeout_seconds` 作为 SDK request timeout。没有 publish budget 时，runtime 使用 LakeFS SDK 的默认 merge 行为。

## Publish fence

publish 前的 client-side fence 由 `build_workspace_publication_plan()` 执行。当前支持两种可发布状态：

| 状态 | 结果 |
| --- | --- |
| 目标 branch current head 等于 input `workspace.ref` | 以 input ref 作为 publish base。 |
| 目标 branch 已被同一个 `perago.logical_task_key` 的提交推进 | 允许从 current head 继续发布，并在 metadata 中记录 `perago.supersedes`。 |

其他 branch advancement 会抛出 `PublishFenceError`，attempt 结果映射为 `FAILED`。这类失败不发布 workspace output，并会触发 staging cleanup 和 attempt-local workspace cleanup。

为了避免对长历史做无界扫描，runtime 从 current head 沿 first-parent 回溯到 input `workspace.ref` 时最多读取 1024 个 commits。超过上限，或在 first-parent history 中找不到 input ref，都会 fail closed 为 `PublishFenceError`。

这个 fence 是 worker 进程里的 soft fence。它能在 merge 前发现意外 branch advancement，但不是 LakeFS server-side compare-and-swap，也不是 exactly-once publication 证明。

## Cleanup

不论 attempt 最终成功还是失败，只要 staging 已经创建，Perago 都会尝试删除 staging branch。cleanup 失败只记录日志：

```text
failed to clean staging workspace
```

staging cleanup 失败不会覆盖原始 task result。如果 LakeFS merge 已经成功，cleanup 失败也不会回滚目标 branch。

attempt-local workspace cleanup 由本机 workspace runtime 处理。它和 staging cleanup 是两个独立动作：前者删除本机临时目录，后者删除 LakeFS staging branch。

正常路径会先尝试清理 staging branch，再清理本机 workspace。LakeFS staging cleanup 失败只记录日志，不会阻止本机 workspace cleanup。若 executor 被 kill、崩溃或 host 退出，本机 workspace 会保留 `.perago-attempt.json` marker；supervisor 发现 dead executor 后会做 targeted GC，下一次 `perago start` 也会先 sweep orphan workspace。远端 LakeFS staging branch 在异常路径允许残留，后续 execution 不会复用同一个 staging branch。

## 故障边界

LakeFS runtime 的常见失败边界是：

| 阶段 | 典型原因 | Task result |
| --- | --- | --- |
| download | repository/ref 不存在、LakeFS 连接失败、本机写入失败 | `FAILED` |
| stage | symlink、上传失败、删除失败、commit 失败 | `FAILED` |
| publish | publish fence 失败、merge 失败、merge timeout | `FAILED` |
| cleanup staging | staging branch 删除失败 | 保留原始结果，只写日志 |
| cleanup local workspace | 本机临时目录删除失败 | 保留原始结果，只写日志 |

LakeFS 连接配置缺失不会等到第一个 attempt 才暴露。`perago start` 在启动 worker 前就要求 LakeFS endpoint、access key id 和 secret access key 完整存在。
