# Worker Processes

Perago 的 `perago start` 是前台 supervisor 进程。supervisor 不直接执行 task body；默认 `process` 模式会为单个 task module 启动一个 Conductor broker process 和 N 个 executor process。每个 executor process 拥有稳定的 slot、独立的 `PERAGO_WORKER_ID` 和独立的日志文件。

这个页面说明本机进程模型。Conductor poll、LakeFS workspace 下载与发布的详细生命周期在后续 runtime 页面展开。

## 进程模型

`perago start app.workers.features_build -j 2 --execution-mode process` 会形成以下结构：

```text
supervisor process
├── perago-conductor-broker
├── perago-executor-0001
└── perago-executor-0002
```

`-j` 在默认 `process` execution mode 下是 executor process count，默认 `1`，最小值为 `1`。非法值会在 CLI 参数层或 `worker_child_specs()` 中被拒绝：

```text
worker process count must be at least 1
```

Perago 通过 `perago start --execution-mode ...` 和 `PERAGO_EXECUTION_MODE` 选择 execution mode；命令行参数优先，其次是环境变量，默认值为 `process`。`process` 模式用于隔离性更强的执行；`thread` 模式使用 SDK `TaskRunner(thread_count=N)` 在同一进程内执行 task body。

默认 process 模式的 task body 并发单位是独立 executor process。同一进程内线程池和 asyncio worker pool 不参与 executor 侧执行。broker process 会导入同一个 single-task module，并用同一个 task name 去 poll Conductor；SDK `TaskRunner(thread_count=N)` 负责 broker 侧并发 poll、lease tracking 和 result update。

process broker 的 adapter、SDK runner 启动函数和 supervisor 进程树已经落地。supervisor 会为 N 个 executor process 创建 N 个私有 execution slot；每个 slot 是 broker 和对应 executor 之间的一条双向 `multiprocessing.Pipe`。slot 本身是稳定的 logical slot，pipe 是当前 executor process 生命周期的 transport generation。`run_conductor_process_broker(...)` 会把 `PeragoProcessDispatchWorker` 包进 SDK `TaskRunner(thread_count=N)`，让 broker 负责 poll、续租和 update result，同时在每个 `execute(...)` 中租用一个空闲 slot，把 task body 执行派发到该 slot pipe。每次派发都会生成 execution id；executor completion 必须从同一条 pipe 带回同一个 `task_id` 和同一个 `execution_id` 的 `RuntimeTaskResult`，broker adapter 会 fail closed 处理无法匹配的 completion。

executor 侧的本地执行循环是 `run_process_executor_loop(...)`。它只监听自己的 slot pipe：收到 `ProcessTaskAssignment` 后执行 Perago task runtime，然后把 `ProcessTaskCompletion` 写回同一条 pipe；它不直接 poll Conductor，也不直接 update result。主任务执行不使用全局 work-stealing queue。workspace attempt fence reload 仍会通过共享 `ProcessAttemptFenceRequest` queue 发给 broker，broker 调 Conductor `get_task` 并把 `ProcessAttemptFenceResponse` 返回到该 executor 的 response queue。

显式 `thread` 模式不会创建 executor child process：

```text
supervisor process
└── perago runner threads
```

这个模式使用 `PERAGO_WORKER_ID_PREFIX + "Broker"` 作为 Conductor 可见 worker id。它已经接入 SDK poll、LeaseManager 和 result update；默认 `process` 模式也使用 broker identity 作为 Conductor 可见 worker id。

## Worker id

worker id 由 `PERAGO_WORKER_ID_PREFIX` 和 child slot 组成。slot 从 `1` 开始，按四位十进制补零：

```text
PERAGO_WORKER_ID_PREFIX=prodAFeaturesBuild
PERAGO_WORKER_ID=prodAFeaturesBuild0001
PERAGO_WORKER_ID=prodAFeaturesBuild0002
```

如果没有显式配置 `PERAGO_WORKER_ID_PREFIX`，Perago 会从 module target 删除非字母数字字符作为默认前缀：

```text
app.workers.features_build -> appworkersfeaturesbuild
```

supervisor 会把每个 executor 的 `PERAGO_WORKER_ID` 写入 child environment。executor process 启动后，`prepare_worker_runtime()` 会通过 `resolve_worker_id()` 读取这个值，并把它用于本地日志路径和运行时日志字段。broker process 使用 `PERAGO_WORKER_ID_PREFIX + "Broker"` 作为 Conductor poll/update identity。

常规部署中交给 supervisor 设置 `PERAGO_WORKER_ID`。它是 supervisor 生成的进程身份；task attempt id、workflow id、logical task key 和 LakeFS branch 名使用各自独立字段。

## Child process 启动步骤

每个 child process 的启动顺序是：

1. 把 child environment 合并到 `os.environ`。
2. 导入 single-task module，并解析唯一的 `@task(...)` 定义。
3. 准备 worker runtime。
4. 检查 runtime config 已存在。
5. broker 绑定 Conductor SDK runner 并进入 poll/update loop。
6. executor 绑定 LakeFS workspace runtime，并进入自己的 slot pipe loop。

启动时 supervisor 会先在 `PERAGO_WORKSPACE_ROOT` 下获取 `.perago-supervisor.lock`。锁文件写入当前 supervisor pid，用来禁止两个 supervisor 共享同一个 workspace root；如果已有锁的 pid 还活着，新的 supervisor 会拒绝启动；如果 pid 已不存在，旧锁会被清理。拿到锁后，supervisor 会 sweep 一次上次 supervisor/host crash 遗留的 orphan attempt workspace。随后 supervisor 会启动后台 workspace GC loop，按 `PERAGO_WORKSPACE_GC_INTERVAL` 周期扫描超过 `PERAGO_WORKSPACE_GC_TTL` 且不属于活跃 executor owner 的 attempt workspace。准备 worker runtime 只做本进程身份和日志初始化：

| 步骤 | 结果 |
| --- | --- |
| 配置 worker 日志 | 在 `PERAGO_LOG_ROOT/<module_target>/worker_id=<id>/` 下创建 JSONL 日志文件，并应用 size rotation 和 retention 设置。 |

日志初始化发生在 broker 或 executor child process 内。workspace root 加锁、workspace sweep、周期 GC、dead executor targeted GC 和 shutdown 后最终 sweep 由 supervisor 负责。supervisor 正常退出时会释放自己持有的 root 锁。

## 外部服务前置条件

`perago start` 在启动 supervisor 前会先做一轮服务前置校验：

| 条件 | 失败边界 |
| --- | --- |
| `CONDUCTOR_SERVER_URL` 已配置 | 缺失时报 `CONDUCTOR_SERVER_URL is required for perago start`。 |
| workspace task 的 LakeFS endpoint、access key id、secret access key 已完整配置 | 缺失时报 `LakeFS config is required for workspace tasks` 或列出缺失变量；workspace-free task 不需要 LakeFS。 |
| task module 可导入并能生成 TaskDef | 失败时按 task definition 或 schema 错误退出。 |
| Conductor 已注册同名 TaskDef | 缺失时报 `Conductor TaskDef '<name>' is not registered; run perago extract and register it before start`。 |

broker child process 内也会再次检查 Conductor config；executor child process 只在执行 workspace task 时检查 LakeFS config，因为默认 process 模式下只有 broker 持有 Conductor client。这个重复检查是进程边界内的防护，不能替代启动前的发布流程。

## 重启和停止

supervisor 会持续监控 broker 和每个 executor process。如果 broker 退出，supervisor 会停止当前 process runtime set；如果某个 executor 退出且 supervisor 尚未收到停止信号，它会按递增 backoff 重启同一 slot：

```text
1s, 2s, 4s, 8s, 16s, 30s, 30s, ...
```

executor 退出时，supervisor 会通过 broker control queue 发送该 slot/generation 的 exit event。broker 如果正等待该 executor 的 completion，会把对应 Conductor task 返回普通 `FAILED`；是否重试由 Conductor task retry 策略决定。重启时 supervisor 会为同一个 logical slot 创建新的 `Pipe()`，启动替换 executor，并通过同一条 control queue 把新 broker-side endpoint 和递增 generation 交给 broker。重启后，该 slot 的 worker id 不变。例如 slot `2` 退出后，替换进程仍使用 `prodAFeaturesBuild0002`。这让日志目录和 Conductor worker id 按 slot 保持稳定。

收到 `SIGINT` 或 `SIGTERM` 时，supervisor 会进入 drain：

1. broker 停止继续 poll 新 Conductor task。
2. supervisor 通过每个 executor 的 slot pipe 写入 executor stop sentinel。
3. idle executor 从 pipe poll 中退出。
4. 正在执行 assignment 的 executor 不会被信号打断；它会尽量跑完当前 task、staging cleanup、本机 workspace cleanup 和 completion send。
5. supervisor 等待 child 自然退出，然后做一次最终 workspace GC sweep。

executor child 自己收到 `SIGTERM` 或 `SIGINT` 时只设置停止标志，不会在 signal handler 中 `sys.exit()`、抛异常、清理 workspace、调用 LakeFS 或调用 Conductor。当前 assignment 的 `finally` 仍有机会执行。

默认情况下，Perago 不调用 `process.kill()`。如果确实需要 supervisor 在 drain deadline 后强制结束子进程，可以显式配置：

```text
PERAGO_SHUTDOWN_FORCE_KILL_AFTER=30s
```

该值未配置时，最终强制退出交给 systemd、Kubernetes 或容器 runtime。配置后，超过 deadline 仍存活的 child 会被 `kill()`，并记录 worker id、pid、phase、deadline 等字段；异常死亡后，supervisor 会对该 dead executor 的本机 attempt workspace 运行 targeted GC。

## 运行时边界

worker process 只对一个 task module 负责。Perago 拒绝在同一文件中定义多个 task 后通过命令行参数选择其中一个运行，也拒绝 app registry 风格的多 task worker。

process count 只增加同一个 task name 的 executor 并发能力；Conductor poll/update 仍集中在 broker。它不会改变 TaskDef、workspace prefix、Pydantic contract 或 publish budget。要运行另一个 task，需要启动另一个 `perago start <module_target>` supervisor。
