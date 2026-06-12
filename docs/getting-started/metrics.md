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

`task_name` label 来自 `@task(name=...)`，不是通过 `labels` 传入。下面假设当前
task 声明为 `@task(name="audio.transcribe", ...)`。

task body 只能使用 `MetricRecorder` 的三个方法。用户 label 通过 `labels=...` 传入；
`labels` 是可选参数，key 和 value 都必须是字符串：

```python
# 没有用户 label。
metrics.gauge("queue_depth", 3)

# 通过 labels=... 传入用户 label。
metrics.histogram("audio_chunks", 12, labels={"stage": "decode", "format": "wav"})

# timer 也通过 labels=... 传入用户 label。
with metrics.timer("model_call", labels={"provider": "openai"}):
    transcript = call_model()
```

application metrics 导出时自动加 `app.` 前缀，并自动带 `task_name` label：

| Recorder call | 导出的指标名 | 自动 labels | 用户 labels |
| --- | --- | --- | --- |
| `metrics.gauge("queue_depth", ...)` | `app.queue_depth` | `task_name="audio.transcribe"` | 无 |
| `metrics.histogram("audio_chunks", ...)` | `app.audio_chunks` | `task_name="audio.transcribe"` | `stage="decode"`, `format="wav"` |
| `metrics.timer("model_call", ...)` | `app.model_call` | `task_name="audio.transcribe"` | `provider="openai"` |

`timer()` 会把耗时秒数记录到给定名称的 histogram 中，不会自动给指标名追加
`_seconds`。

在 VictoriaMetrics 里，histogram 会按 Prometheus 形态展开成多条 series：

| Perago 指标名 | VictoriaMetrics series |
| --- | --- |
| `app.audio_chunks` | `app.audio_chunks_bucket`、`app.audio_chunks_count`、`app.audio_chunks_sum` |
| `app.model_call` | `app.model_call_bucket`、`app.model_call_count`、`app.model_call_sum` |

`_sum` 是样本值总和，`_count` 是样本数。需要平均值时用 `_sum / _count`：

```text
sum by (task_name) ({__name__="app.model_call_sum"})
/
sum by (task_name) ({__name__="app.model_call_count"})
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

这些 runtime histogram 在 VictoriaMetrics 中同样展开成 `_bucket`、`_count` 和
`_sum` series。例如 `runtime.workspace_io_duration_seconds` 会写成
`runtime.workspace_io_duration_seconds_bucket`、
`runtime.workspace_io_duration_seconds_count` 和
`runtime.workspace_io_duration_seconds_sum`。按 operation 计算平均耗时：

```text
sum by (task_name, operation) ({__name__="runtime.workspace_io_duration_seconds_sum"})
/
sum by (task_name, operation) ({__name__="runtime.workspace_io_duration_seconds_count"})
```

`runtime.busy_slots` 是 gauge，不会展开成 `_sum` 或 `_count`。

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
