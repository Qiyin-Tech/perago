# Runtime Logging

Perago worker 日志是 worker-local 运行时状态。它用于排查 worker process 启动、Conductor poll/update、workspace 下载/发布和本机清理行为；Conductor task output 和 LakeFS workspace 都不会携带这些日志。

日志只在 child process 内配置。`perago start` 的 supervisor 负责派生和监控 child process；每个 child 在 `prepare_worker_runtime()` 阶段配置自己的 Loguru sink，并获得独立的 JSONL 日志文件。

## 文件位置

日志根目录由 `PERAGO_LOG_ROOT` 控制，默认是平台临时目录下的 `perago/logs`。每个 worker child 的日志路径如下：

```text
<PERAGO_LOG_ROOT>/<module_target>/worker_id=<PERAGO_WORKER_ID>/pid=<pid>__started=<timestamp>.jsonl
```

示例：

```text
/var/tmp/perago/logs/
└── app.workers.features_build/
    └── worker_id=prodAFeaturesBuild0001/
        └── pid=42118__started=20260520T143015+0800.jsonl
```

`module_target` 和 `worker_id` 会经过安全路径片段处理后再进入目录名。worker id 由 supervisor 写入 child environment，不表示 task id、workflow id 或 LakeFS branch 名。

同一个 child slot 重启时会复用同一个 worker id，但新进程会写入新的 `pid=...__started=...jsonl` 文件。排查重启问题时，先按 `worker_id=<id>` 找目录，再按文件名里的 `started` 时间排序。

## 日志格式

日志 sink 使用 Loguru 的 `serialize=True`，因此文件是 JSONL：每一行都是一个完整 JSON object。消息文本位于 `record.message`，结构化字段位于 Loguru 的 `extra` 字段。

最小结构如下：

```json
{"text":"...","record":{"message":"worker started","time":{"repr":"2026-05-20 14:30:15.000000+08:00"},"extra":{"worker_id":"prodAFeaturesBuild0001","module_target":"app.workers.features_build","log_file":"/var/tmp/perago/logs/app.workers.features_build/worker_id=prodAFeaturesBuild0001/pid=42118__started=20260520T143015+0800.jsonl"}}}
```

实际 JSON object 还会包含 Loguru 提供的 level、module、function、line、process、thread 和 exception 信息。文档和工具不应依赖字段顺序。

## 时间和时区

Perago 在配置 worker 日志时会给 Loguru record 安装 patcher，把 record time 转成固定的 `UTC+08:00`。这条规则不依赖主机本地时区。

文件名中的 `started` 时间也使用同一个 `UTC+08:00` 时区，格式是：

```text
YYYYMMDDTHHMMSS+0800
```

## Rotation 和 retention

日志 rotation 由 `PERAGO_LOG_FILE_MAX_SIZE` 控制，默认 `100MB`。它接受正数加二进制单位：

| 示例 | 字节数 |
| --- | ---: |
| `512KB` | `524288` |
| `100MB` | `104857600` |
| `1.5GB` | `1610612736` |

裸数字非法，`0MB` 也非法。配置错误会在 `load_runtime_config()` 阶段变成 `RuntimeConfigError`，因此 `perago check` 可以提前发现。

日志 retention 由 `PERAGO_LOG_RETENTION` 控制，默认 `30d`。当前只接受正整数天数，例如 `7d` 或 `30d`。Perago 把解析后的 `timedelta` 交给 Loguru retention 参数；历史文件何时删除由 Loguru 的文件 sink 行为决定。

## 与运行时生命周期的关系

`prepare_worker_runtime()` 的本机准备顺序是：

1. 解析当前进程的 worker id。
2. 创建 worker 日志目录并配置 JSONL sink。
3. 返回 `WorkerRuntime(worker_id, log_file, swept_workspaces=[])`。

因此 `WorkerRuntime.log_file` 是当前 child process 的活跃日志文件路径。worker 启动后会记录一条 `worker started` 日志，并把 `worker_id`、`module_target` 和 `log_file` 作为结构化字段写入。

attempt workspace 清理不在 `prepare_worker_runtime()` 内执行。`perago start` 的 supervisor 负责启动前 sweep、后台 GC loop、dead executor targeted GC 和 shutdown 后最终 sweep。

## 排查入口

常见定位路径：

| 问题 | 先看哪里 |
| --- | --- |
| `perago start` 启动前失败 | 终端 stderr；此时 child process 可能尚未启动，也就没有 worker 日志。 |
| child process 启动后退出并被重启 | supervisor stderr 和对应 `worker_id=<id>` 目录下的多个日志文件。 |
| Conductor poll 失败 | worker JSONL 中 `failed to poll Conductor task` 相关记录。 |
| task attempt 执行失败 | worker JSONL 中带 `task_id`、`workflow_instance_id` 或异常信息的记录。 |
| workspace 清理或发布问题 | worker JSONL 中的 workspace runtime、LakeFS runtime 和 cleanup 相关记录。 |

Perago 日志目录只保存 worker-local 事实。最终 task 状态仍以 Conductor task update 为准；LakeFS workspace 是否发布成功按 target HEAD、staging branch 和 [LakeFS 发布协议](../lakefs-publication-protocol.md) 判定。
