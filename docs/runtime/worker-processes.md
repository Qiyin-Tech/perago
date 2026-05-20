# Worker Processes

Perago 的 `perago start` 是前台 supervisor 进程。supervisor 不直接执行 task body；它先为单个 task module 生成一组 child process spec，再启动一个或多个 worker child process。每个 child process 拥有稳定的 slot、独立的 `PERAGO_WORKER_ID` 和独立的日志文件。

这个页面说明本机进程模型。Conductor poll、LakeFS workspace 下载与发布的详细生命周期在后续 runtime 页面展开。

## 进程模型

`perago start app.workers.features_build -j 2 --execution-mode process` 会形成以下结构：

```text
supervisor process
├── perago-worker-appworkersfeaturesbuild0001
└── perago-worker-appworkersfeaturesbuild0002
```

`-j` 在默认 `process` execution mode 下是 executor process count，默认 `1`，最小值为 `1`。非法值会在 CLI 参数层或 `worker_child_specs()` 中被拒绝：

```text
worker process count must be at least 1
```

Perago 当前已支持解析 execution mode 公共接口：`perago start --execution-mode ...` 优先于 `PERAGO_EXECUTION_MODE`，再退回默认 `process`。`process` 模式是默认重负载模型；`thread` 模式作为显式轻量路径仍在 Conductor Runtime 重构后续步骤中落地。

当前已实现的并发单位仍是独立进程，不是同一进程内的线程池或 asyncio worker pool。每个 child process 会导入同一个 single-task module，并用同一个 task name 去 poll Conductor。

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

supervisor 会把每个 child 的 `PERAGO_WORKER_ID` 写入 child environment。child process 启动后，`prepare_worker_runtime()` 会通过 `resolve_worker_id()` 读取这个值，并把它用于 Conductor poll/update、日志路径和运行时日志字段。

不要在常规部署中手动设置 `PERAGO_WORKER_ID`。它是 supervisor 生成的进程身份，不是 task attempt id、workflow id、logical task key 或 LakeFS branch 名。

## Child process 启动步骤

每个 child process 的启动顺序是：

1. 把 child environment 合并到 `os.environ`。
2. 导入 single-task module，并解析唯一的 `@task(...)` 定义。
3. 准备 worker runtime。
4. 检查 Conductor 和 LakeFS runtime config 已存在。
5. 绑定 Conductor client 和 LakeFS workspace runtime。
6. 进入 Conductor poll loop。

准备 worker runtime 会做两件本机工作：

| 步骤 | 结果 |
| --- | --- |
| 清理遗留 attempt workspace | 删除带 Perago attempt marker 的本机 workspace 目录，避免上一次异常退出留下的临时目录污染新 attempt。 |
| 配置 worker 日志 | 在 `PERAGO_LOG_ROOT/<module_target>/worker_id=<id>/` 下创建 JSONL 日志文件，并应用 size rotation 和 retention 设置。 |

这些动作发生在 child process 内。supervisor 本身只负责派生、监控、重启和停止 child process。

## 外部服务前置条件

`perago start` 在启动 supervisor 前会先做一轮服务前置校验：

| 条件 | 失败边界 |
| --- | --- |
| `CONDUCTOR_SERVER_URL` 已配置 | 缺失时报 `CONDUCTOR_SERVER_URL is required for perago start`。 |
| LakeFS endpoint、access key id、secret access key 已完整配置 | 缺失时报 `LakeFS config is required for perago start` 或列出缺失变量。 |
| task module 可导入并能生成 TaskDef | 失败时按 task definition 或 schema 错误退出。 |
| Conductor 已注册同名 TaskDef | 缺失时报 `Conductor TaskDef '<name>' is not registered; run perago extract and register it before start`。 |

child process 内也会再次检查 Conductor 和 LakeFS config。这个重复检查是进程边界内的防护，不能替代启动前的发布流程。

## 重启和停止

supervisor 会持续监控每个 child process。如果某个 child 退出且 supervisor 尚未收到停止信号，它会按递增 backoff 重启同一 slot：

```text
1s, 2s, 4s, 8s, 16s, 30s, 30s, ...
```

重启后，该 slot 的 worker id 不变。例如 slot `2` 退出后，替换进程仍使用 `prodAFeaturesBuild0002`。这让日志目录和 Conductor worker id 按 slot 保持稳定。

收到 `SIGINT` 或 `SIGTERM` 时，supervisor 会设置 stop event，让 poll loop 有机会自然退出。随后停止流程按固定顺序执行：

1. 等待 child 自然退出，最多 `10` 秒。
2. 对仍存活的 child 调用 `terminate()`。
3. 再等待 `5` 秒。
4. 对仍存活的 child 调用 `kill()`。
5. 最后再等待 `5` 秒完成回收。

因此 task body 应避免吞掉进程信号或长期阻塞不可中断 I/O；否则 supervisor 会在宽限期后强制结束进程。

## 运行时边界

worker process 只对一个 task module 负责。Perago 不支持在同一文件中定义多个 task，再通过命令行参数选择其中一个运行；也不支持 app registry 形状的多 task worker。

worker process count 只增加同一个 task name 的并行 poll 能力。它不会改变 TaskDef、workspace prefix、Pydantic contract 或 publish budget。要运行另一个 task，需要启动另一个 `perago start <module_target>` supervisor。
