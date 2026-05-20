# Task Contract

Task contract 只来自 Python 函数签名和 Pydantic models。Perago 不要求也不允许在 `@task(...)` 中重复声明 params 或 output schema。

## Workspace task contract

workspace task worker 的合法函数形状是：

```python
from pathlib import Path

from pydantic import BaseModel


class Params(BaseModel):
    source: str


class Output(BaseModel):
    rows: int


def build_features(workspace: Path, params: Params) -> Output:
    ...
```

它对应的外部 contract 是：

- input required: `workspace`
- input required: `params`
- output generated: `workspace`
- output generated: `result`

`workspace` 参数由 Perago 注入为本地 attempt workspace path。`params` 由 Conductor input 中的 `params` 字段解析。返回值被序列化到 output 的 `result` 字段。

## Workspace-free task contract

workspace-free task worker 的合法函数形状是：

```python
from pydantic import BaseModel


class Params(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def double(params: Params) -> Output:
    ...
```

它对应的外部 contract 是：

- input required: `params`
- output generated: `result`

workspace-free task 不声明 `WorkspaceSpec(...)`，也不接收 `workspace: Path`。

## Required, optional, generated

Perago 文档中使用这三个标签描述 contract 字段来源：

- required：调用方必须在 Conductor input 中提供，例如 `workspace` 和 `params`。
- optional：task metadata 或 controls 中可以不声明、由默认值补齐的字段，例如 `WorkspaceSpec(prefix="/")`。
- generated：Perago 在运行时或 TaskDef 生成时产生的字段，例如 output `workspace.ref` 和 TaskDef JSON 中的 derived timeout。

业务字段是否 required 由 Pydantic params model 决定。Perago 不把业务字段展开到 Conductor input 顶层。

## Rejected shapes

以下函数形状不属于 Perago MVP contract：

- 同一个 module 里定义多个 task workers。
- workspace task 缺少 `workspace=WorkspaceSpec(...)`。
- workspace-free task 声明 `workspace=WorkspaceSpec(...)`。
- 参数名不是 `workspace` 或 `params`。
- 展开业务字段，例如 `def task(workspace: Path, source: str) -> Output`。
- `async def` task worker。
- 默认参数、keyword-only 参数、`*args` 或 `**kwargs`。
- 缺少 params 类型注解或返回类型注解。
- 在 `@task(...)` 中重复声明 params 或 output schema。

这些错误会在 import-time validation、`perago check` 或 worker 启动阶段失败，而不是等到业务函数执行中才失败。
