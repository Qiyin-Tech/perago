# Conductor Runtime

Perago worker child process 通过 Conductor 拉取单个 task name 的 attempt，执行本地 task body，再把 `COMPLETED`、`FAILED` 或 `FAILED_WITH_TERMINAL_ERROR` 写回 Conductor。Conductor 负责调度和重试；Perago 负责把 Conductor task snapshot 映射成强类型 attempt，并在 workspace 发布前确认 attempt 仍然是当前可写入的 attempt。

## 启动前检查

`perago start` 连接 Conductor 前必须已经有完整的 runtime service config：

| 配置 | Required | 说明 |
| --- | --- | --- |
| `CONDUCTOR_SERVER_URL` | required for `perago start` | Orkes/Conductor API endpoint。 |
| LakeFS endpoint 和 credentials | required for `perago start` | workspace task 和当前 worker runtime 统一要求完整 LakeFS config。 |
| generated TaskDef | required before worker starts | 必须已经注册到 Conductor；`perago start` 只验证存在，不自动注册。 |

如果 Conductor 中没有同名 TaskDef，启动会失败：

```text
error: Conductor TaskDef 'features.build' is not registered; run perago extract and register it before start
```

正确顺序是先本地校验，再生成 TaskDef JSON，再通过部署流程注册到 Conductor，最后启动 worker：

```bash
perago check app.workers.features_build
perago extract app.workers.features_build --output generated/features.build.json
perago start app.workers.features_build -j 2 --execution-mode process
```

## Poll loop

当前默认 `process` runtime 中，每个 worker child process 只 poll 当前 module 中定义的单个 task name，并用 supervisor 注入的 `PERAGO_WORKER_ID` 作为 Conductor worker id。`perago start --execution-mode ...` 与 `PERAGO_EXECUTION_MODE` 的解析已经可用，优先级是 CLI 参数高于环境变量，默认值为 `process`；真正的单 broker + N executor process 调度模型会在后续重构步骤替换这一段 poll loop。

一次循环的顺序是：

1. 调用 Conductor `poll_task(task.name, worker_id=...)`。
2. 如果 Conductor 没有返回 task，等待 1 秒后继续 poll。
3. 如果 poll 抛出异常，记录 `failed to poll Conductor task`，等待 5 秒后重试。
4. 如果拿到 task，把 Conductor task 转成 `ConductorTaskAttempt`。
5. 执行 workspace task 或 workspace-free task。
6. 调用 Conductor `update_task(...)` 写回结果。
7. 如果 update 抛出异常，记录 `failed to update Conductor task result`，等待 5 秒后继续 poll。

poll loop 不在内存里排队任务，也不会在一个 Python module 内路由多个 task。并发来自 `perago start -j N` 启动的多个独立 child process。

显式 `thread` runtime 使用 SDK `TaskRunner` 和 `PeragoThreadWorker`。`-j N` 会传给 SDK worker 的 `thread_count`，`lease_extend_enabled=True`，并且 `register_task_def=False`、`register_schema=False`。在这个模式下，SDK thread pool 负责 poll、LeaseManager 追踪和 result update；Perago adapter 只把 SDK `Task` 转成 `ConductorTaskAttempt`，执行现有 task body/workspace 流程，再把 `RuntimeTaskResult` 转回 SDK `TaskResult`。thread mode 的 Conductor 可见 worker id 当前由 `PERAGO_WORKER_ID_PREFIX + "Broker"` 派生。

process broker 重构的 adapter foundation 已经存在于 `PeragoProcessDispatchWorker`。它同样满足 SDK worker contract：`thread_count=N`、`lease_extend_enabled=True`、`register_task_def=False`、`register_schema=False`，并用 broker worker id 作为 Conductor 可见 identity。和 thread worker 不同，它不会在 SDK worker thread 内执行 task body；`execute(...)` 会把 SDK `Task` 转成 `ConductorTaskAttempt`，放入 broker-to-executor assignment queue，等待 executor 返回 `RuntimeTaskResult` completion，再映射成 SDK `TaskResult`。完整的 supervisor 进程树、executor loop 和 attempt-fence RPC 仍是后续实现范围。

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
| `retried_task_id` | optional | 用于 metadata 和 publish-state 追踪。 |
| `response_timeout_seconds` | optional | SDK task 的 lease timeout snapshot；后续 SDK broker/runner adapter 用它接入 LeaseManager 追踪和日志排查。 |

workspace task 在发布前会重新读取当前 `task_id` 的 Conductor task，并调用 attempt fence。只有 fresh snapshot 同时满足以下条件时，才允许继续进入 stage 或 publish：

- `status == "IN_PROGRESS"`。
- `workflow_instance_id` 与已 poll 到的 attempt 一致。
- `task_id` 与已 poll 到的 attempt 一致。
- `retry_count` 与已 poll 到的 attempt 一致。

Perago 当前在两个位置检查 attempt fence：执行 task body 后、stage workspace 前检查一次；stage workspace 后、publish workspace 前再检查一次。任一检查失败都会返回普通 `FAILED`，并清理 attempt-local workspace；如果 staging 已经创建，还会尝试清理 staging branch。

## Result update

Perago 内部先生成 `RuntimeTaskResult`，再转换为 Conductor SDK 的 `TaskResult`：

| Perago status | Conductor 字段 | 典型来源 |
| --- | --- | --- |
| `COMPLETED` | `outputData` | task body 成功，workspace task 还包含已发布的 workspace output。 |
| `FAILED` | `reasonForIncompletion` | bad input、post guardrail、stale attempt、task body exception、publish failure。 |
| `FAILED_WITH_TERMINAL_ERROR` | `reasonForIncompletion` | pre guardrail failure。 |

`COMPLETED` 必须带 output，且不能带 failure reason。失败状态必须带 `reasonForIncompletion`，且不能带 output。worker id 会写入 Conductor result，便于从 Conductor 结果反查 worker 日志目录。

workspace task 如果配置了 `PublishBudget`，worker child 会把 `conductor_completion_timeout_seconds` 传给 Conductor update 请求。没有 publish budget 时使用 SDK 默认 update 行为。

`ConductorTaskAttempt.response_timeout_seconds` 来自 SDK task snapshot 本身。默认 process poll-loop 仍只保留该字段；显式 thread runtime 已交给 SDK `TaskRunner` 处理 LeaseManager 续租。后续 process broker 模式也会复用 SDK runner 管理租约。

## 输入输出边界

Conductor 传入的 `input_data` 必须匹配 Perago task 类型：

| Task 类型 | Required input shape | Completed output shape |
| --- | --- | --- |
| workspace task | 顶层只能有 `workspace` 和 `params` | 顶层包含 `workspace` 和 `result` |
| workspace-free task | 顶层只能有 `params` | 顶层只包含 `result` |

Conductor 不保存 attempt-local workspace 路径，也不参与 workspace 文件同步。workspace 路径、LakeFS download/stage/merge 和 staging cleanup 都由 Perago worker runtime 在本机执行。

## 故障边界

Conductor runtime 页面只覆盖与 Conductor 交互有关的边界：

- TaskDef 缺失会阻止 `perago start` 启动 worker。
- poll 失败和 result update 失败会记录日志并退避重试，不会让 supervisor 立即退出。
- result update 失败发生在 task 已经本地执行之后；对于 workspace task，publish 可能已经完成，因此排查时要同时看 Conductor task 状态、worker JSONL 日志和 LakeFS publication metadata。
- attempt fence 是 client-side soft fence。它降低旧 attempt 继续发布的风险，但不是 exactly-once 证明。
