# Workspace Model

Perago 把 LakeFS workspace identity、task-local workspace root 和业务 payload 分开处理。workflow payload 只携带需要跨 task 传递的 workspace identity；workspace prefix、runtime credentials 和 staging branch 都不进入业务 contract。

## Workspace input

workspace task 的 Conductor input 形状是：

```json
{
  "workspace": {
    "repository": "demo-repo",
    "branch": "main",
    "ref_type": "commit",
    "ref": "abc123"
  },
  "params": {
    "source": "raw/events.jsonl"
  }
}
```

`workspace` 是 flat object，包含 LakeFS repository、target branch、immutable ref type 和 immutable ref。它不是业务参数，也不包含 workspace prefix 或 LakeFS connection settings。

`params` 承载业务 payload，并由 task 函数的 Pydantic params model 定义。

## Workspace output

workspace task 成功后返回：

```json
{
  "workspace": {
    "repository": "demo-repo",
    "branch": "main",
    "ref_type": "commit",
    "ref": "def456"
  },
  "result": {
    "rows": 42
  }
}
```

`workspace.ref` 是 Perago 发布成功后的新 commit ref。`result` 是业务返回值，由 task 函数的 Pydantic output model 定义。

## Workspace prefix

`WorkspaceSpec(prefix=...)` 是 task metadata，不是 workflow input。默认 prefix 是 `/`，表示暴露整个 repository root。比如 `WorkspaceSpec(prefix="/audio/render")` 会把 LakeFS 中 `audio/render/` 下面的对象映射到本地 `workspace` 路径根部。

prefix 必须 stay inside the repository。绝对宿主机路径、`..` 逃逸和反斜杠分隔都不是合法 workspace prefix。

## Attempt workspace

每一次 task attempt 都有独立的本地 attempt workspace。attempt workspace 不能跨 attempts、task workers 或 worker processes 复用。

workspace task 的基本生命周期是：

1. 从 input workspace ref 下载 `WorkspaceSpec(prefix=...)` 对应的对象。
2. 在本地 attempt workspace 中执行业务函数。
3. 执行 post workspace checks。
4. 把本地结果上传到 staging branch。
5. 通过 attempt fence 和 publish fence 后 merge 到 target branch。
6. 输出新的 workspace ref。

workspace-free task 没有 attempt workspace，也不接收 fake workspace。

## Workspace checks

Workspace checks 是针对本地 workspace root 的文件形状检查。公开 API 中的 `require_file`、`require_dir`、`require_glob` 和 `forbid_glob` 使用 guardrail 命名，但文档中把它们解释为 workspace checks，避免把它们误解成业务数据校验或 Pydantic schema 校验。

pre checks 在业务函数执行前检查输入 workspace，post checks 在业务函数执行后、发布前检查输出 workspace。检查失败会映射到对应的 task failure，而不是修改业务返回模型。
