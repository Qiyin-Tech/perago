# Getting Started

本页给新任务作者一条最短本地路径：写一个 single-task module，运行 `perago check`，再生成 Conductor TaskDef。这里不启动 worker；`perago start` 需要真实 Conductor 和 LakeFS 配置，通常属于部署或运行时维护流程。

## 前置条件

Perago 要求 Python 3.10 或更新版本。仓库本地开发默认使用 `uv`：

```bash
uv sync
```

以下命令都从仓库根目录运行。示例 module 来自 `tests/fixtures/app/workers/`，因此本地验证 fixture 时需要设置 `PYTHONPATH=tests/fixtures`。

## 写一个 workspace task

Workspace task 适合需要读写版本化 workspace 的 Conductor task。函数签名必须是 `(workspace: Path, params: ParamsModel) -> OutputModel`，并且 decorator 必须提供 `name` 和 `owner_email`。

```python
from pathlib import Path

from pydantic import BaseModel, Field

from perago import WorkspaceSpec, require_dir, require_glob, task


class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)


class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)


@task(
    name="features.build",
    description="Build feature parquet files.",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/audio/render",
        pre=[
            require_dir("raw"),
            require_glob("raw/**/*.parquet", min_count=1),
        ],
    ),
)
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    features = workspace / "features"
    features.mkdir(exist_ok=True)
    (features / f"{params.feature_set}.parquet").write_text("ok", encoding="utf-8")
    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

Required: `name`、`owner_email`、`workspace: Path`、`params` Pydantic model、返回 Pydantic model。Optional: `description`、workspace prefix、pre/post workspace checks 和 task controls。Generated: Conductor input 中的 workspace 本机路径注入、TaskDef schema、成功输出中的 workspace ref。

## 写一个 workspace-free task

Workspace-free task 适合只处理 typed input/output、无需 LakeFS workspace 的 task。函数签名必须是 `(params: ParamsModel) -> OutputModel`，不能声明 `WorkspaceSpec`。

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

Required: `name`、`owner_email`、`params` Pydantic model 和返回 Pydantic model。Optional: `description` 和不涉及 publication 的 `TaskControls`。Forbidden: `workspace` 参数、`WorkspaceSpec`、`publish_budget`、多个业务参数和 keyword-only 参数。

## 本地检查

`perago check` 会加载 runtime config、导入 module、校验 task contract，并确认 TaskDef 可以生成。它不连接 Conductor 或 LakeFS：

```bash
PYTHONPATH=tests/fixtures uv run perago check app.workers.features_build
PYTHONPATH=tests/fixtures uv run perago check app.workers.metadata_validate
```

成功输出会包含任务名、本机 workspace/log 目录、worker id 前缀，以及 Conductor 和 LakeFS 是否已配置。`conductor: not configured` 和 `lakefs: not configured` 对 `check` 不是失败条件。

## 生成 TaskDef

`perago extract` 使用同一套校验，并把 generated Conductor TaskDef 写到 JSON 文件。它不会注册 TaskDef：

```bash
PYTHONPATH=tests/fixtures uv run perago extract app.workers.features_build --output /tmp/features.build.json
PYTHONPATH=tests/fixtures uv run perago extract app.workers.metadata_validate --output /tmp/metadata.validate.json
```

输出路径必须以 `.json` 结尾。部署流程或 Conductor 管理工具负责把生成的 TaskDef 注册到 Conductor。

## 启动 worker 的边界

`perago start <module_target> -j N` 是长运行入口。启动前它会要求：

- `CONDUCTOR_SERVER_URL` 已配置。
- LakeFS endpoint、access key id 和 secret access key 已完整配置。
- Conductor 中已经注册同名 TaskDef。

开发 task body、params/output schema 或 workspace checks 时，先停在 `check` 和 `extract`。需要理解启动后的 poll、workspace download、stage、publish 和 cleanup 生命周期时，再阅读 {doc}`runtime/index`。

## 下一步

新任务作者继续读 {doc}`task-authoring/index`。需要核对精确 JSON shape、TaskDef 字段或错误分类时，读 {doc}`reference/index`。运行时维护者从 {doc}`runtime/index` 开始；需要理解 publication fence 和事务取舍时，读 {doc}`architecture/index`。
