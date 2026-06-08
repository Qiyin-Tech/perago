# Runtime Configuration

Perago runtime configuration 是 worker-local 配置。它控制本机 workspace 目录、日志目录、worker id、Conductor 连接和 LakeFS 连接；这些值不属于 Conductor task input/output，也不会写入生成的 TaskDef JSON。

配置由 `load_runtime_config()` 统一读取。`perago check`、`perago extract` 和 `perago start` 都会在加载 task module 前执行同一套配置加载与校验。

## 配置来源和优先级

Perago 会读取当前工作目录下的 `.env`，再用真实进程环境变量覆盖同名值：

```text
.env values < process environment values
```

`.env` 只填补缺失值。部署系统、shell 或 supervisor 注入的进程环境变量拥有更高优先级。

`.env` 支持简单的 `KEY=value`、`export KEY=value`、单引号和双引号包裹的值。空行、注释行和没有 `=` 的行会被忽略。

## 最小本地示例

```text
CONDUCTOR_SERVER_URL=http://localhost:8080/api

# Required only when starting workspace tasks.
# LAKECTL_SERVER_ENDPOINT_URL=http://localhost:8000
# LAKECTL_CREDENTIALS_ACCESS_KEY_ID=replace-with-real-access-key
# LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY=replace-with-real-secret-key

PERAGO_WORKSPACE_ROOT=/var/tmp/perago/workspaces
PERAGO_LOG_ROOT=/var/tmp/perago/logs
PERAGO_LOG_FILE_MAX_SIZE=100MB
PERAGO_LOG_RETENTION=30d
PERAGO_WORKER_ID_PREFIX=peragoLocalWorker
PERAGO_INSTANCE_ID=perago-local-001
PERAGO_FAILURE_REASON_MAX_LENGTH=500
PERAGO_WORKSPACE_GC_TTL=24h
PERAGO_WORKSPACE_GC_INTERVAL=1h
# Optional; unset by default.
PERAGO_SHUTDOWN_FORCE_KILL_AFTER=30s
```

`replace-me` 属于占位值。Perago 看到未替换的连接密钥占位值时会拒绝启动。workspace-free task 不需要 LakeFS 连接变量；不要为了启动 workspace-free worker 配置占位 LakeFS 值。

## 变量表

| 变量 | Required | 默认值 | 说明 |
| --- | --- | --- | --- |
| `PERAGO_WORKSPACE_ROOT` | optional | 平台临时目录下的 `perago/workspaces` | attempt-local workspace 根目录。必须是 worker 主机文件系统路径，LakeFS object path 不适用。一个 running supervisor 会独占一个 root。 |
| `PERAGO_LOG_ROOT` | optional | 平台临时目录下的 `perago/logs` | worker JSONL 日志根目录。 |
| `PERAGO_LOG_FILE_MAX_SIZE` | optional | `100MB` | 单个日志文件 rotation 阈值。接受正数加 `KB`、`MB` 或 `GB`，例如 `512KB`、`100MB`、`1.5GB`。裸数字非法。 |
| `PERAGO_LOG_RETENTION` | optional | `30d` | 日志保留天数。接受正整数加 `d`，例如 `7d` 或 `30d`。 |
| `PERAGO_WORKER_ID_PREFIX` | optional | 从 module target 删除非字母数字字符后派生 | supervisor 为子进程生成 `PERAGO_WORKER_ID` 的前缀。显式配置时只能包含 ASCII 字母和数字。 |
| `PERAGO_WORKER_ID` | generated / debug-only | supervisor 生成；非 supervisor 进程退回到 module target 加 pid | worker process 身份。`perago start -j` 会为每个 child slot 写入该值；用户一般不应在 `.env` 中配置。 |
| `PERAGO_INSTANCE_ID` | required for metrics-enabled `perago start` when `worker_capacity=True` | 无 | Perago runtime instance 的低基数部署身份。用于区分多个 Perago instance 运行同一个 Task Worker 时的 `runtime.busy_slots`，接受 ASCII 字母、数字、点、下划线和连字符。 |
| `PERAGO_FAILURE_REASON_MAX_LENGTH` | optional | `500` | 写入 Conductor `reasonForIncompletion` 的最大字符数。只接受正整数；超长 failure reason 会截断并记录截断元数据到 worker 日志。 |
| `PERAGO_WORKSPACE_GC_TTL` | optional | `24h` | supervisor workspace GC 删除 abandoned attempt workspace 前等待的最小年龄。接受正整数加 `s`、`m`、`h` 或 `d`。仍属于活跃 executor owner 的 workspace 不会被周期 GC 删除。 |
| `PERAGO_WORKSPACE_GC_INTERVAL` | optional | `1h` | supervisor 后台 workspace GC loop 的运行间隔。接受正整数加 `s`、`m`、`h` 或 `d`。 |
| `PERAGO_SHUTDOWN_FORCE_KILL_AFTER` | optional | unset | shutdown drain 的可选强制 kill deadline。未配置时 Perago 不调用 `process.kill()`；配置后接受正整数加 `s`、`m`、`h` 或 `d`，例如 `30s`。 |
| `CONDUCTOR_SERVER_URL` | required for `perago start` | 无 | Conductor API endpoint。`perago check` 和 `perago extract` 可在未配置时运行并报告 `not configured`。 |
| `LAKECTL_SERVER_ENDPOINT_URL` | required for workspace-task `perago start` | 无 | LakeFS endpoint。LakeFS 三个变量必须同时配置或同时省略；workspace-free `perago start` 不需要 LakeFS。 |
| `LAKECTL_CREDENTIALS_ACCESS_KEY_ID` | required for workspace-task `perago start` | 无 | LakeFS access key id。 |
| `LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY` | required for workspace-task `perago start` | 无 | LakeFS secret access key。 |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | required for metrics-enabled `perago start` | 无 | OTLP/HTTP protobuf metrics endpoint。Perago 第一版只支持 metrics-specific endpoint，不读取 generic `OTEL_EXPORTER_OTLP_ENDPOINT`。 |
| `OTEL_EXPORTER_OTLP_METRICS_COMPRESSION` | optional | unset | 第一版只接受 `gzip`。 |
| `OTEL_EXPORTER_OTLP_METRICS_TIMEOUT` | optional | OTel Python exporter 默认值 | 按 OpenTelemetry Python OTLP/HTTP metrics exporter 当前接受的秒数数字解析，例如 `10` 表示 10 秒；不接受 `10s`。 |
| `OTEL_METRIC_EXPORT_INTERVAL` | optional | OTel SDK 默认值 | 正整数毫秒，例如 `60000`。 |

Perago 目前只解析 `CONDUCTOR_SERVER_URL` 作为 Conductor runtime config。Conductor auth key/secret 可以由底层 SDK 或部署环境使用；Perago `RuntimeConfig` 暂不建模这两个字段。

## 本地目录校验

默认情况下，`load_runtime_config()` 会探测 `PERAGO_WORKSPACE_ROOT` 和 `PERAGO_LOG_ROOT` 是否可创建、可写。探测会在目标根目录下创建临时目录和 `write-test` 文件，再立即删除。

`perago start` 会在 `PERAGO_WORKSPACE_ROOT` 下创建 `.perago-supervisor.lock`，并写入当前 supervisor pid。该锁表达部署边界：一个 supervisor 独占一个 workspace root，不允许两个 supervisor 同时指向同一个 root。如果锁内 pid 仍存活，新的 supervisor 会拒绝启动；如果上次 supervisor 或 host 崩溃留下的 pid 已不存在，启动时会清理旧锁并重新加锁。正常退出时 supervisor 会释放自己持有的锁。

`perago check` 因此可以在不连接 Conductor 或 LakeFS 的情况下验证本机目录配置：

```bash
perago check app.workers.features_build
```

成功输出会包含解析后的目录和连接配置状态：

```text
ok: features.build
workspace_root: /var/tmp/perago/workspaces
log_root: /var/tmp/perago/logs
worker_id_prefix: peragoLocalWorker
conductor: configured
lakefs: configured
```

目录探测失败会抛出 `RuntimeConfigError`，错误文本包含不可写路径和底层 `OSError`。

## Worker id 规则

`PERAGO_WORKER_ID_PREFIX` 是用户可配置的碰撞隔离旋钮。显式配置时，值必须非空并且只包含 `A-Z`、`a-z`、`0-9`：

```text
PERAGO_WORKER_ID_PREFIX=prodAFeaturesBuild
```

`perago start -j 2 app.workers.features_build` 会派生：

```text
PERAGO_WORKER_ID=prodAFeaturesBuild0001
PERAGO_WORKER_ID=prodAFeaturesBuild0002
```

如果没有显式配置前缀，Perago 会从 module target 派生默认前缀。例如 `app.workers.features_build` 会变成 `appworkersfeaturesbuild`。显式配置非法前缀不会被自动修正：

```text
PERAGO_WORKER_ID_PREFIX=prod-a-features-build
```

会失败并报告：

```text
PERAGO_WORKER_ID_PREFIX must contain only ASCII letters and digits
```

`PERAGO_WORKER_ID` 是进程身份。task attempt id、logical task key 和 workspace publication key 使用各自的独立字段。supervisor 管理的 worker 会由 supervisor 写入该值；只有非 supervisor 本地调试进程才会使用用户提供的 `PERAGO_WORKER_ID` 或 pid fallback。

## Metrics instance id

`PERAGO_INSTANCE_ID` 是 metrics 用的 Perago runtime instance 身份，不是 worker process 身份。它应该由 operator 或部署系统提供，并保持低基数；Nomad 部署可以由 job/group/allocation 生成稳定短标识后注入。

`runtime.busy_slots` 使用 `task_name` 和 `perago_instance_id` 作为 labels。process mode 中，busy slots 只由 broker 汇总 executor slot state 后导出一次；executor process 和 supervisor parent 不导出这个指标。thread mode 中，由运行 `TaskRunner` 的 worker runtime owner 导出一次。多个 Perago instance 跑同一个 Task Worker 时，dashboard 应按 `task_name` 聚合：

```text
sum by (task_name) (runtime_busy_slots)
```

## 命令差异

`perago check` 会验证配置、task module 和生成 TaskDef 的基本结构，且不连接 Conductor 或 LakeFS。

`perago extract` 会验证配置和 task module，然后写出 TaskDef JSON。它同样不要求 Conductor 和 LakeFS 必须已经配置完成。

`perago start` 会额外要求：

- `CONDUCTOR_SERVER_URL` 已配置。
- workspace task 需要 LakeFS endpoint、access key id 和 secret access key 已完整配置；workspace-free task 不需要 LakeFS 连接变量。
- metrics-enabled task 需要 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`；如果 `MetricSpec.worker_capacity=True`，还需要 `PERAGO_INSTANCE_ID`。
- Conductor 中已经注册了对应 TaskDef。

推荐流程是先运行 `perago check`，再运行 `perago extract` 并注册 TaskDef，最后启动 worker。
