# Workspace-Free Task

workspace-free task 是不读写 LakeFS workspace 的 Perago worker。它只接收 typed `params`，返回 typed `result`，适合 metadata 校验、轻量规则判断、外部只读查询包装或不需要发布 workspace 变更的节点。

## 最小示例

一个 workspace-free task module 仍然只能定义一个 task worker。`@task(...)` 声明 task metadata；函数签名声明唯一的业务 contract。

```python
from pydantic import BaseModel, Field

from perago import task


class ValidateMetadataParams(BaseModel):
    song_id: str
    min_duration_seconds: int = Field(ge=1)


class ValidateMetadataOutput(BaseModel):
    valid: bool
    reason: str | None = None


@task(
    name="metadata.validate",
    description="Validate song metadata.",
    owner_email="data@example.com",
)
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    return ValidateMetadataOutput(valid=True)
```

Required/generated 字段边界：

- input required: Conductor input 必须提供 `params`。
- input forbidden: Conductor input 不能提供顶层 `workspace`，也不能把业务字段展开到顶层。
- output generated: 函数返回值序列化为 Conductor output 的 `result`。
- output forbidden: workspace-free task 不生成 output `workspace` ref。
- task metadata required: `@task(...)` 必须声明 `name` 和 `owner_email`。
- task metadata optional: `description` 和非 publication control 可按任务需要声明。
- task metadata forbidden: 不能声明 `workspace=WorkspaceSpec(...)`；不能通过 `TaskControls(publish_budget=...)` 配置发布预算。

## 函数签名规则

workspace-free task 的函数签名固定为一个 positional-or-keyword 参数：

```python
def task_fn(params: ParamsModel) -> OutputModel:
    ...
```

合法 workspace-free task 必须满足：

- 唯一参数名是 `params`，类型注解是 Pydantic `BaseModel` 子类。
- 返回类型注解是 Pydantic `BaseModel` 子类。
- 不声明 `workspace: Path` 参数。
- 不使用默认参数、keyword-only 参数、`*args`、`**kwargs` 或未标注类型的 contract 字段。
- `@task(...)` 不声明 `workspace=WorkspaceSpec(...)`。

`@task(...)` 不重复声明 params 或 output schema。Pydantic params/output models 是 contract 真源，也是 TaskDef schema 生成来源。

## Conductor 输入输出

workspace-free task 的 Conductor input 只包含一个顶层 key：

```json
{
  "params": {
    "song_id": "song-000123",
    "min_duration_seconds": 30
  }
}
```

业务字段必须放在 `params` 内。下面这种展开写法会失败：

```json
{
  "song_id": "song-000123",
  "min_duration_seconds": 30
}
```

成功输出只包含 `result`：

```json
{
  "result": {
    "valid": true,
    "reason": null
  }
}
```

`params` 和 `result` 都按对应 Pydantic model 校验。额外字段会被拒绝，包括嵌套 model 内部的额外字段。

## TaskDef 结构

workspace-free task 生成的 Conductor TaskDef 不含 workspace key：

- `inputKeys`: `["params"]`
- `outputKeys`: `["result"]`
- `inputSchema.data.required`: `["params"]`
- `outputSchema.data.required`: `["result"]`

`params` schema 来自函数参数类型，`result` schema 来自返回类型。`None` 默认值和 Pydantic 约束会进入 schema；`workspace` 不会出现在 input/output schema 中。

## 常见拒绝场景

下面这些 module 会在 import-time validation、`perago check` 或 worker 启动时失败：

```python
# 参数名必须是 params。
@task(name="metadata.validate", owner_email="data@example.com")
def validate_metadata(input: ValidateMetadataParams) -> ValidateMetadataOutput:
    ...


# 业务字段不能展开到函数签名。
@task(name="metadata.validate", owner_email="data@example.com")
def validate_metadata(
    song_id: str,
    min_duration_seconds: int,
) -> ValidateMetadataOutput:
    ...


# workspace-free task 不能声明 WorkspaceSpec。
@task(
    name="metadata.validate",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/"),
)
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    ...


# publish_budget 只适用于 workspace task。
@task(
    name="metadata.validate",
    owner_email="data@example.com",
    controls=TaskControls(publish_budget=budget),
)
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    ...
```

同一个 Python module 只能定义一个 task worker。`perago check app.workers.metadata_validate`、`perago extract app.workers.metadata_validate --output generated/metadata.validate.json` 和 `perago start app.workers.metadata_validate -j 4` 都以这个 single-task module 为目标。

## 可运行参考

仓库测试夹具中的 `tests/fixtures/app/workers/metadata_validate.py` 是完整 workspace-free task 参考。相关运行时输入、输出和拒绝场景由 `tests/test_execution.py` 与 `tests/test_taskdef.py` 覆盖。
