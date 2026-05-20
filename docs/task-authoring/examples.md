# Examples

本页把 task-authoring 前面几页的规则收束到可运行 module。示例优先来自仓库测试夹具，后续改动应先更新 fixture 和测试，再同步这里的说明。

## Workspace task

`tests/fixtures/app/workers/features_build.py` 是完整 workspace task。它展示了 single-task module、Pydantic params/output、workspace prefix、pre/post guardrails 和 TaskDef controls 如何组合。

```python
from pathlib import Path

from pydantic import BaseModel, Field

from perago import (
    ExecutionLimits,
    RetryPolicy,
    TaskControls,
    TimeoutPolicy,
    WorkspaceSpec,
    forbid_glob,
    require_dir,
    require_glob,
    task,
)


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
        post=[
            require_dir("features"),
            require_glob("features/**/*.parquet", min_count=1),
            forbid_glob("**/*.tmp"),
        ],
    ),
    controls=TaskControls(
        retry=RetryPolicy(count=4, logic="FIXED", delay_seconds=30),
        timeout=TimeoutPolicy(response_seconds=900),
        limits=ExecutionLimits(concurrent_exec_limit=2),
    ),
)
def build_features(workspace: Path, params: BuildFeaturesParams) -> BuildFeaturesOutput:
    features = workspace / "features"
    features.mkdir(exist_ok=True)
    (features / f"{params.feature_set}.parquet").write_text("ok", encoding="utf-8")
    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

字段边界：

- Required: `name`、`owner_email`、`params` 类型注解、返回类型注解，以及 runtime input 中的 `workspace` 和 `params`。
- Optional: `description`、`WorkspaceSpec.prefix`、pre/post guardrails、`TaskControls` 中的 retry/timeout/limits。
- Generated: 业务函数的 `workspace: Path` 参数、TaskDef schema、成功输出中的 `workspace` ref。
- Forbidden: 业务函数直接接收 LakeFS ref、把业务字段展开成多个函数参数、在 decorator 中重复声明 params/output schema。

## Workspace-free task

`tests/fixtures/app/workers/metadata_validate.py` 是完整 workspace-free task。它只声明 task metadata 和 typed `params -> result` contract，不声明 `WorkspaceSpec`。

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

字段边界：

- Required: `name`、`owner_email`、`params` 类型注解、返回类型注解，以及 runtime input 中的顶层 `params`。
- Optional: `description` 和不涉及 publication 的 `TaskControls`。
- Generated: TaskDef schema 和成功输出中的 `result`。
- Forbidden: 顶层 `workspace` input、`WorkspaceSpec`、`publish_budget`、多个业务参数或 keyword-only contract 参数。

## Workspace task with publish budget

这个正例展示 workspace task 如何声明 publication budget。`PublishBudget` 不会作为业务 input，也不会写入 TaskDef 的独立字段；它会派生 generated `responseTimeoutSeconds`，并在 runtime 中约束 LakeFS merge 和 Conductor completion 的 request timeout。

```python
from pathlib import Path

from pydantic import BaseModel

from perago import PublishBudget, TaskControls, WorkspaceSpec, task


class RenderParams(BaseModel):
    stem: str


class RenderOutput(BaseModel):
    file_count: int


@task(
    name="audio.render",
    owner_email="audio@example.com",
    workspace=WorkspaceSpec(prefix="/audio/render"),
    controls=TaskControls(
        publish_budget=PublishBudget(
            observed_merge_p99_seconds=20,
            safety_margin_seconds=10,
            lakefs_merge_timeout_seconds=45,
            conductor_completion_timeout_seconds=15,
            worker_shutdown_grace_seconds=30,
            heartbeat_interval_seconds=10,
        ),
    ),
)
def render_audio(workspace: Path, params: RenderParams) -> RenderOutput:
    output_dir = workspace / "rendered"
    output_dir.mkdir(exist_ok=True)
    (output_dir / f"{params.stem}.wav").write_bytes(b"")
    return RenderOutput(file_count=1)
```

字段边界：

- Required: `PublishBudget` 的六个时间字段都必须显式配置，且 `lakefs_merge_timeout_seconds` 必须覆盖 `observed_merge_p99_seconds + safety_margin_seconds`。
- Generated: `responseTimeoutSeconds = 45 + 15 + 30 + 10 = 100`。
- Forbidden: workspace-free task 不能配置 `publish_budget`。

## 本地验证

对一个 module 做 task-authoring 层面的检查：

```bash
PYTHONPATH=tests/fixtures perago check app.workers.features_build
PYTHONPATH=tests/fixtures perago check app.workers.metadata_validate
```

生成 Conductor TaskDef JSON：

```bash
PYTHONPATH=tests/fixtures perago extract app.workers.features_build --output /tmp/features.build.json
PYTHONPATH=tests/fixtures perago extract app.workers.metadata_validate --output /tmp/metadata.validate.json
```

`perago check` 和 `perago extract` 都以 Python import path 指向单个 module。不要传文件路径，也不要把多个 task 放进同一个 module。

## 反例索引

这些 fixture 是文档规则的可执行反例。它们用于测试 `@task(...)` import-time validation 和 CLI 诊断。

| Fixture | 被拒绝的形状 | 对应规则 |
| --- | --- | --- |
| `bad_signature.py` | workspace 参数名写成 `path` | workspace task 第一个参数必须名为 `workspace` |
| `bad_async_task.py` | task function 是 `async def` | worker body 必须是同步函数 |
| `bad_default_param.py` | `params` 声明默认值 | contract 参数不能声明默认值 |
| `bad_keyword_only_signature.py` | 增加 keyword-only 参数 | contract 只允许固定 positional-or-keyword 形状 |
| `bad_missing_params_annotation.py` | `params` 缺少类型注解 | params model 必须来自函数签名注解 |
| `bad_missing_return_annotation.py` | 缺少返回类型注解 | output model 必须来自返回类型注解 |
| `bad_variadic_signature.py` | 使用 `*extra` | 不支持 `*args` 或 `**kwargs` |
| `bad_guardrail_absolute.py` | guardrail path 以 `/` 开头 | guardrail path 必须相对 workspace root |
| `bad_workspace_prefix.py` | prefix 逃出 workspace root | `WorkspaceSpec.prefix` 必须留在 repository 内 |
| `bad_control_extra.py` | `TaskControls` 使用未知字段 | controls model 拒绝额外字段 |
| `bad_task_name_path.py` | task name 包含 path separator | task name 不能是路径 |
| `multi_task.py` | 一个 module 定义两个 task | Perago module 只能定义一个 task worker |
| `no_task.py` | module 没有 task | CLI 目标必须声明一个 Perago task |

反例不要复制到业务代码里调试。遇到相似错误时，优先运行 `perago check <module>`，然后回到对应规则页确认字段边界和签名形状。
