# CLI

Perago CLI 面向单个 task module。三个命令都接收 Python import path 形式的 `module_target`，例如 `app.workers.features_build`；不要传 `.py` 文件路径，也不要把多个 task worker 放进同一个 module。

CLI 会先加载 runtime config，再导入 task module。这个顺序让本机目录、worker id 前缀和服务连接配置问题先暴露，避免配置错误被 task definition 错误掩盖。

## 命令总览

| 命令 | 输入 | 输出 | 是否连接外部服务 |
| --- | --- | --- | --- |
| `perago check <module_target>` | 单个 task module import path | 校验摘要和配置状态 | 不连接 Conductor 或 LakeFS |
| `perago extract <module_target> --output <path.json>` | 单个 task module import path 和 JSON 输出路径 | 生成的 TaskDef JSON 文件路径 | 不连接 Conductor 或 LakeFS |
| `perago start [-j N] <module_target>` | 单个 task module import path 和 worker 进程数 | 前台 supervisor 进程 | 启动前检查 Conductor TaskDef，运行时连接 Conductor 和 LakeFS |

`perago check` 和 `perago extract` 是开发与发布前验证命令；`perago start` 是长运行 worker 入口。

## `perago check`

`check` 用来验证本机 runtime config、task module 导入、task contract 和 TaskDef 生成形状：

```bash
PYTHONPATH=tests/fixtures perago check app.workers.features_build
```

成功输出包含任务名、本机目录、worker id 前缀和服务配置状态：

```text
ok: features.build
workspace_root: /var/folders/.../perago/workspaces
log_root: /var/folders/.../perago/logs
worker_id_prefix: appworkersfeaturesbuild
conductor: not configured
lakefs: not configured
```

`conductor: not configured` 和 `lakefs: not configured` 对 `check` 不是失败条件。它们只说明当前进程环境还不足以启动 worker。

`check` 会拒绝以下形状：

- runtime config 无法解析或本机目录不可写。
- module target 不是 Python import path。
- module 没有 task，或定义了多个 task。
- task function 签名、Pydantic schema、WorkspaceSpec、guardrail 或 TaskControls 不合法。
- TaskDef JSON Schema 无法从 Pydantic model 生成。

错误统一写到 stderr，并以 `error: ...` 开头：

```text
error: module target must be a Python import path, not a file path
```

## `perago extract`

`extract` 在通过同一套校验后，把 generated Conductor TaskDef 写到指定 JSON 文件：

```bash
PYTHONPATH=tests/fixtures perago extract app.workers.features_build --output generated/features.build.json
```

短参数等价：

```bash
PYTHONPATH=tests/fixtures perago extract app.workers.features_build -o generated/features.build.json
```

成功时 stdout 只输出写入路径，便于脚本捕获：

```text
generated/features.build.json
```

`--output` / `-o` 是 required。输出路径必须以 `.json` 结尾；如果父目录不存在，Perago 会创建父目录。不要把输出路径写成目录：

```text
error: output must be a JSON file path, for example generated/features.build.json
```

`extract` 生成的是 Conductor TaskDef，不是运行时 input payload。它不会连接 Conductor，也不会注册 TaskDef；注册动作仍由部署流程或 Conductor 管理工具完成。

## `perago start`

`start` 启动 supervisor，并由 supervisor 管理一个或多个 worker child process：

```bash
PYTHONPATH=tests/fixtures perago start app.workers.features_build -j 2
```

`-j` 是 worker process count，默认 `1`，最小值为 `1`。每个 child process 会得到独立的 `PERAGO_WORKER_ID`：

```text
PERAGO_WORKER_ID=appworkersfeaturesbuild0001
PERAGO_WORKER_ID=appworkersfeaturesbuild0002
```

启动前，`start` 会额外要求：

- `CONDUCTOR_SERVER_URL` 已配置。
- LakeFS endpoint、access key id 和 secret access key 已完整配置。
- module 能导入并生成 TaskDef。
- Conductor 中已经注册了同名 TaskDef。

如果 Conductor 中缺少 TaskDef，命令会失败并提示先生成和注册：

```text
error: Conductor TaskDef 'features.build' is not registered; run perago extract and register it before start
```

运行时，supervisor 会先清理遗留 attempt workspace 并启动后台 workspace GC loop；每个 worker child 只准备自己的日志文件，然后进入 broker poll loop 或 executor assignment loop。child process 退出时，supervisor 会按递增 backoff 重启；收到 `SIGINT` 或 `SIGTERM` 时，supervisor 会请求停止并等待 worker drain。只有配置了 `PERAGO_SHUTDOWN_FORCE_KILL_AFTER` 时，超过 deadline 仍未退出的 child 才会被 `kill()`。

## 推荐发布前流程

先在本地验证 task module：

```bash
PYTHONPATH=tests/fixtures perago check app.workers.features_build
```

再生成 TaskDef JSON：

```bash
PYTHONPATH=tests/fixtures perago extract app.workers.features_build --output generated/features.build.json
```

确认 TaskDef 已注册到 Conductor 后，再启动 worker：

```bash
PYTHONPATH=tests/fixtures perago start app.workers.features_build -j 2
```

如果只是在开发 task body 或 schema，停在 `check` 和 `extract` 即可；不要用 `start` 代替本地校验，因为它需要真实 Conductor 和 LakeFS runtime config。
