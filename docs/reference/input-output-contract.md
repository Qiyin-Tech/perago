# Input/Output Contract

本页定义 Perago worker 从 Conductor 读取的 `inputData` 结构，以及 Perago 回写到 Conductor 的 task result 结构。这里的 contract 描述运行时边界；业务 `params` 和 `result` 的字段仍然来自 task module 函数签名中的 Pydantic model。

## Workspace Task Input

Workspace task 的 Conductor `inputData` 顶层必须包含 `workspace` 和 `params`。其他顶层字段会被忽略；例如 Conductor dynamic node 追加的 `toExecute` 不会传给 task body。

| 字段 | 状态 | 来源 | 说明 |
| --- | --- | --- | --- |
| `workspace` | required | Workflow 传入 | `WorkspaceInput`，指向要下载的 LakeFS repository、target branch 和 immutable input commit。 |
| `params` | required | Workflow 传入 | 业务参数对象，按 task 函数的 `params` Pydantic model 校验。 |

```json
{
  "workspace": {
    "repository": "song-000123",
    "branch": "main",
    "refType": "commit",
    "ref": "589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3"
  },
  "params": {
    "feature_set": "default",
    "min_rows": 100
  }
}
```

`workspace` 内部字段边界如下。

| 字段 | 状态 | 说明 |
| --- | --- | --- |
| `repository` | required | LakeFS repository 名称，不能为空字符串。 |
| `branch` | required | 可写 workspace task 成功发布时要推进的目标 branch，不能为空字符串。 |
| `refType` | required | 目前只接受 `"commit"`。 |
| `ref` | required | 本次 attempt 下载的 immutable input commit，不能为空字符串。 |

LakeFS endpoint、access key、secret key、workspace prefix 和 guardrail 都不属于 Conductor input。连接信息来自 worker 本地配置；prefix 与 guardrail 来自 task module 的 `WorkspaceSpec(...)`。

## Workspace-Free Task Input

Workspace-free task 的 Conductor `inputData` 顶层必须包含 `params`。其他顶层字段会被忽略。

| 字段 | 状态 | 来源 | 说明 |
| --- | --- | --- | --- |
| `params` | required | Workflow 传入 | 业务参数对象，按 task 函数的 `params` Pydantic model 校验。 |

```json
{
  "params": {
    "song_id": "song-000123",
    "min_duration_seconds": 30
  }
}
```

不要把 `params` 展开到顶层。Perago 只读取 schema 要求的顶层字段，不会把其他顶层数据传给业务函数。

## Business Model Validation

Perago 在调用业务函数前使用 task 的 Pydantic `params` model 校验 `params`。默认 `@task(strict_params=False)`，允许输入是 schema 的超集：所有层级的额外字段都会被忽略，业务函数只收到 schema 声明的数据。

如果 task 必须拒绝任何额外业务字段，使用 `@task(..., strict_params=True)`。该模式会对顶层和嵌套 Pydantic object 强制 `extra="forbid"`。这个开关不影响 Conductor `inputData` 顶层的宽松读取，也不改变始终严格的 `workspace` 和 `result` 校验。默认值属于 Pydantic schema 与运行时模型校验的一部分，但 Perago 不生成 Conductor `inputTemplate`，也不会把默认值复制进 Conductor task input。

## Completed Output

Perago 在 task 成功完成时将业务结果写入 `outputData.result`。`RuntimeTaskResult.conductor_payload()` 的完成载荷如下：

```json
{
  "status": "COMPLETED",
  "output": {
    "workspace": {
      "repository": "song-000123",
      "branch": "main",
      "refType": "commit",
      "ref": "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca"
    },
    "result": {
      "row_count": 100,
      "feature_count": 24
    }
  }
}
```

Workspace task 的 `output` 顶层字段必须包含：

| 字段 | 状态 | 来源 | 说明 |
| --- | --- | --- | --- |
| `workspace` | generated | Perago runtime | `WorkspaceOutput`，保留 input 的 repository/branch；read-only 或 no-op completion 可保留 input ref，可写 publication 会替换为成功发布后的 commit。 |
| `result` | generated from task return | 业务函数返回值 | 按 task 函数 return annotation 的 Pydantic model 校验和序列化。 |

Workspace-free task 的完成输出只包含 `result`。

```json
{
  "status": "COMPLETED",
  "output": {
    "result": {
      "valid": true,
      "reason": null
    }
  }
}
```

`result` 也会强制 `extra="forbid"` 校验。业务函数可以返回对应 Pydantic model 实例，也可以返回可被该 model 校验的 mapping。

## Failed Output

失败必须包含 `reasonForIncompletion`，并可携带任务作者显式提供的 JSON metadata。`raise TaskFailed(reason, output_data=payload)` 和 `raise TaskTerminalError(reason, output_data=payload)` 将 payload 直接写入 Conductor `outputData`；`RuntimeTaskResult` 内部字段名仍为 `output`。默认 `output_data=None` 保留仅 status/reason 的接口，`{}` 保留空输出。

`reason` 是必填字符串，按 `PERAGO_FAILURE_REASON_MAX_LENGTH`（默认 500 字符）截断；该限制不截断 metadata。output_data 必须是 JSON object，嵌套值允许字符串、布尔、整数、有限浮点数、null、数组和字符串键 object。异常构造时复制原对象；任意 Python 对象、非字符串键、非有限浮点数和循环容器会被拒绝。业务作者负责内容脱敏，Perago 不解析其 code 或 retryable 字段。

失败 metadata 不按成功返回值 model 校验，不被包装为 `result`，也不修改成功 TaskDef output schema。失败状态仍由所抛异常决定，workspace task 不会因 metadata 进入 stage/publish 路径。未处理异常、参数/结果校验和 guardrail 等运行时失败保持原状态规则，不自动生成业务错误对象。

```json
{
  "status": "FAILED",
  "reasonForIncompletion": "workspace task input must contain workspace and params"
}
```

```json
{
  "status": "FAILED_WITH_TERMINAL_ERROR",
  "reasonForIncompletion": "pre guardrail require_glob('raw/**/*.parquet') matched 0 files; min_count=1"
}
```

| 状态 | 输出字段 | 典型来源 |
| --- | --- | --- |
| `COMPLETED` | `output` required, `reasonForIncompletion` forbidden | 业务函数成功、post guardrail 通过；workspace task 已完成 read-only、no-op 或 publication 路径。 |
| `FAILED` | `reasonForIncompletion` required, `output` optional（显式任务失败的 JSON metadata） | 输入结构错误、Pydantic 校验失败、`TaskFailed`、未知业务异常、post guardrail、attempt fence 或 publish fence 失败。 |
| `FAILED_WITH_TERMINAL_ERROR` | `reasonForIncompletion` required, `output` optional（显式任务失败的 JSON metadata） | pre guardrail 失败或 `TaskTerminalError`，表示同一 input 自动重试没有意义。 |

## Input Wrapper And Output Shapes

Perago 的运行时入口先检查 required 顶层字段，再校验 Pydantic payload。Input 顶层允许额外字段，completed output 仍由 Perago 严格生成。

| Task 类型 | Required input 顶层字段 | 合法 completed output 顶层字段 |
| --- | --- | --- |
| Workspace task | `workspace`, `params`；其他字段忽略 | `workspace`, `result` |
| Workspace-free task | `params`；其他字段忽略 | `result` |

这些结构同时用于运行时执行和生成 TaskDef schema。TaskDef 中会生成对应的 `inputKeys`、`outputKeys`、`inputSchema` 和 `outputSchema`；input 顶层 schema 使用 `additionalProperties: true`，`params` schema 的 `additionalProperties` 由 `strict_params` 决定，output schema 保持 strict。guardrail、workspace access mode、publish budget、LakeFS credentials 和 staging branch 不出现在 input/output contract 中。
