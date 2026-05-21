# Workspace Task

workspace task 是需要读写版本化 workspace 的 Perago worker。它把 Conductor input 中的 `workspace` ref 下载到本地 attempt workspace，把业务函数的 `workspace: Path` 指向这个本地目录，然后在任务成功后把输出发布回 LakeFS。

## 最小示例

一个 workspace task module 只定义一个 task worker。`@task(...)` 声明 task metadata 和 `WorkspaceSpec(...)`；函数签名声明唯一的业务 contract。

```python
from pathlib import Path

from pydantic import BaseModel

from perago import WorkspaceSpec, require_file, task


class BuildFeaturesParams(BaseModel):
    source: str


class BuildFeaturesOutput(BaseModel):
    rows: int


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/",
        pre=[require_file("input/data.csv")],
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    input_path = workspace / "input" / "data.csv"
    return BuildFeaturesOutput(rows=sum(1 for _ in input_path.open()))
```

Required/generated 字段边界：

- input required: Conductor input 必须提供 `workspace` 和 `params`。
- input generated: `workspace: Path` 由 Perago 注入，不由业务调用方传入函数。
- output generated: 函数返回值序列化为 Conductor output 的 `result`；成功发布后 Perago 生成 output `workspace` ref。
- task metadata required: `@task(...)` 必须声明 `name` 和 `owner_email`。
- task metadata optional: `description`、guardrail 和 controls 可按任务需要声明；`WorkspaceSpec` 的 `prefix` 参数可省略为默认值 `"/"`。

## 函数签名规则

workspace task 的函数签名固定为两个 positional-or-keyword 参数：

```python
def task_fn(workspace: Path, params: ParamsModel) -> OutputModel:
    ...
```

Perago 在 module import 时校验这个签名，`perago check` 复用同一套校验并给出 CLI 诊断。合法 workspace task 必须满足：

- 第一个参数名是 `workspace`，类型注解是 `pathlib.Path`。
- 第二个参数名是 `params`，类型注解是 Pydantic `BaseModel` 子类。
- 返回类型注解是 Pydantic `BaseModel` 子类。
- 不使用默认参数、keyword-only 参数、`*args`、`**kwargs` 或未标注类型的 contract 字段。
- `@task(...)` 必须声明 `workspace=WorkspaceSpec(...)`。

`@task(...)` 不重复声明 params 或 output schema。Pydantic params/output models 是 contract 真源，也是 TaskDef schema 生成来源。

## WorkspaceSpec prefix

`WorkspaceSpec(prefix=...)` 定义业务函数看到的 workspace 根目录对应 LakeFS ref 中的哪个对象前缀。它属于 task metadata，workflow input 只携带 workspace ref。

如果 task 声明：

```python
WorkspaceSpec(prefix="/audio/render")
```

那么业务代码里的：

```python
workspace / "raw" / "clip.parquet"
```

对应 LakeFS ref 下的：

```text
audio/render/raw/clip.parquet
```

prefix 规则：

- `"/"` 表示仓库 ref 根目录。
- `"/audio/render"` 会规范化为 `"audio/render"`。
- `"audio/render"` 合法。
- `""`、`"../raw"`、`"audio/../raw"` 非法。
- 字符串 prefix 使用 `/` 分隔，不能使用反斜杠路径。

## 读取和写入 workspace

业务函数只操作本地 `Path`。不要在 task body 内直接拼 LakeFS 对象路径、分支名或 commit ref。

```python
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    features = workspace / "features"
    features.mkdir(exist_ok=True)
    (features / f"{params.source}.txt").write_text("ok", encoding="utf-8")
    return BuildFeaturesOutput(rows=1)
```

运行时负责：

- 按 Conductor input 的 workspace ref 下载 `WorkspaceSpec(prefix=...)` 指向的内容。
- 在本地 attempt workspace 中执行函数。
- 检查 post guardrails。
- 将该 prefix 下的变更 stage 到 LakeFS。
- 成功发布后报告 generated output `workspace` 和 `result`。

## 常见拒绝场景

下面这些 module 会在 import-time validation、`perago check` 或 worker 启动时失败：

```python
# 参数名错误。
@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/"),
)
def build_features(path: Path, input: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...


# 业务字段不能展开到函数签名。
@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/"),
)
def build_features(
    workspace: Path,
    source: str,
) -> BuildFeaturesOutput:
    ...


# workspace task 必须声明 WorkspaceSpec。
@task(name="features.build", owner_email="data@example.com")
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    ...
```

同一个 Python module 只能定义一个 task worker。`perago check app.workers.features_build`、`perago extract app.workers.features_build --output generated/features.build.json` 和 `perago start app.workers.features_build -j 4` 都以这个 single-task module 为目标。

## 可运行参考

仓库测试夹具中的 `tests/fixtures/app/workers/features_build.py` 是完整 workspace task 参考。它展示了 `WorkspaceSpec(prefix="/audio/render")`、pre/post guardrails 和 `TaskControls(...)` 如何放在同一个 task declaration 中。
