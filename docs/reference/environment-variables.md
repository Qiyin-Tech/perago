# Environment Variables

本页提供 Perago 运行时环境变量的精确参考。任务作者通常只需要阅读
`runtime/configuration` 理解配置流程；排查启动失败、部署变量和本机目录问题时，以本页表格为准。

Perago 只读取当前工作目录下的 `.env` 和进程环境变量。合并顺序是：

```text
.env values < process environment values
```

也就是说，`.env` 只能提供默认值，shell、部署系统或 supervisor 注入的进程环境变量会覆盖同名 `.env` 值。

## 连接变量

| 变量 | 状态 | 默认值 | 读取位置 | 校验和说明 |
| --- | --- | --- | --- | --- |
| `CONDUCTOR_SERVER_URL` | required for `perago start`; optional for `check`/`extract` | 无 | `RuntimeConfig.conductor.server_url` | 空值表示未配置；值会去除前后空白。`replace-me` 会被拒绝。 |
| `LAKECTL_SERVER_ENDPOINT_URL` | required for workspace-task `perago start`; optional for workspace-free `start` and `check`/`extract` | 无 | `RuntimeConfig.lakefs.endpoint_url` | LakeFS 三个变量必须全部配置或全部省略；缺任意一个都会报 `LakeFS config is incomplete`。 |
| `LAKECTL_CREDENTIALS_ACCESS_KEY_ID` | required for workspace-task `perago start`; optional for workspace-free `start` and `check`/`extract` | 无 | `RuntimeConfig.lakefs.access_key_id` | 空值表示未配置；`replace-me` 会被拒绝。 |
| `LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY` | required for workspace-task `perago start`; optional for workspace-free `start` and `check`/`extract` | 无 | `RuntimeConfig.lakefs.secret_access_key` | 不会在 `perago check` 的配置状态输出中打印。`replace-me` 会被拒绝。 |

Perago 目前不会把 Conductor auth key、Conductor auth secret 或 LakeFS 配置写入 Conductor TaskDef，也不会把这些值放入 task input/output。它们是 worker-local runtime config。

## 本机运行变量

| 变量 | 状态 | 默认值 | 读取位置 | 校验和说明 |
| --- | --- | --- | --- | --- |
| `PERAGO_WORKSPACE_ROOT` | optional | 平台临时目录下的 `perago/workspaces` | `RuntimeConfig.workspace_root` | attempt-local workspace 根目录。默认会探测目录是否可创建、可写。`perago start` 会用 `.perago-supervisor.lock` 要求一个 running supervisor 独占一个 root。 |
| `PERAGO_LOG_ROOT` | optional | 平台临时目录下的 `perago/logs` | `RuntimeConfig.log_root` | worker JSONL 日志根目录。默认会探测目录是否可创建、可写。 |
| `PERAGO_LOG_FILE_MAX_SIZE` | optional | `100MB` | `RuntimeConfig.log_file_max_size` | 接受正数加 `KB`、`MB` 或 `GB`，大小写不敏感，例如 `512KB`、`100MB`、`1.5GB`。裸数字、`0MB` 和无效单位会被拒绝。 |
| `PERAGO_LOG_RETENTION` | optional | `30d` | `RuntimeConfig.log_retention` | 接受正整数加 `d`，大小写不敏感，例如 `7d` 或 `30D`。`0d` 和其他单位会被拒绝。 |
| `PERAGO_WORKER_ID_PREFIX` | optional | 从 module target 删除非字母数字字符后派生 | `RuntimeConfig.worker_id_prefix` | 只能包含 ASCII 字母和数字。supervisor 使用它派生 broker worker id 和 executor `PERAGO_WORKER_ID`。 |
| `PERAGO_WORKER_ID` | generated / debug-only | supervisor 生成；非 supervisor 调试进程退回到 `<module-target-prefix>-pid-<pid>` | worker runtime identity | `perago start -j` 为 broker 和每个 executor child process 写入该值。常规部署不应在 `.env` 中配置。 |
| `PERAGO_EXECUTION_MODE` | optional | `process` | `RuntimeConfig.execution_mode` | 接受 `process` 或 `thread`，大小写不敏感。CLI `perago start --execution-mode ...` 会覆盖该环境变量。`thread` 使用 SDK `TaskRunner` 在单进程内执行；默认 `process` 使用单 broker + N executor IPC 模型。 |
| `PERAGO_FAILURE_REASON_MAX_LENGTH` | optional | `500` | `RuntimeConfig.failure_reason_max_length` | 失败 task 写入 Conductor `reasonForIncompletion` 的最大字符数。只接受正整数；超长 reason 会截断并在 worker JSONL 日志中记录原始长度和上限，不记录完整原文。 |
| `PERAGO_WORKSPACE_GC_TTL` | optional | `24h` | `RuntimeConfig.workspace_gc_ttl` | 接受正整数加 `s`、`m`、`h` 或 `d`，例如 `30m`、`24h`。supervisor 周期 GC 只会删除超过该年龄且不属于活跃 owner 的 attempt workspace。 |
| `PERAGO_WORKSPACE_GC_INTERVAL` | optional | `1h` | `RuntimeConfig.workspace_gc_interval` | 接受正整数加 `s`、`m`、`h` 或 `d`。控制 supervisor 后台 workspace GC loop 的运行间隔。 |
| `PERAGO_SHUTDOWN_FORCE_KILL_AFTER` | optional | unset | `RuntimeConfig.shutdown_force_kill_after` | 接受正整数加 `s`、`m`、`h` 或 `d`，例如 `30s`。未配置时 Perago shutdown 只 drain 并等待 child 自然退出，不调用 `process.kill()`；配置后超过 deadline 的 child 会被 kill。 |

## `.env` 解析规则

`.env` 解析是有意保持简单的：

- 支持 `KEY=value`。
- 支持 `export KEY=value`。
- 支持用单引号或双引号包裹整个值。
- 忽略空行、注释行和没有 `=` 的行。
- 不做 shell 展开、变量插值或转义解释。

示例：

```text
export PERAGO_LOG_FILE_MAX_SIZE=512KB
PERAGO_WORKSPACE_ROOT='/tmp/perago/workspaces'
PERAGO_LOG_ROOT="/tmp/perago/logs"
PERAGO_WORKER_ID_PREFIX=localWorker
PERAGO_EXECUTION_MODE=process
PERAGO_FAILURE_REASON_MAX_LENGTH=500
PERAGO_WORKSPACE_GC_TTL=24h
PERAGO_WORKSPACE_GC_INTERVAL=1h
PERAGO_SHUTDOWN_FORCE_KILL_AFTER=30s
```

## 命令要求

| 命令 | 连接变量要求 | 会探测本机目录吗 | 说明 |
| --- | --- | --- | --- |
| `perago check` | 否 | 是 | 可在没有 Conductor/LakeFS 的本机环境中检查 task module 和配置。 |
| `perago extract` | 否 | 是 | 可生成 TaskDef JSON；连接变量不会写入 TaskDef。 |
| `perago start` | `CONDUCTOR_SERVER_URL` 必须配置；LakeFS 三个变量只对 workspace task 必须配置 | 是 | 启动前会导入 task module；workspace-free task 不需要 LakeFS 连接变量。 |

如果只配置了部分 LakeFS 变量，三个命令都会在加载 runtime config 阶段失败；这是为了避免部署环境带着半套连接配置继续运行。

`perago start` 还会在 `PERAGO_WORKSPACE_ROOT` 下创建 `.perago-supervisor.lock`，锁内容包含当前 supervisor pid。已有活 pid 锁时，启动会失败并提示为每个 supervisor 使用不同的 `PERAGO_WORKSPACE_ROOT`；崩溃遗留的死 pid 锁会在启动时被替换。

## 常见错误文本

| 错误文本 | 触发条件 | 修复 |
| --- | --- | --- |
| `CONDUCTOR_SERVER_URL is required for perago start` | 启动 worker 时未配置 Conductor endpoint。 | 在 `.env` 或进程环境中配置真实 `CONDUCTOR_SERVER_URL`。 |
| `LakeFS config is required for workspace tasks` | 启动 workspace task worker 时三个 LakeFS 变量都未配置。 | workspace task 需要同时配置 LakeFS endpoint、access key id 和 secret access key；workspace-free task 不需要这些变量。 |
| `LakeFS config is incomplete; missing ...` | LakeFS 三个变量只配置了一部分。 | 同时配置 endpoint、access key id 和 secret access key；如果当前命令和 task 类型不需要 LakeFS，就三个变量全部省略。 |
| `<NAME> must be replaced with a real value` | 连接变量仍是 `replace-me`。 | 用真实部署值替换 `.env.example` 的占位值。 |
| `PERAGO_LOG_FILE_MAX_SIZE must be a positive size ...` | 日志文件大小格式无效。 | 使用正数和 `KB`、`MB` 或 `GB` 单位，例如 `512KB`、`100MB`、`1.5GB`。 |
| `PERAGO_LOG_RETENTION must be a positive day count ...` | 日志保留期格式无效。 | 使用 `7d`、`30d` 这类格式。 |
| `PERAGO_WORKER_ID_PREFIX must contain only ASCII letters and digits` | worker id prefix 含有连字符、下划线、点号或非 ASCII 字符。 | 改成只含字母和数字的前缀，例如 `prodAFeaturesBuild`。 |
| `PERAGO_EXECUTION_MODE must be either 'process' or 'thread'` | execution mode 超出支持范围。 | 使用默认 `process`，或显式设置为 `thread`。 |
| `PERAGO_FAILURE_REASON_MAX_LENGTH must be a positive integer` | failure reason 长度上限不是整数。 | 使用正整数，例如 `500` 或 `1200`。 |
| `PERAGO_FAILURE_REASON_MAX_LENGTH must be greater than zero` | failure reason 长度上限为 `0`。 | 使用大于零的整数。 |
| `PERAGO_WORKSPACE_GC_TTL must be a positive duration ...` | workspace GC TTL 格式非法。 | 使用 `30m`、`1h`、`24h` 这类正数 duration。 |
| `PERAGO_WORKSPACE_GC_INTERVAL must be a positive duration ...` | workspace GC interval 格式非法。 | 使用 `30s`、`5m`、`1h` 这类正数 duration。 |
| `PERAGO_SHUTDOWN_FORCE_KILL_AFTER must be a positive duration ...` | shutdown force-kill deadline 格式非法。 | 使用 `30s`、`5m`、`1h` 这类正数 duration，或不配置该变量。 |
