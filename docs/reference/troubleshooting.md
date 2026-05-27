# Troubleshooting

本页按报错入口定位 Perago 问题。优先用 `perago check` 复现 task module 和本机配置问题；`perago check` 通过后，再排查 `perago extract`、TaskDef 注册和 `perago start` 的运行时问题。

## Triage Flow

从仓库根目录按这个顺序缩小问题范围。下面使用仓库内测试 fixture；真实部署时把 `app.workers.features_build` 换成自己的 task module target。

```bash
PYTHONPATH=tests/fixtures uv run perago check app.workers.features_build
PYTHONPATH=tests/fixtures uv run perago extract app.workers.features_build --output generated/features.build.json
PYTHONPATH=tests/fixtures uv run perago start app.workers.features_build -j 1
```

| 停在哪一步 | 主要问题范围 | 下一步 |
| --- | --- | --- |
| `check` 失败 | task module、Pydantic contract、WorkspaceSpec、TaskControls 或本机 runtime config | 先看 stderr 的 `error: ...`；修正代码或 `.env` 后重跑 `check`。 |
| `extract` 失败 | `check` 覆盖的范围，或输出路径缺少 `.json` 后缀 | 确认输出路径指向 JSON 文件。 |
| `start` 失败 | Conductor/LakeFS 连接变量、Conductor TaskDef 注册、worker supervisor 启动前检查 | 先确认 `.env` 完整，再确认 TaskDef 已注册到 Conductor。 |
| attempt 运行中失败 | Conductor input、LakeFS workspace、Workspace Check、task body、publish fence 或 cleanup | 结合 Conductor result status、`reasonForIncompletion` 和 worker JSONL 日志排查。 |

## Task Module Cannot Load

| 错误文本 | 常见原因 | 修复 |
| --- | --- | --- |
| `module target must be a Python import path, not a file path or object path` | CLI target 写成 `app/workers/foo.py`、`module:app` 或空值。 | 使用 Python module import path，例如 `app.workers.features_build`。 |
| `<module> does not declare a Perago task` | module 导入成功，但没有执行任何 `@task(...)` decorator。 | 确认文件中 exactly one function 被 `@task(name=..., owner_email=...)` 装饰。 |
| `<module> declares more than one Perago task` | 一个 Python module 中注册了多个 Perago task。 | 拆成多个 single-task module；不要用 `--task` 或 app registry 在同一文件中选择任务。 |

Perago 的 worker 单元是 exactly one task per Python module。一个 worker process 只服务这个 task name。

## Task Definition Errors

| 错误文本 | 常见原因 | 修复 |
| --- | --- | --- |
| `task name is required` | `@task(name=...)` 为空字符串。 | 配置非空 task name。 |
| `task name must not contain path separators` | task name 含 `/` 或 `\`。 | task name 用 Conductor task 名称，不要用路径。 |
| `owner_email is required` | `owner_email` 为空字符串。 | 填写负责人的邮箱或内部 owner 标识。 |
| `unsupported task decorator fields: ...` | `@task(...)` 使用了未支持的 keyword。 | 只使用 `name`、`owner_email`、`description`、`workspace`、`controls`。 |
| `workspace must be a WorkspaceSpec` | `workspace=` 传入了 dict 或其他对象。 | 使用 `WorkspaceSpec(...)`。 |
| `controls must be a TaskControls` | `controls=` 传入了 dict 或其他对象。 | 使用 `TaskControls(...)` 和嵌套 policy model。 |
| `publish_budget requires workspace=WorkspaceSpec(...)` | workspace-free task 配置了 publish budget。 | 只在 workspace task 上配置 `TaskControls(publish_budget=...)`。 |
| `WorkspaceSpec(read_only=True) disables workspace publication; TaskControls.publish_budget is ignored.` | read-only workspace task 配置了 publish budget。 | 这是启动/校验阶段 warning；删除 `publish_budget`，或把 task 改为可写 workspace task。 |

这类错误在 module import 或 `build_taskdef(...)` 阶段暴露；`perago check` 会先加载 runtime config，再导入 task module。

## Task Function Shape

| 错误文本 | 常见原因 | 修复 |
| --- | --- | --- |
| `task function must be a synchronous function` | task body 定义为 `async def`。 | 改成普通 `def`；MVP worker 不运行 async task function。 |
| `task function must not use *args, **kwargs, or keyword-only fields` | 签名使用 variadic 或 keyword-only 参数。 | 使用精确签名 `(workspace: Path, params: ParamsModel)` 或 `(params: ParamsModel)`。 |
| `task function parameters must not declare defaults` | `params` 或 `workspace` 参数有默认值。 | 删除默认值；required/optional 字段只在 Pydantic model 内表达。 |
| `workspace task parameters must be named workspace and params` | workspace task 的两个参数名不匹配。 | 顺序和名称都写成 `workspace, params`。 |
| `workspace must be annotated as pathlib.Path` | `workspace` 未标注为 `Path` 或使用了其他类型。 | 使用 `from pathlib import Path` 并标注 `workspace: Path`。 |
| `workspace task functions require workspace=WorkspaceSpec(...)` | 函数签名像 workspace task，但 decorator 没声明 workspace。 | 在 `@task(...)` 里加入 `workspace=WorkspaceSpec(...)`。 |
| `workspace-free task parameter must be named params` | workspace-free task 的唯一参数不叫 `params`。 | 把唯一参数命名为 `params`。 |
| `workspace-free task functions must not declare workspace=WorkspaceSpec(...)` | 函数只有 `params`，但 decorator 声明了 workspace。 | 删除 `workspace=...`，或把函数改成 workspace task 签名。 |
| `params must be annotated as a Pydantic BaseModel subclass` | `params` 缺少类型注解，或类型未继承 Pydantic `BaseModel`。 | 定义 `class Params(BaseModel): ...` 并标注 `params: Params`。 |
| `return value must be annotated as a Pydantic BaseModel subclass` | 返回值缺少类型注解，或类型未继承 Pydantic `BaseModel`。 | 定义 output model 并标注 `-> Output`。 |

不要把业务字段展开成多个函数参数。函数签名只声明 Perago contract；业务 required/optional 字段属于 Pydantic `Params` 和 `Output` model。

## WorkspaceSpec And Workspace Checks

| 错误文本 | 常见原因 | 修复 |
| --- | --- | --- |
| `WorkspaceSpec.prefix must use '/' separators` | prefix 使用了 Windows 反斜杠。 | 使用 POSIX 风格 `/`。 |
| `WorkspaceSpec.prefix must stay inside the repository` | prefix 含 `..`、`.` 或空 segment。 | 使用 `/` 或 repository 内的相对 prefix，例如 `datasets/raw`。 |
| read-only task 写了本机 workspace 但没有发布 | `WorkspaceSpec(read_only=True)` 禁止 workspace publication。 | 这是预期行为；写入会随 attempt-local cleanup 丢弃。 |
| `workspace guardrail paths must not be empty` | `require_file("")` 或类似空路径。 | 填写 workspace-prefix 内的相对路径。 |
| `guardrail paths must be relative to WorkspaceSpec(prefix=...)` | guardrail path 以 `/` 开头。 | 删除开头 `/`，路径相对于 `WorkspaceSpec.prefix`。 |
| `absolute guardrail paths are not allowed` | 使用了绝对 `Path`。 | 使用 workspace-relative path。 |
| `drive-qualified or absolute guardrail paths are not allowed` | 使用 Windows drive path。 | 使用不含 drive/root 的相对路径。 |
| `string guardrail paths must use '/' separators` | 字符串路径使用 `\`。 | 使用 `/`。 |
| `guardrail min_count must be <= max_count` | `require_glob(..., min_count=5, max_count=2)`。 | 调整 count bounds。 |

运行中 Workspace Check 失败会进入 `reasonForIncompletion`：

| 错误文本 | 状态 | 含义 |
| --- | --- | --- |
| `pre guardrail require_file('...') did not find a file` | `FAILED_WITH_TERMINAL_ERROR` | 输入 workspace 缺少 task 声明的必需文件。 |
| `pre guardrail require_glob('...') matched 0 files; min_count=1` | `FAILED_WITH_TERMINAL_ERROR` | 输入 workspace glob 数量不足。 |
| `post guardrail forbid_glob('...') matched N files` | `FAILED` | task body 产出的 workspace 文件未通过 post check。 |

pre check 和任务显式抛出的 `TaskTerminalError` 会映射为 `FAILED_WITH_TERMINAL_ERROR`。post check、`TaskFailed`、未知业务异常和 publish 失败都会映射为普通 `FAILED`。

## Runtime Configuration

| 错误文本 | 常见原因 | 修复 |
| --- | --- | --- |
| `CONDUCTOR_SERVER_URL is required for perago start` | `perago start` 没有 Conductor endpoint。 | 在 `.env` 或进程环境配置真实 `CONDUCTOR_SERVER_URL`。 |
| `LakeFS config is required for perago start` | 三个 LakeFS 变量都没配置。 | 配置 `LAKECTL_SERVER_ENDPOINT_URL`、`LAKECTL_CREDENTIALS_ACCESS_KEY_ID`、`LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY`。 |
| `LakeFS config is incomplete; missing ...` | 只配置了部分 LakeFS 变量。 | 三个 LakeFS 变量同时配置，或在非 `start` 调试时全部省略。 |
| `<NAME> must be replaced with a real value` | 连接变量仍是 `replace-me`。 | 用真实部署值替换占位值。 |
| `<path> is not writable: ...` | workspace/log root 不可创建或不可写。 | 修正 `PERAGO_WORKSPACE_ROOT` 或 `PERAGO_LOG_ROOT` 的目录权限。 |
| `PERAGO_LOG_FILE_MAX_SIZE must be a positive size ...` | 日志大小格式错误。 | 使用 `512KB`、`100MB` 或 `1.5GB`。 |
| `PERAGO_LOG_RETENTION must be a positive day count ...` | 日志保留期格式错误。 | 使用 `7d` 或 `30d`。 |
| `PERAGO_WORKER_ID_PREFIX must contain only ASCII letters and digits` | worker id prefix 含连字符、下划线、点号或非 ASCII 字符。 | 使用只含字母数字的前缀，例如 `prodAFeaturesBuild`。 |
| `PERAGO_WORKSPACE_GC_TTL must be a positive duration ...` | workspace GC TTL 格式错误。 | 使用 `30m`、`1h`、`24h` 这类正数 duration。 |
| `PERAGO_WORKSPACE_GC_INTERVAL must be a positive duration ...` | workspace GC interval 格式错误。 | 使用 `30s`、`5m`、`1h` 这类正数 duration。 |
| `PERAGO_SHUTDOWN_FORCE_KILL_AFTER must be a positive duration ...` | shutdown force-kill deadline 格式错误。 | 使用 `30s`、`5m`、`1h` 这类正数 duration，或不配置该变量。 |

`perago check` 和 `perago extract` 不要求 Conductor/LakeFS 连接变量完整，但会拒绝半套 LakeFS 配置和不可写本机目录。

## TaskDef Registration

| 错误文本 | 常见原因 | 修复 |
| --- | --- | --- |
| `output must be a JSON file path` | `perago extract --output` 指向目录或非 `.json` 文件。 | 指向具体 JSON 文件，例如 `generated/features.build.json`。 |
| `Conductor TaskDef '<name>' is not registered; run perago extract and register it before start` | `perago start` 前 Conductor 中没有同名 TaskDef。 | 运行 `perago extract` 生成 TaskDef JSON，并用部署流程注册到 Conductor。 |
| `failed to validate Conductor TaskDef: ...` | 启动前连接 Conductor 或查询 TaskDef 失败。 | 检查 `CONDUCTOR_SERVER_URL`、网络、认证和 Conductor 服务状态。 |

`perago extract` 只生成 TaskDef JSON，不会自动写入 Conductor。`perago start` 在启动 supervisor 前会真实检查 TaskDef 是否已注册。

## Attempt Input And Result Validation

| 错误文本 | 常见原因 | 修复 |
| --- | --- | --- |
| `workspace task input must contain only workspace and params` | workspace task input 顶层字段缺失或多了其他字段。 | input 顶层只保留 `workspace` 和 `params`。 |
| `workspace-free task input must contain only params` | workspace-free task input 顶层包含 `workspace` 或其他字段。 | input 顶层只保留 `params`。 |
| `workspace repository, branch, and ref must not be blank` | `WorkspaceInput` 的 repository、branch 或 ref 是空白字符串。 | 填写非空 LakeFS repository、target branch 和 input commit ref。 |
| `Extra inputs are not permitted` | Pydantic input/result model 收到未声明字段，或 control object 有未知字段。 | 删除额外字段；扩展 contract 必须先改 Pydantic model。 |
| `Input should be ...` | Pydantic 字段类型不匹配。 | 按 generated TaskDef schema 和 Pydantic model 修正字段类型。 |

Workspace task 的 `workspace` 是平铺的 `repository`、`branch`、`ref_type`、`ref` 四元组；LakeFS endpoint、credentials、`WorkspaceSpec.prefix` 和 `WorkspaceSpec.read_only` 不属于 workflow input。

## Workspace Sync And Publication

| 错误文本或现象 | 常见原因 | 修复 |
| --- | --- | --- |
| `workspace publication does not support symlinks: ...` | attempt-local workspace 中包含 symlink。 | 输出真实文件；不要把 symlink 发布到 LakeFS。 |
| `cannot publish from input ref <input-ref>; target branch <branch> current head is <current-head>` | diff 非空 publication 前，target branch HEAD 既不是 input ref，也不是 input ref 的直接子提交。 | 不要手动重试同一 attempt；从当前 target branch head 发起新的 workflow。 |
| `cannot complete no-op from input ref <input-ref>; target branch <branch> current head is <current-head>` | 可写 no-op completion 前，target branch HEAD 不符合 `HEAD == input_ref` 或 `parent(HEAD) == input_ref` 协议。 | 不要把当前 no-op attempt 包装成成功；从当前 target branch head 重新发起 workflow。 |
| 可写 task 没有产生新 commit | `read_only=False` 但 workspace diff 为空。 | 这是 no-op completion；Perago 不会创建 empty commit，output ref 保持 input ref。 |
| LakeFS download/stage/merge SDK 异常 | repository/ref 不存在、凭证错误、网络失败或 LakeFS 服务异常。 | 检查 LakeFS 连接变量、repository、input commit 和 worker JSONL。 |
| staging branch create 失败且 branch 已存在 | 同一个 execution id 的 staging branch 已存在，或远端残留与当前 execution id 冲突。 | 当前 attempt 会 fail closed。正常情况下每次 execution 都有唯一 branch；排查 worker 日志中的 `execution_id`，必要时清理残留 `perago-staging-...-exec-...` branch。 |
| `failed to clean staging workspace` | staging branch 删除失败。 | 原始 task result 不会被覆盖；事后检查并清理 `perago-staging-...` branch。 |
| `failed to clean attempt-local workspace` | 本机 attempt workspace 删除失败。 | 原始 task result 不会被覆盖；检查 `PERAGO_WORKSPACE_ROOT` 权限并清理带 `.perago-attempt.json` marker 的目录。 |

publish fence 和 LakeFS merge 失败会让 attempt `FAILED`，不会生成 workspace output。cleanup 失败只写日志，不回滚已经完成的 target branch merge。publish 成功但 Conductor completion 未上报时，Perago 不补发旧 completion；由 Conductor timeout/fail/retry 处理。

## Where To Look Next

| 需要核对 | 页面 |
| --- | --- |
| Conductor input/output JSON shape | [Input/Output Contract](input-output-contract.md) |
| generated TaskDef 字段、默认值和 `None` 省略规则 | [Conductor TaskDef](conductor-taskdef.md) |
| 环境变量、`.env` 解析和配置错误 | [Environment Variables](environment-variables.md) |
| result status 和 failure mapping | [Failure Classification](failure-classification.md) |
| worker JSONL 日志路径和排查入口 | [Runtime Logging](../runtime/logging.md) |
| LakeFS download/stage/publish/cleanup 生命周期 | [LakeFS Runtime](../runtime/lakefs.md) |
