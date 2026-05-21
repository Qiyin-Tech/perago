# Perago

Perago 是一个内部任务运行时上下文，用于让 typed Python workers 在版本化 workspace 上执行 Conductor tasks。

它把任务契约、Conductor TaskDef、LakeFS workspace 输入输出、attempt-local workspace、发布事务和 guardrail 收敛到同一组边界里。任务作者主要关心函数签名、Pydantic 契约、workspace 注入和 guardrail；运行时维护者主要关心 worker process、Conductor poll/result、LakeFS 同步和 publication fence。

如果 workflow 中还包含 TypeScript worker、人工审核节点或其他非 Perago 节点，先阅读 [Conductor / LakeFS Integration Protocol](runtime/integration-protocol.md)。该页面定义了所有节点共享同一份 LakeFS workspace 时必须遵守的 payload、publication 和 metadata 规则。

## 最小 workspace task

```python
from pathlib import Path

from pydantic import BaseModel

from perago import WorkspaceSpec, require_file, task


class Params(BaseModel):
    source: str


class Output(BaseModel):
    rows: int


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/",
        pre=[require_file("input/data.csv")],
    )
)
def build_features(workspace: Path, params: Params) -> Output:
    input_path = workspace / "input" / "data.csv"
    return Output(rows=sum(1 for _ in input_path.open()))
```

## 最小 workspace-free task

```python
from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


@task(
    name="numbers.double",
    owner_email="data@example.com",
)
def double(params: Params) -> Output:
    return Output(doubled=params.value * 2)
```

```{toctree}
:maxdepth: 2
:caption: 文档

getting-started
concepts/index
task-authoring/index
runtime/index
reference/index
architecture/index
api/index
```
