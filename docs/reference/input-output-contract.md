# Input/Output Contract

本页定义 Perago worker 从 Conductor 读取的 `inputData` 结构，以及 Perago 回写到 Conductor 的 task result 结构。这里的 contract 描述运行时边界；业务 `params` 和 `result` 的字段仍然来自 task module 函数签名中的 Pydantic model。

## Workspace Task Input

Workspace task 的 Conductor `inputData` 顶层字段必须且只能包含 `workspace` 和 `params`。

| 字段 | 状态 | 来源 | 说明 |
| --- | --- | --- | --- |
| `workspace` | required | Workflow 传入 | `WorkspaceInput`，指向要下载的 LakeFS repository、target branch 和 immutable input commit。 |
| `params` | required | Workflow 传入 | 业务参数对象，按 task 函数的 `params` Pydantic model 校验。 |

```json
{
  "workspace": {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
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
| `branch` | required | 成功发布时要推进的目标 branch，不能为空字符串。 |
| `ref_type` | required | 目前只接受 `"commit"`。 |
| `ref` | required | 本次 attempt 下载的 immutable input commit，不能为空字符串。 |

LakeFS endpoint、access key、secret key、workspace prefix 和 guardrail 都不属于 Conductor input。连接信息来自 worker 本地配置；prefix 与 guardrail 来自 task module 的 `WorkspaceSpec(...)`。

## Workspace-Free Task Input

Workspace-free task 的 Conductor `inputData` 顶层字段必须且只能包含 `params`。

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

不要把 `params` 展开到顶层，也不要在 workspace-free task input 中传 `workspace`。Perago 会把顶层额外字段视为 contract 错误。

## Business Model Validation

Perago 在调用业务函数前使用 task 的 Pydantic `params` model 校验 `params`，并强制 `extra="forbid"`。这意味着即使业务 model 没有显式声明 `ConfigDict(extra="forbid")`，运行时仍会拒绝额外字段。

嵌套 object 也按相同规则关闭额外字段。默认值属于 Pydantic schema 与运行时模型校验的一部分，但 Perago 不生成 Conductor `inputTemplate`，也不会把默认值复制进 Conductor task input。

## Completed Output

Perago 只在 task 成功完成时向 Conductor 回写 `outputData`。`RuntimeTaskResult.conductor_payload()` 的完成载荷如下：

```json
{
  "status": "COMPLETED",
  "output": {
    "workspace": {
      "repository": "song-000123",
      "branch": "main",
      "ref_type": "commit",
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
| `workspace` | generated | Perago runtime | `WorkspaceOutput`，保留 input 的 repository/branch，并把 `ref` 替换成成功发布后的 commit。 |
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

失败结果不会携带 `output`。Perago 只回写 Conductor status 和 `reasonForIncompletion`。

```json
{
  "status": "FAILED",
  "reasonForIncompletion": "workspace task input must contain only workspace and params"
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
| `COMPLETED` | `output` required, `reasonForIncompletion` forbidden | 业务函数成功、post guardrail 通过、workspace task 已完成发布。 |
| `FAILED` | `reasonForIncompletion` required, `output` forbidden | 输入结构错误、Pydantic 校验失败、业务异常、post guardrail 失败、attempt fence 或 publish fence 失败。 |
| `FAILED_WITH_TERMINAL_ERROR` | `reasonForIncompletion` required, `output` forbidden | pre guardrail 失败，表示上游 workspace input 不满足任务输入文件契约。 |

## Strict Top-Level Shapes

Perago 的运行时入口会先检查顶层字段集合，再校验 Pydantic payload。

| Task 类型 | 合法 input 顶层字段 | 合法 completed output 顶层字段 |
| --- | --- | --- |
| Workspace task | `workspace`, `params` | `workspace`, `result` |
| Workspace-free task | `params` | `result` |

这些结构同时用于运行时执行和生成 TaskDef schema。TaskDef 中会生成对应的 `inputKeys`、`outputKeys`、`inputSchema` 和 `outputSchema`；guardrail、publish budget、LakeFS credentials 和 staging branch 不出现在 input/output contract 中。
