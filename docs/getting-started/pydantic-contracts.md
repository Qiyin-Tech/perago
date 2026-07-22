# Pydantic Contracts

Perago task 的业务契约只来自两个 Pydantic model：`params` 参数类型和返回值类型。`@task(...)` 不再重复声明 input schema、output schema 或业务字段；`perago check`、worker runtime 和 `perago extract` 都从同一组类型注解读取契约。

## Contract 真源

每个 task module 必须声明两个普通 Pydantic `BaseModel` object model 子类：

```python
from pydantic import BaseModel, Field


class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)


class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)
```

Required/optional/generated 字段边界：

- required: 没有默认值的 model 字段必须由 Conductor input 或 task body 返回值提供。
- optional: 有默认值的字段不进入 JSON Schema 的 `required` 列表；`str | None = None` 表示字段可省略且可为 `null`。
- required-but-nullable: `str | None` 没有默认值时仍然是必填字段，只是允许值为 `null`。
- generated: workspace task 的 `workspace: Path` 参数、成功 output `workspace` ref、Conductor TaskDef schema 和 TaskDef key 列表都由 Perago 生成。
- forbidden: 业务字段不能展开到函数签名、Conductor 顶层 input 或 Conductor 顶层 output。

这意味着任务作者只维护业务模型本身。函数签名继续保持：

```python
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...
```

或：

```python
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    ...
```

## 运行时校验

Perago 在调用 task body 前执行：

```python
extra = "forbid" if task.strict_params else "ignore"
params = task.params_model.model_validate(input_data["params"], extra=extra)
```

并在构造 Conductor output 时用返回类型再次校验结果。对任务作者来说，效果是：

- `Field(...)` 约束会在运行时生效，例如 `Field(ge=1)` 会拒绝 `0`。
- 默认 `strict_params=False`：`params` 可以是 schema 的超集，所有层级的额外字段都会被丢弃，task body 只收到 schema 声明的数据。
- `strict_params=True`：所有层级的 `params` 额外字段都会被拒绝。
- Conductor `inputData` 顶层始终允许额外字段；Perago 只读取 workspace task 的 `workspace` / `params`，或 workspace-free task 的 `params`。
- task body 返回 dict 或 Pydantic object 都必须能被返回类型 model 校验。

下面的 input 在默认模式下合法；`workspace` 不在 `BuildFeaturesParams` schema 中，因此 task body 收到的 model 不包含它：

```json
{
  "params": {
    "feature_set": "default",
    "min_rows": 100,
    "workspace": "not-a-workspace"
  }
}
```

需要拒绝额外业务字段时，在装饰器上显式启用严格模式：

```python
@task(
    name="features.build",
    owner_email="data@example.com",
    strict_params=True,
)
def build_features(params: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...
```

workspace task 要求顶层 input 至少包含 `workspace` 和 `params`：

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

Conductor dynamic node 追加的 `toExecute` 等顶层字段会被忽略。

workspace-free task 要求顶层 input 包含 `params`：

```json
{
  "params": {
    "song_id": "song-000123",
    "min_duration_seconds": 30
  }
}
```

## JSON Schema 生成

`perago extract` 生成 Conductor TaskDef 时，会对 `params` model 和返回值 model 调用 Pydantic `model_json_schema()`。Perago 随后做三件事，让 schema 适合作为 Conductor TaskDef 内联 schema：

- inline `$defs` / `$ref`，避免 TaskDef 依赖外部 schema definition。
- 删除 Pydantic 自动生成的 `title` 字段，以及从 `BaseModel` class docstring 自动生成的 object-level `description`。
- 顶层 input schema 使用 `additionalProperties: true`。
- `params` object schema 默认使用 `additionalProperties: true`；`strict_params=True` 时改为 `false`，包括嵌套 Pydantic object。
- `result` 和 workspace object schema 保持 `additionalProperties: false`。

不要在 task 的 `params` / `result` model 中使用 `RootModel`，也不要依赖 `ConfigDict`。Perago 期望 task contract 是普通 `BaseModel` object model：`RootModel` 会被 `perago check`、`perago extract` 和 `perago start` 直接拒绝；配置了 `ConfigDict` 的 task model 会报 warning。Perago 当前不对使用 `ConfigDict` 后的 TaskDef schema 或运行时行为做兼容保证。`params` 的额外字段策略只由 `@task(strict_params=...)` 决定；`result` 始终严格校验。

workspace task 的 TaskDef schema 结构如下：

```text
inputSchema.data.properties.workspace = WorkspaceInput schema
inputSchema.data.properties.params = ParamsModel schema
outputSchema.data.properties.workspace = WorkspaceOutput schema
outputSchema.data.properties.result = OutputModel schema
```

workspace-free task 不包含 `workspace` schema，只包含 `params` 和 `result`。

Pydantic 默认值会保留在 schema 中，但不会生成 Conductor `inputTemplate`。例如：

```python
class ParamsWithDefaults(BaseModel):
    required_value: int
    optional_reason: str | None = None
```

会让 `required_value` 进入 `required`，并让 `optional_reason` 带有 `default: null`；调用方仍然需要显式提供顶层 `params`。

## 字段描述和 schema 漂移

`Field(description=...)`、`Field(examples=...)`、alias、正则、长度和数值范围都会进入 Pydantic JSON Schema，进而进入 generated TaskDef JSON。`BaseModel` class docstring 只作为 Python API 文档，不作为 TaskDef schema 描述。不要用 `RootModel`、`ConfigDict` 或 model-level `json_schema_extra` 定义 TaskDef schema 文案。

如果只是想解释字段业务含义，优先写在本地文档、module 注释或 API docstring 中。这样可以补全文档，不改变 Conductor 注册用的 TaskDef JSON。

## 常见拒绝场景

下面这些写法会在 import-time validation、运行时 input validation 或 output validation 阶段失败：

```python
# params 必须是 Pydantic BaseModel 子类，不能是 dict。
@task(name="features.build", owner_email="data@example.com")
def build_features(params: dict[str, str]) -> BuildFeaturesOutput:
    ...


# 返回值也必须是 Pydantic BaseModel 子类。
@task(name="features.build", owner_email="data@example.com")
def build_features(params: BuildFeaturesParams) -> dict[str, int]:
    ...


# 业务字段不能展开到函数签名。
@task(name="features.build", owner_email="data@example.com")
def build_features(feature_set: str, min_rows: int) -> BuildFeaturesOutput:
    ...
```

运行时 input 也不能绕过 `params` 包装：

```json
{
  "feature_set": "default",
  "min_rows": 100
}
```

返回值同样不能包含未声明字段：

```python
return {"row_count": 100, "feature_count": 24, "debug": "temporary"}
```

如果需要输出调试信息，应先把它建模为返回类型中的显式字段，再接受它会进入 TaskDef output schema。

## 可运行参考

`tests/fixtures/app/workers/features_build.py` 和 `tests/fixtures/app/workers/metadata_validate.py` 展示了最小 Pydantic contract。`tests/test_execution.py` 覆盖了宽松/严格 params、嵌套额外字段和 output 校验；`tests/test_taskdef.py` 覆盖了 defaults、嵌套 schema inline 和与 `strict_params` 一致的 `additionalProperties`。
