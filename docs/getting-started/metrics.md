# Metrics

Perago metrics 是显式启用的 task author API 和少量内置 runtime metrics。没有声明
`MetricSpec` 的 task 使用普通函数签名，不需要 metrics endpoint，也不会收到
`metrics` 参数。

## 启用 metrics

在 `@task(...)` 中声明 `metrics=MetricSpec()` 后，task 函数必须接收
`metrics: MetricRecorder` 参数。workspace-free task 的签名变为：

```python
from pydantic import BaseModel, Field

from perago import MetricRecorder, MetricSpec, task


class ValidateMetadataParams(BaseModel):
    song_id: str
    min_duration_seconds: int = Field(ge=1)


class ValidateMetadataOutput(BaseModel):
    valid: bool


@task(
    name="metadata.validate",
    owner_email="data@example.com",
    metrics=MetricSpec(),
)
def validate_metadata(
    params: ValidateMetadataParams,
    metrics: MetricRecorder,
) -> ValidateMetadataOutput:
    with metrics.timer("metadata_lookup", labels={"source": "catalog"}):
        valid = True
    metrics.histogram("checked_items", 1)
    return ValidateMetadataOutput(valid=valid)
```

workspace task 在 `params` 后接收同一个 `metrics` 参数：

```python
from pathlib import Path

from pydantic import BaseModel

from perago import MetricRecorder, MetricSpec, WorkspaceSpec, task


class BuildParams(BaseModel):
    feature_set: str


class BuildOutput(BaseModel):
    row_count: int


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/audio/render"),
    metrics=MetricSpec(),
)
def build_features(
    workspace: Path,
    params: BuildParams,
    metrics: MetricRecorder,
) -> BuildOutput:
    metrics.histogram("input_files", len(list((workspace / "raw").glob("*.parquet"))))
    return BuildOutput(row_count=100)
```

如果 task 声明了 `metrics=MetricSpec(...)` 却没有 `metrics` 参数，或者没有声明
`MetricSpec` 却接收 `metrics` 参数，`perago check` 会失败。

## Application metrics

task body 只能使用 `MetricRecorder` 的三个方法：

```python
metrics.histogram("batch_rows", 100, labels={"stage": "normalize"})
metrics.gauge("queue_depth", 3)

with metrics.timer("model_call", labels={"provider": "openai"}):
    ...
```

application metrics 导出时自动加 `app.` 前缀，并自动带 `task_name` label：

```text
app.batch_rows{task_name="features.build", stage="normalize"}
app.queue_depth{task_name="features.build"}
app.model_call_seconds{task_name="features.build", provider="openai"}
```

metric name 使用稳定、低基数的业务动作名。不要把文件名、用户 id、attempt id、
workflow id、execution id 或 worker id 放进 metric name 或 labels。Perago 会过滤
`task_id`、`workflow_instance_id`、`execution_id` 和 `worker_id` 这些 attempt identity
label key，并记录 warning。

## Built-in runtime metrics

`MetricSpec()` 默认开启三类 Perago 内置 runtime metrics：

| 开关 | 默认值 | 指标 |
| --- | --- | --- |
| `attempts` | `True` | `runtime.task_attempt_duration_seconds{task_name}` |
| `workspace_io` | `True` | `runtime.workspace_io_duration_seconds{task_name, operation}` 和 `runtime.workspace_io_bytes{task_name, operation}` |
| `worker_capacity` | `True` | `runtime.busy_slots{task_name, perago_instance_id}` |

`runtime.workspace_io_duration_seconds` 的 `operation` 是 `download`、`upload` 或
`publish`。`runtime.workspace_io_bytes` 只记录 `download` 和 `upload`，不会为
`publish` 写入 0 样本。

按大类关闭内置 runtime metrics：

```python
@task(
    name="metadata.validate",
    owner_email="data@example.com",
    metrics=MetricSpec(
        attempts=True,
        workspace_io=False,
        worker_capacity=False,
    ),
)
def validate_metadata(
    params: ValidateMetadataParams,
    metrics: MetricRecorder,
) -> ValidateMetadataOutput:
    metrics.histogram("checked_items", 1)
    return ValidateMetadataOutput(valid=True)
```

这些开关只影响 Perago 内置 runtime metrics，不会关闭 task body 自己写的
application metrics。

## Runtime configuration

`perago check` 和 `perago extract` 可以在没有 metrics endpoint 时运行。它们会校验
task declaration 和函数签名，并报告 metrics 配置状态。

`perago start` 对 metrics-enabled task 要求更严格：

- 必须配置 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`。
- 如果 `MetricSpec.worker_capacity=True`，还必须配置 `PERAGO_INSTANCE_ID`。
- `OTEL_EXPORTER_OTLP_METRICS_TIMEOUT` 使用毫秒整数，例如 `10000` 表示 10 秒，
  `500` 表示 500ms；不要写 `10s`。
- `OTEL_METRIC_EXPORT_INTERVAL` 也是毫秒整数，例如 `60000`。

本地 VictoriaMetrics 示例：

```bash
rtk docker compose -f docker-compose.victoria-metrics.yml up -d

PERAGO_RUN_VICTORIA_METRICS_TEST=1 \
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:8428/opentelemetry/v1/metrics \
rtk uv run pytest -q tests/integration/test_victoria_metrics.py

rtk docker compose -f docker-compose.victoria-metrics.yml down -v
```

完整环境变量说明见 {doc}`../reference/environment-variables`，runtime 配置边界见
{doc}`../runtime/configuration`。
