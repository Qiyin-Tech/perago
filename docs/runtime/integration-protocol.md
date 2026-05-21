# Conductor / LakeFS Integration Protocol

本页面向不一定使用 Perago Python runtime 的 Conductor 节点开发者，包括 TypeScript worker、人工审核节点、外部工具节点和以后可能出现的其他语言 worker。只要节点读取或推进同一份 LakeFS workspace，就必须遵守这里的运行协议。

Perago Python runtime 是该协议的一份实现，不是唯一允许接入 Conductor 和 LakeFS 的代码路径。详细字段和执行细节仍以 [Input/Output Contract](../reference/input-output-contract.md)、[Conductor Runtime](conductor.md)、[LakeFS Runtime](lakefs.md) 和 [Workspace Publication](workspace-publication.md) 为准。

## 节点类型

跨语言节点先明确自己属于哪一类：

| 类型 | 可以做什么 | 不能做什么 |
| --- | --- | --- |
| Workspace consumer | 从 `workspace.ref` 读取 LakeFS 对象，输出小型业务 `result`，必要时原样传递 `workspace`。 | 不能推进 target branch，不能把 branch head 当输入版本。 |
| Workspace publisher | 读取 `workspace.ref`，把本轮输出发布成新的 LakeFS commit，并返回新的 `workspace.ref`。 | 不能绕过 staging branch、attempt fence、publish fence 和 Perago metadata。 |
| Workspace-free node | 只处理 `params -> result`，不读取也不发布 workspace。 | 不能接收假的 `workspace` 字段来复用 workspace task contract。 |

人工审核节点通常是 workspace consumer：它读取指定 commit 上的文件，回写审核决定。只有当人工节点会产出需要进入 workspace 的文件时，它才是 workspace publisher，或者应把文件交给后续 publisher 节点发布。

## 事实边界

跨语言实现必须保持这些 source-of-truth 边界：

| 事实 | 所属系统 | 规范 |
| --- | --- | --- |
| task attempt 状态、retry、timeout、completion | Conductor | 节点只能按当前 attempt 回写结果；不能从 LakeFS 反推并补发旧 completion。 |
| workspace 文件、人工产物、大体量 review archive | LakeFS | 大文件和可追溯产物进入 LakeFS，不进入 Conductor output。 |
| workspace locator | Conductor task payload | 只包含 repository、target branch、immutable commit ref。 |
| LakeFS / Conductor endpoint 和 credentials | worker-local runtime config | 不能写入 TaskDef、workflow input、task output 或 commit metadata。 |
| publish retry 分类 | LakeFS commit metadata | 只使用 `perago.*` metadata 和 target branch first-parent history 分类。 |

Conductor output 应保持小型结构化结果，例如审核结论、错误原因、计数或下游路由字段。需要长期保存或人工复核的文件应写入 LakeFS，并通过 `workspace.ref` 传递。

## Conductor Payload

Workspace 节点的 input 顶层字段必须是：

```json
{
  "workspace": {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3"
  },
  "params": {}
}
```

`workspace.ref_type` 当前只允许 `commit`。`workspace.ref` 必须是不可变 commit id，不能传 branch 名、`latest`、UI 当前 head 或本地路径。LakeFS endpoint、credentials、workspace prefix、staging branch 和 publish metadata 都不能放进 payload。

Workspace publisher 成功后必须返回：

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

Workspace consumer 如果 workflow contract 要继续传递 workspace，应原样返回输入的 `workspace`；如果该节点的 TaskDef 是 workspace-free contract，则 output 只能包含 `result`。失败结果不能携带 workspace output。

Workspace-free 节点的 input 只能包含 `params`，成功 output 只能包含 `result`。不要为了让节点知道 LakeFS 位置而把 workspace locator 塞进 `params`；需要读取 workspace 的节点应显式使用 workspace contract。

## TaskDef And Registration

TaskDef 必须在 worker 启动前由部署流程显式注册。任何语言的 worker 都不应在运行时自动创建或更新 TaskDef。

非 Python 节点的 TaskDef schema 必须和上面的 payload shape 一致：

- workspace publisher / consumer：`inputKeys = ["workspace", "params"]`，需要输出 workspace 时 `outputKeys = ["workspace", "result"]`。
- workspace-free node：`inputKeys = ["params"]`，`outputKeys = ["result"]`。
- `params` 和 `result` 的业务 schema 由该节点自己的代码或接口定义维护，但不能把业务字段展开到顶层。
- retry、timeout、response timeout 和 rate limit 属于 Conductor TaskDef control，不属于业务 payload。

如果该节点会发布 workspace，`responseTimeoutSeconds` 必须覆盖业务执行、LakeFS stage/merge、Conductor completion、heartbeat 和 shutdown grace。Perago 的 [Publish Budget](publish-budget.md) 是 Python task 的配置模型；其他语言可以用自己的配置方式，但预算语义必须一致。

## Reading Workspace

Workspace consumer 和 publisher 都必须从 `workspace.repository` 的 `workspace.ref` 读取对象。读取范围由节点 contract 或 workflow 约定的 prefix 决定；不要让节点默认扫描整个 repository，除非该 contract 明确以 repository root 为工作区。

读取规则：

- 只把 LakeFS object 映射为业务文件；不要把 branch、commit metadata 或 runtime marker 当业务输入。
- 不要读 target branch 当前 head 来代替 `workspace.ref`。
- 不要把本地工作目录路径写回 Conductor。
- 人工界面必须展示 repository、branch 和 commit ref，让审核发生在具体版本上，而不是模糊的最新版本上。

只读节点不得创建 staging branch，不得向 target branch 写入 commit。需要把人工修改、TS 生成物或外部工具输出写回 workspace 时，节点必须升级为 workspace publisher，或者把输出交给后续 publisher 节点。

## Publishing Workspace

任何会推进 `workspace.branch` 的非 Perago 节点都必须实现与 Perago 相同的发布协议：

1. 重新读取当前 Conductor attempt，确认它仍是 `IN_PROGRESS`，且 `workflow_instance_id`、`task_id`、`retry_count` 与已领取的 attempt 一致；这次 pre-stage attempt fence 必须发生在创建 staging branch、写对象或提交 staging commit 之前。
2. 从 input `workspace.ref` 创建本次 execution 专属 staging branch。
3. 只在 contract 允许的 prefix 内写入、删除或更新对象。
4. 提交 staging branch，并写入 `perago.phase=try` metadata。
5. 再次重新读取当前 Conductor attempt，并执行同一组 attempt fence 检查。
6. 读取 target branch 当前 head，并沿 first-parent history 回溯到 input `workspace.ref`。
7. 仅当 target head 等于 input ref，或这段 history 全部属于同一 `perago.logical_task_key` 时继续发布；否则 fail closed。
8. 将 staging branch squash merge 到 target branch，并写入 `perago.phase=confirm` metadata。
9. 用 merge commit id 生成成功 output 的 `workspace.ref`。
10. 尝试清理 staging branch；cleanup 失败只能记录日志，不能覆盖原始结果。

如果实现无法完成 attempt fence 或 publish fence，就不能直接推进 protected workspace branch。此时应改成 read-only consumer、调用 Perago publisher、或把输出发布到不会被下游当作 workspace truth 的临时位置。

## Metadata Contract

`perago.*` 是发布协议保留的 LakeFS metadata namespace。跨语言 publisher 必须按同一语义写入这些字段，值统一序列化为字符串。

`perago.logical_task_key` 的格式固定为：

```text
<workflow_instance_id>:<reference_task_name>:<seq>:<iteration>:<task_def_name>
```

try 和 confirm commit 都必须写入：

| Metadata | 来源 |
| --- | --- |
| `perago.phase` | `try` 或 `confirm`。 |
| `perago.logical_task_key` | 上面的 workflow step 稳定 key，不包含 `task_id`。 |
| `perago.workflow_instance_id` | Conductor attempt snapshot。 |
| `perago.task_def_name` | Conductor attempt snapshot。 |
| `perago.reference_task_name` | Conductor attempt snapshot。 |
| `perago.seq` | Conductor attempt snapshot。 |
| `perago.iteration` | Conductor attempt snapshot；缺失时按 `0` 处理。 |
| `perago.input_ref` | input `workspace.ref`。 |
| `perago.target_branch` | input `workspace.branch`。 |
| `perago.prefix` | 本节点发布的 LakeFS prefix。 |
| `perago.task_id` | 当前 Conductor task attempt id。 |
| `perago.retry_count` | 当前 retry count。 |
| `perago.retried_task_id` | Conductor retried task id；没有则为空字符串。 |

confirm commit 还必须写入：

| Metadata | 来源 |
| --- | --- |
| `perago.staging_branch` | 本次 execution 的 staging branch。 |
| `perago.staging_commit` | staging branch 上的 try commit。 |
| `perago.expected_head` | publish fence 选择的 target branch publish base。 |
| `perago.supersedes` | 同一 logical task 被覆盖的前序 commit；没有则为空字符串。 |

publish fence 判断必须检查 input ref 到 current head 之间的整个 first-parent commit range。只检查 current head 的 metadata 不够；中间任一 commit 缺少 `perago.logical_task_key` 或 key 不匹配，都必须 fail closed。

## Human Nodes

人工节点需要额外遵守这些规则：

- UI 必须把审核对象绑定到 immutable `workspace.ref`，不能让人基于 target branch 最新 head 做审批。
- 审核结论、路由选择、短文本说明可以写入 Conductor `result`。
- 人工上传、编辑或批注产生的文件应进入 LakeFS；如果这些文件会成为下游 workspace truth，必须通过 publisher 协议发布。
- 人工节点不能手动直接 commit 到 protected workspace branch。需要人工紧急修复时，应从当前 protected branch head 发起新的 workflow 或使用受控 publisher 工具。

## Failure And Recovery

跨语言节点必须沿用 fail-closed 恢复边界：

- input shape、workspace ref、attempt fence、publish fence、LakeFS merge 任何一步无法确认时，当前 attempt 返回失败，不发布 workspace output。
- merge timeout 或 result update 失败后，不能假设 publish 没发生；先按 `perago.logical_task_key`、`perago.task_id`、`perago.staging_commit` 查 target branch metadata。
- LakeFS merge 成功但 Conductor completion 未成功时，不从 LakeFS metadata 补发旧 completion。让 Conductor timeout/fail/retry，并由后续 attempt 的 publish fence 分类 target branch 状态。
- cleanup 失败只保留日志和运维告警，不改变 task result。

需要人工恢复时，从当前 protected branch head 发起新的 workflow。不要在一个状态不确定的旧 attempt 内补偿、重放或伪造成功结果。

## Implementation Checklist

开发一个 TS worker、人工节点或其他语言节点前，先逐项确认：

- TaskDef 已由部署流程注册，worker 启动时只验证或使用它。
- input/output 顶层 shape 与 [Input/Output Contract](../reference/input-output-contract.md) 一致。
- 读取 workspace 时只使用 immutable `workspace.ref`。
- 写 workspace 时使用 staging branch，不直接写 protected target branch。
- publisher 实现了两次 Conductor attempt fence 和 LakeFS publish fence。
- publisher 写入完整 `perago.*` try/confirm metadata。
- Conductor output 只放小型 `result` 和必要 workspace locator；文件和人工产物进入 LakeFS。
- 失败、不确定、metadata 不完整和 cleanup 失败都按 fail-closed 处理。

Perago Python worker 的执行顺序见 [Workspace Publication](workspace-publication.md)。跨语言实现如果和该页面出现语义差异，应先修正实现或更新协议，再让对应节点写入共享 workspace branch。
