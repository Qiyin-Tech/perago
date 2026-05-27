# Conductor Runtime

Perago 默认 `process` runtime 由一个 Conductor broker process 拉取单个 task name 的 attempt，再派发给本地 executor process 执行 task body，并由 broker 把 `COMPLETED`、`FAILED` 或 `FAILED_WITH_TERMINAL_ERROR` 写回 Conductor。Conductor 负责调度和重试；Perago 负责把 Conductor task snapshot 映射成强类型 attempt，并在可写 workspace 路径执行 stage、publish 或 no-op branch relocation 前确认 attempt 仍然是当前可写入的 attempt。

## 启动前检查

`perago start` 连接 Conductor 前必须已经有完整的 runtime service config：

| 配置 | Required | 说明 |
| --- | --- | --- |
| `CONDUCTOR_SERVER_URL` | required for `perago start` | Orkes/Conductor API endpoint。 |
| LakeFS endpoint 和 credentials | required for workspace-task `perago start` | workspace task 需要完整 LakeFS config；workspace-free task 不需要 LakeFS 连接变量。 |
| generated TaskDef | required before worker starts | 必须已经注册到 Conductor；`perago start` 只验证存在，不自动注册。 |

如果 Conductor 中没有同名 TaskDef，启动会失败：

```text
error: Conductor TaskDef 'features.build' is not registered; run perago extract and register it before start
```

推荐顺序如下：先本地校验，再生成 TaskDef JSON，再通过部署流程注册到 Conductor，最后启动 worker：

```bash
perago check app.workers.features_build
perago extract app.workers.features_build --output generated/features.build.json
perago start app.workers.features_build -j 2 --execution-mode process
```

## Poll loop

当前默认 `process` runtime 中，supervisor 启动一个 `perago-conductor-broker` 和 N 个 `perago-executor-000N`。broker 内嵌 SDK `TaskRunner(thread_count=N)`，只 poll 当前 module 中定义的单个 task name，并用 `PERAGO_WORKER_ID_PREFIX + "Broker"` 作为 Conductor 可见 worker id。`perago start --execution-mode ...` 与 `PERAGO_EXECUTION_MODE` 的解析优先级是 CLI 参数高于环境变量，默认值为 `process`。

一次 broker dispatch 的顺序是：

1. SDK `TaskRunner` poll 当前 task name，并按 SDK 策略处理空 poll、错误退避和 lease tracking。
2. broker adapter 把 SDK `Task` 转成 `ConductorTaskAttempt`，为这次实际执行生成 execution id，并写入 assignment queue。
3. executor 执行 workspace task 或 workspace-free task。
4. workspace task 的 attempt fence 通过 broker IPC 重新读取 fresh attempt。
5. executor 把 `RuntimeTaskResult` 写回 completion queue。
6. broker adapter 把 completion 映射成 SDK `TaskResult`。
7. SDK `TaskRunner` 调用 result update，并按 SDK 策略处理 `update_task_v2` / `update_task` fallback。

process mode 不会在一个 Python module 内路由多个 task。并发来自 broker SDK runner 的 `thread_count=N` 和 N 个独立 executor process；broker thread 只负责把 SDK `Task` 派发到 assignment queue 并等待 completion。

显式 `thread` runtime 使用 SDK `TaskRunner` 和 `PeragoThreadWorker`。`-j N` 会传给 SDK worker 的 `thread_count`，`lease_extend_enabled=True`，并且 `register_task_def=False`、`register_schema=False`。在这个模式下，SDK thread pool 负责 poll、LeaseManager 追踪和 result update；Perago adapter 只把 SDK `Task` 转成 `ConductorTaskAttempt`，执行现有 task body/workspace 流程，再把 `RuntimeTaskResult` 转回 SDK `TaskResult`。thread mode 的 Conductor 可见 worker id 当前由 `PERAGO_WORKER_ID_PREFIX + "Broker"` 派生。

thread mode 对 workspace task 使用一个 `PeragoThreadWorker`、一个 `LakeFSWorkspaceRuntime` 和 SDK `ThreadPoolExecutor(max_workers=N)`；workspace-free task 不创建 LakeFS runtime。同一个 runtime 实例的方法会被多个 SDK worker thread 并发调用，因此 `LakeFSWorkspaceRuntime` 只持有共享 client 和 publish budget，不缓存上一轮 attempt 的 repository、branch 或其他 per-attempt 可变状态；每次执行需要的 LakeFS 身份都来自当前 `workspace input` 或 `StagedWorkspace` 这类显式参数。

process broker 由 `PeragoProcessDispatchWorker` 与 `run_conductor_process_broker(...)` 组成。它同样满足 SDK worker contract：`thread_count=N`、`lease_extend_enabled=True`、`register_task_def=False`、`register_schema=False`，并用 broker worker id 作为 Conductor 可见 identity。和 thread worker 不同，它不会在 SDK worker thread 内执行 task body；`execute(...)` 会把 SDK `Task` 转成 `ConductorTaskAttempt`，生成本次 execution id，放入 broker-to-executor assignment queue，等待 executor 返回同一个 `task_id` 和同一个 `execution_id` 的 `RuntimeTaskResult` completion，再映射成 SDK `TaskResult`。SDK `TaskRunner` 仍负责 broker 侧 poll、LeaseManager tracking 和 result update。

process executor 的本地执行循环是 `run_process_executor_loop(...)`。executor 只消费 `ProcessTaskAssignment`，复用现有 `execute_polled_task()` 跑 workspace 或 workspace-free task，再把同一 `task_id` 和 `execution_id` 的 `ProcessTaskCompletion` 写回 completion queue；它不 poll Conductor，也不 update Conductor result。workspace task 的 attempt-fence reload 会写入 `attempt_fence_request_queue`，由 broker 调 Conductor `get_task` 后通过对应 executor 的 response queue 返回 fresh attempt snapshot。

execution id 的作用域是“一次 executor 实际执行 assignment”。Conductor task id 和 workflow step identity 使用各自的独立字段；broker 使用 execution id 拒绝旧 completion 或重复派发残留 completion，LakeFS runtime 使用它隔离 staging branch 和本机 attempt workspace。

Perago 的维护边界是 worker adapter、workspace execution 和 LakeFS publish。Conductor task lifecycle 仍交给 SDK `TaskRunner`：poll、lease tracking、result update 以及 update-v2 fallback 都由 SDK 处理。

## Attempt snapshot

Conductor task 会被映射成 Perago attempt snapshot：

| 字段 | Required | 用途 |
| --- | --- | --- |
| `workflow_instance_id` | required | 区分 workflow run。 |
| `task_id` | required | 当前 Conductor task attempt id，也是重新读取 attempt 的 key。 |
| `retry_count` | required | attempt fence 的一部分；重试 attempt 不能复用旧 snapshot。 |
| `task_def_name` | required | Conductor TaskDef name。 |
| `reference_task_name` | required | workflow 中的 reference task name。 |
| `seq` | required | Conductor task sequence。 |
| `iteration` | optional | 缺失时按 `0` 处理。 |
| `status` | required | 当前 Conductor task 状态。 |
| `input_data` | required | Perago task input payload；必须是 mapping。 |
| `retried_task_id` | optional | Conductor retry lineage，可用于日志排查。 |
| `response_timeout_seconds` | optional | SDK task 的 lease timeout snapshot；后续 SDK broker/runner adapter 用它接入 LeaseManager 追踪和日志排查。 |

可写 workspace task 在 stage、publish 或 no-op branch relocation 前会重新读取当前 `task_id` 的 Conductor task，并调用 attempt fence。默认 process 模式下，这个重新读取动作由 broker-owned RPC 完成，executor 不持有 Conductor client；thread 模式下由同进程 runner client 完成。只有 fresh snapshot 同时满足以下条件时，才允许继续进入可写 LakeFS 路径：

- `status == "IN_PROGRESS"`。
- `workflow_instance_id` 与已 poll 到的 attempt 一致。
- `task_id` 与已 poll 到的 attempt 一致。
- `retry_count` 与已 poll 到的 attempt 一致。

Perago 当前在可写路径的两个位置检查 attempt fence：执行 task body 后、stage workspace 或 no-op branch relocation 前检查一次；stage workspace 后、publish workspace 前再检查一次。任一检查失败都会返回普通 `FAILED`，并清理 attempt-local workspace；如果 staging 已经创建，还会尝试清理 staging branch。

`WorkspaceSpec(read_only=True)` 不进入这些 LakeFS 写入 fence。Read-only completion 没有 staging、publish 或 target branch relocation，runtime 会直接生成 `COMPLETED` result，按普通 Conductor worker completion 回写；Perago 不在 complete 前额外 `get_task` 自查 `IN_PROGRESS`。旧 `task_id`、已 terminal task 或 retry 后的新 attempt 的 result 接受与幂等性由 Conductor 服务端负责。

## Result update

Perago 内部先生成 `RuntimeTaskResult`，再转换为 Conductor SDK 的 `TaskResult`：

| Perago status | Conductor 字段 | 典型来源 |
| --- | --- | --- |
| `COMPLETED` | `outputData` | task body 成功；workspace task 包含 read-only、no-op 或 published workspace output。 |
| `FAILED` | `reasonForIncompletion` | bad input、MVP `TaskFailed`、未知 task body exception、post guardrail、stale attempt、publish failure。 |
| `FAILED_WITH_TERMINAL_ERROR` | `reasonForIncompletion` | pre guardrail failure 或 MVP `TaskTerminalError`。 |

`COMPLETED` 必须带 output，且不能带 failure reason。失败状态必须带 `reasonForIncompletion`，且不能带 output。worker id 会写入 Conductor result，便于从 Conductor 结果反查 worker 日志目录。
可预期的业务拒绝、待补充信息或人工处理分支不应伪装成 Conductor task failure；
worker 应返回成功的 `result`，让 WorkflowDef 用分支逻辑处理。

可写 workspace task 如果配置了有效 `PublishBudget`，TaskDef 会使用派生出的 `responseTimeoutSeconds`，让 SDK runner 的 LeaseManager 按 publication 预算续租。LakeFS merge request timeout 仍由 LakeFS runtime 使用 `lakefs_merge_timeout_seconds` 约束。`conductor_completion_timeout_seconds` 只是 `responseTimeoutSeconds` 中的 completion reserve；当前不传给 SDK `TaskRunner` 作为 result update HTTP timeout。没有 publish budget，或 `WorkspaceSpec(read_only=True)` 导致 publish budget 被忽略时，使用普通 task timeout。

`ConductorTaskAttempt.response_timeout_seconds` 来自 SDK task snapshot 本身。显式 thread runtime 和默认 process broker runtime 都交给 SDK `TaskRunner` 处理 LeaseManager 续租。

## 输入输出边界

Conductor 传入的 `input_data` 必须匹配 Perago task 类型：

| Task 类型 | Required input shape | Completed output shape |
| --- | --- | --- |
| workspace task | 顶层只能有 `workspace` 和 `params` | 顶层包含 `workspace` 和 `result` |
| workspace-free task | 顶层只能有 `params` | 顶层只包含 `result` |

Conductor 不保存 attempt-local workspace 路径，也不参与 workspace 文件同步。workspace 路径、LakeFS download、read-only/no-op completion、stage/merge 和 staging cleanup 都由 Perago worker runtime 在本机执行。

## 故障边界

Conductor runtime 页面只覆盖与 Conductor 交互有关的边界：

- TaskDef 缺失会阻止 `perago start` 启动 worker。
- poll 失败和 result update 失败会记录日志并退避重试，不会让 supervisor 立即退出。
- result update 失败发生在 task 已经本地执行之后；对于 workspace task，publish 可能已经完成，因此排查时要同时看 Conductor task 状态、worker JSONL 日志和 LakeFS target HEAD。
- attempt fence 是 client-side soft fence。它降低旧 attempt 继续发布的风险；strict exactly-once publication proof 超出 MVP 保证范围。
- 如果 LakeFS publish 已成功但 Conductor result update 未完成，Perago 不会在下一次启动时补发 completion；最终由 Conductor timeout/fail/retry 处理。
