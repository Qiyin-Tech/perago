# Project Development TODO

## Publish fence commit-range classification

- [x] Implement the intended publish-fence commit range check, or deliberately narrow the implementation contract.

Current state: `LakeFSWorkspaceRuntime.publish_workspace()` passes only the target branch head commit into `build_workspace_publication_plan()`. The intended project behavior is to classify target-branch advancement by checking the commit range from the input workspace ref to the current head, and accepting only commits attributable to the same `perago.logical_task_key`.

Acceptance criteria:

- Fetch the relevant LakeFS commit range between `WorkspaceInput.ref` and the observed target branch head.
- Pass that range to `build_workspace_publication_plan()` instead of a single head commit.
- Keep unrelated or metadata-incomplete branch advancement fail-closed.
- Cover the behavior with a runtime-level test, not only metadata helper tests.

## Perago Conductor Runtime 重构计划

  默认采用 **1 个 Conductor broker 进程 + N 个 execution worker 进程**。`perago start module -j N` 在默认 `process` 模式下仍表示 N 个 OS 级执行槽位，但 Conductor 只看到一
  个逻辑 worker：同一个 `taskType + workerId` 通过 SDK `TaskRunner(thread_count=N)` 批量 poll、续租、update。

  核心选择：broker 进程内嵌 Conductor SDK `TaskRunner`，用自定义 `WorkerInterface` 适配 Perago。SDK thread pool 在 `process` 模式下不执行 Python task body，只负责把已 poll
  到的 Conductor task 派发到独立 executor process 并阻塞等待结果。这样复用 SDK 的 batch polling、adaptive backoff、LeaseManager、update-v2 fallback 和 event/metrics hook，
  同时保留进程隔离和 LakeFS transaction boundary。

  同时允许显式选择 `thread` 模式：单 broker/runner 进程内直接用 SDK thread pool 执行 task body，适合作为轻量路径。

  ## Public Interface

  - 新增 `perago start` 参数：
    ```bash
    perago start app.workers.features_build -j 4 --execution-mode process
    perago start app.workers.features_build -j 8 --execution-mode thread
    ```
  - 新增环境变量：
    ```bash
    PERAGO_EXECUTION_MODE=process
    PERAGO_EXECUTION_MODE=thread
    ```
  - 优先级固定为：CLI `--execution-mode` > `PERAGO_EXECUTION_MODE` > 默认 `process`。
  - `-j` 在两种模式都表示并发度：
    - `process`：executor process count。
    - `thread`：SDK `thread_count`。
  - 继续保留 Perago 自己生成 TaskDef/schema：
    - SDK worker `register_task_def=False`。
    - SDK worker `register_schema=False`。
    - 不使用 SDK decorator 自动注册 schema。
  - worker identity 分两层：
    - `PERAGO_BROKER_WORKER_ID`：Conductor 可见 worker id，默认由 `PERAGO_WORKER_ID_PREFIX + "Broker"` 派生。
    - `PERAGO_WORKER_ID`：executor slot 本地日志和 workspace 身份，仍按 `prefix0001`、`prefix0002` 生成。

  ## Process Mode

  - `perago start -j N --execution-mode process` 进程树：
    ```text
    supervisor
    ├── perago-conductor-broker
    ├── perago-executor-0001
    ├── perago-executor-0002
    └── ...
    ```
  - broker 进程负责全部 Conductor 通讯：
    - SDK `TaskRunner` poll/batch poll。
    - SDK `LeaseManager` 续租，`lease_extend_enabled=True`。
    - SDK result update，优先 `update_task_v2`，自动 fallback。
    - attempt fence reload，通过 Conductor `get_task`。
  - executor process 只负责本地执行：
    - 加载 single-task module。
    - 准备 worker runtime、日志、workspace root。
    - 执行 `execute_polled_task()`、LakeFS download/stage/publish/cleanup。
    - 不直接 poll Conductor，不 heartbeat，不 update result。
  - broker 到 executor 使用 multiprocessing IPC：
    - `assignment_queue`：broker thread 派发 `ConductorTaskAttempt`。
    - `completion_queue`：executor 返回 `RuntimeTaskResult`。
    - `attempt_fence_request_queue` + per-worker response queue：executor 请求 fresh attempt，由 broker 调 Conductor `get_task` 后返回。
  - 新增内部 adapter `PeragoProcessDispatchWorker(WorkerInterface)`：
    - `thread_count = process_count`
    - `lease_extend_enabled = True`
    - `get_identity()` 返回 broker worker id。
    - `execute(task: Task) -> TaskResult` 将 SDK `Task` 映射为 `ConductorTaskAttempt`，派发到 executor，等待完成，再映射为 SDK `TaskResult`。
  - `ConductorTaskAttempt` 保留 SDK task 的 `response_timeout_seconds`，用于验证 SDK lease tracking 和日志排查。

  ## Thread Mode

  - `perago start -j N --execution-mode thread` 进程树：
    ```text
    supervisor
    └── perago-conductor-runner
        ├── SDK worker thread 1
        ├── SDK worker thread 2
        └── ...
    ```
  - runner 进程同样使用 SDK `TaskRunner(thread_count=N, lease_extend_enabled=True)`。
  - 新增内部 adapter `PeragoThreadWorker(WorkerInterface)`：
    - 在 SDK worker thread 内直接调用现有 `execute_polled_task()`。
    - LakeFS transaction、Pydantic contract、result mapping、attempt fence 仍走 Perago runtime。
    - attempt fence 直接通过同进程 Conductor client 调 `get_task`，不需要 IPC。
  - `thread` 模式不是默认重负载模型；它是显式选择的轻量/I/O 路径。

  ## Failure Semantics

  - broker/runner 退出：supervisor 停止或重启整个 runtime set，不让 executor 孤儿继续执行新 task。
  - executor 退出：broker 正在等待的 SDK worker thread 返回 retryable `FAILED`，对应 lease 停止后由 Conductor retry 策略接管。
  - executor 执行超长：SDK `LeaseManager` 按 `responseTimeoutSeconds * 0.8` 续租，不按每秒固定 heartbeat，降低 Conductor server 压力。
  - shutdown：broker 先停止 SDK runner poll，再等待 active assignments；超过 grace 后 supervisor terminate/kill executor。
  - 已 publish 但 result update 失败：沿用现有 fail-closed/运维排查边界；SDK update retry/fallback 提升可靠性，但不宣称 exactly-once。
  - 替换当前“每个 worker process 自己 heartbeat”的中间实现：移除自管 `LeaseHeartbeat`，execution worker 不持有 Conductor polling/update/heartbeat client。

  - CLI/config：
    - 默认 mode 为 `process`。
  - SDK adapter：
    - `register_task_def=False`、`register_schema=False`。
    - `lease_extend_enabled=True`。
    - `thread_count=N` 正确传入 SDK `TaskRunner`。
    - completed/failed/terminal failed result 映射保持现有语义。
  - process mode：
    - `run_worker_supervisor(..., process_count=N, execution_mode="process")` 启动 1 broker + N executor。
    - workspace task 的两次 attempt fence 都通过 broker `get_task` RPC。
    - executor 不直接调用 Conductor poll/update/heartbeat。
    - executor restart 后 slot-local `PERAGO_WORKER_ID` 稳定。
  - thread mode：
    - `run_worker_supervisor(..., process_count=N, execution_mode="thread")` 不创建 executor process。
  - lease 行为：
    - mock SDK task 带 `response_timeout_seconds` 时，SDK `LeaseManager.track()` 被触发。
    - workspace-free task 也可续租，只要 Conductor task 带 response timeout；续租不再依赖 `PublishBudget` 是否存在。
  - 文档：
    - 更新 `docs/runtime/worker-processes.md`、`docs/runtime/conductor.md`、`docs/reference/environment-variables.md`。
    - 明确 `process` 默认、`thread` 显式选择、CLI 参数优先于环境变量。

  ## Progress

  - [x] Public interface parsing: `PERAGO_EXECUTION_MODE` is loaded into `RuntimeConfig.execution_mode`, `perago start --execution-mode` overrides it, and `process` remains the default.
  - [x] Attempt snapshot carries SDK `response_timeout_seconds`, so the later broker/runner adapters can hand lease timeout data to SDK lease tracking and logs.
  - [x] Thread runner foundation: `PeragoThreadWorker` adapts Perago task execution to SDK `WorkerInterface`, configures `TaskRunner(thread_count=N, lease_extend_enabled=True)`, and maps `RuntimeTaskResult` back to SDK `TaskResult`.
  - [x] Process dispatch worker foundation: `PeragoProcessDispatchWorker` adapts SDK `Task` polling to broker assignment/completion queues, preserves SDK worker flags, and maps executor `RuntimeTaskResult` completions back to SDK `TaskResult`.
  - [x] Process executor loop foundation: `run_process_executor_loop()` consumes broker assignments, executes Perago task runtime locally, and returns `ProcessTaskCompletion` without polling or updating Conductor from the executor.
  - [x] Process broker runner adapter: `run_conductor_process_broker()` wraps `PeragoProcessDispatchWorker` in SDK `TaskRunner(thread_count=N, lease_extend_enabled=True)` and preserves SDK-managed polling, lease extension, and result update.
  - [x] Process supervisor process tree and IPC queues: `run_worker_supervisor(..., execution_mode="process")` launches 1 broker + N executor processes, shares assignment/completion queues, and stops the runtime set when the broker exits.
  - [ ] Process attempt-fence RPC: workspace attempt-fence reloads still need to move from executor-side `get_task` to broker-owned Conductor `get_task` over IPC.

  ## Assumptions

  - 以当前 lockfile 的 `conductor-python==1.3.11` 为目标；该版本已有 SDK `TaskRunner`、batch poll、adaptive empty-poll backoff、`LeaseManager`、`lease_extend_enabled` 和
  update-v2 fallback。
  - 默认必须是 `process`，保持 Perago 重负载 workspace worker 的 OS process 隔离语义。
  - SDK thread pool 在 `process` 模式下只作为 broker dispatch slot，不作为 Python task body 并行模型。
  - broker 是 process mode 唯一 Conductor 通讯进程，包括 poll、heartbeat、update、attempt fence reload。
  - 不引入 durable local queue；broker/executor IPC 只是本机 runtime 管理，Conductor 仍是唯一持久任务源。
