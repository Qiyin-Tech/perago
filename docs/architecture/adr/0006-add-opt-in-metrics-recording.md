# ADR-0006: 增加显式启用的 metrics 记录

**日期**: 2026-06-08
**Status**: accepted
**Deciders**: Perago maintainers

## 背景

Perago 需要为 worker 运行和 task-authored application measurements 提供 metrics。日志由 Docker / Nomad 侧采集，再由 Vector 处理；Perago metrics 不能变成第二套日志通道。

之前的 OpenTelemetry PR 一次性在调度、执行、LakeFS、Conductor 等路径加入大量埋点，容易把内部事件当作 metrics。Perago 第一版 metrics 应该只记录能长期聚合分析的少量运行事实，并且保持 task author API 稳定。

当前生产目标是自部署 VictoriaMetrics。Perago worker 直接通过 OTLP/HTTP protobuf 推送 metrics 到 VictoriaMetrics，不要求部署 OpenTelemetry Collector。聚合、周期导出、export timeout 和 shutdown collection 由 OpenTelemetry Python SDK 负责，Perago 不实现自己的全局 queue 或定时 batch loop。

## 决策

Perago 通过 task metadata 显式启用 metrics：

```python
from pathlib import Path

from pydantic import BaseModel

from perago import MetricRecorder, MetricSpec, WorkspaceSpec, task


class BuildFeaturesParams(BaseModel):
    source: str


class BuildFeaturesOutput(BaseModel):
    row_count: int
    feature_count: int


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/"),
    metrics=MetricSpec(),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
    metrics: MetricRecorder,
) -> BuildFeaturesOutput:
    with metrics.timer("model_call", labels={"provider": "openai"}):
        ...

    metrics.counter("rows_processed", 100, labels={"stage": "normalize"})
    metrics.histogram("audio_seconds", 31.4, labels={"format": "wav"})

    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

`metrics=None` 是默认值。没有 `MetricSpec` 的 task 必须使用非 metrics 签名：

```python
@task(name="metadata.validate", owner_email="data@example.com")
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    ...
```

声明了 `metrics=MetricSpec(...)` 的 task 必须在函数签名中接收 `metrics: MetricRecorder`：

```python
@task(name="metadata.validate", owner_email="data@example.com", metrics=MetricSpec())
def validate_metadata(
    params: ValidateMetadataParams,
    metrics: MetricRecorder,
) -> ValidateMetadataOutput:
    metrics.counter("model_calls")
    ...
```

以下声明必须失败：

```python
@task(name="bad.missing_metrics_arg", owner_email="data@example.com", metrics=MetricSpec())
def missing_metrics_arg(params: Params) -> Output:
    ...


@task(name="bad.extra_metrics_arg", owner_email="data@example.com")
def extra_metrics_arg(params: Params, metrics: MetricRecorder) -> Output:
    ...


@task(name="bad.metrics_type", owner_email="data@example.com", metrics=MetricSpec())
def bad_metrics_type(params: Params, metrics: object) -> Output:
    ...
```

`MetricSpec` 控制三类内置 runtime metrics，默认全部开启：

```python
MetricSpec(
    attempts=True,
    workspace_io=True,
    worker_capacity=True,
)
```

如需减少内置 metrics，可以按大类关闭：

```python
@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/"),
    metrics=MetricSpec(
        attempts=True,
        workspace_io=False,
        worker_capacity=False,
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
    metrics: MetricRecorder,
) -> BuildFeaturesOutput:
    ...
```

第一版内置 runtime metrics 只包含：

```text
runtime.task_attempt_duration_seconds{task_name}
runtime.task_failures_total{task_name, failure_kind}
runtime.workspace_io_duration_seconds{task_name, operation}
runtime.workspace_io_bytes{task_name, operation}
runtime.busy_slots{task_name}
```

`failure_kind` 只使用低基数值：

```text
retryable
terminal
```

`operation` 只使用低基数值：

```text
download
upload
publish
```

Application metrics 使用 `app.` 前缀，并自动带 `task_name` label：

```python
metrics.counter("rows_processed")
metrics.counter("rows_processed", 100)
metrics.counter("rows_processed", 100, labels={"stage": "normalize"})

metrics.histogram("audio_seconds", 31.4)
metrics.histogram("audio_seconds", 31.4, labels={"format": "wav"})

metrics.gauge("busy_slots", 3)

with metrics.timer("model_call"):
    ...

with metrics.timer("model_call", labels={"provider": "openai"}):
    ...
```

上面的 application metrics 导出时命名为：

```text
app.rows_processed{task_name="features.build", stage="normalize"}
app.audio_seconds{task_name="features.build", format="wav"}
app.model_call{task_name="features.build", provider="openai"}
```

`MetricRecorder` 是 Perago 抽象，不是裸 OpenTelemetry SDK object。它携带只读 task attempt context：

```python
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
    metrics: MetricRecorder,
) -> BuildFeaturesOutput:
    task_id = metrics.context.task_id
    workflow_instance_id = metrics.context.workflow_instance_id
    retry_count = metrics.context.retry_count
    ...
```

task author 可以读取这些字段，但不能把它们放进 metric labels。Perago 内置 metrics 不使用 `task_id`、`workflow_instance_id`、`execution_id`、`worker_id` 作为 label。用户 labels 中出现这些 key 时，Perago 丢弃对应用户 label 并记录 warning。

Perago 自动 labels 优先于用户 labels。冲突时保留 Perago 值，丢弃用户值，并写 worker log：

```python
metrics.counter(
    "rows_processed",
    100,
    labels={"task_name": "fake", "stage": "normalize"},
)
```

实际导出：

```text
app.rows_processed{task_name="features.build", stage="normalize"}
```

warning 至少包含：

```text
metric_name=app.rows_processed
label_key=task_name
perago_label_value=features.build
ignored_user_label_value=fake
```

metrics export 使用 OpenTelemetry Python SDK 的 OTLP/HTTP protobuf exporter，直接写入 VictoriaMetrics。Perago 只支持 metrics-specific endpoint，不支持通用 endpoint 自动拼接：

```text
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://victoria-metrics:8428/opentelemetry/v1/metrics
OTEL_EXPORTER_OTLP_METRICS_COMPRESSION=gzip
OTEL_EXPORTER_OTLP_METRICS_TIMEOUT=10s
OTEL_METRIC_EXPORT_INTERVAL=60000
```

第一版只支持这些 env：

```text
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
OTEL_EXPORTER_OTLP_METRICS_COMPRESSION
OTEL_EXPORTER_OTLP_METRICS_TIMEOUT
OTEL_METRIC_EXPORT_INTERVAL
```

第一版不支持：

```text
OTEL_EXPORTER_OTLP_METRICS_HEADERS
OTEL_SERVICE_NAME
OTEL_RESOURCE_ATTRIBUTES
OTEL_EXPORTER_OTLP_ENDPOINT
```

`perago check` 和 `perago extract` 可以在未配置 metrics endpoint 时运行。它们校验 task declaration、function signature 和 TaskDef 生成，并报告 metrics 配置状态。

`perago start` 对 metrics-enabled task 更严格：只要 task 声明 `metrics=MetricSpec(...)`，就必须配置 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`，否则启动失败。这和 workspace task 在启动时要求 LakeFS config 的边界一致。

## 备选方案

### 方案 1: 对所有 runtime 分支做大范围 OTel 埋点

- **优点**: 可以快速暴露大量内部事件和耗时。
- **缺点**: metrics 会退化成 log-like event stream，dashboard 噪音大，标签和指标名难以治理。
- **不采用原因**: Perago 第一版 metrics 只记录少量可长期聚合的问题：attempt 耗时/失败、workspace I/O 成本、busy slots。

### 方案 2: 直接把 OpenTelemetry SDK object 注入给 task

- **优点**: 高级用户可以直接使用 OTel instrument 和 SDK 语义。
- **缺点**: task author API 绑定到一个后端实现，绕过 Perago context、命名前缀和 label 保护规则。
- **不采用原因**: Perago 应暴露稳定的 `MetricRecorder` 抽象，并保留以后调整底层 exporter 的空间。

### 方案 3: Perago 自己实现全局 metrics queue 和 batch export

- **优点**: Perago 可以完全控制 buffering、flush 和 batch 大小。
- **缺点**: 重复实现 OTel SDK 已有的 aggregation 和 periodic export；process mode 下还要处理 IPC、shutdown flush、timestamp 归属和进程异常退出。
- **不采用原因**: OTel SDK 已提供 metric reader、aggregation、export interval、timeout 和 shutdown collection。

### 方案 4: 支持完整 OTel 环境变量面

- **优点**: 对熟悉 OTel 的 operator 更灵活。
- **缺点**: headers、service name、resource attributes 会扩大配置面，尤其 resource attributes 在 VictoriaMetrics 中可能被提升成 labels，带来无意的 cardinality 风险。
- **不采用原因**: 第一版生产目标只是直连 VictoriaMetrics 的 OTLP metrics endpoint，应保持 RuntimeConfig 边界小而可审查。

## 影响

### 正向

- metrics-enabled task 是显式声明，且签名错误能在 CLI 校验阶段暴露。
- task author 得到简单稳定的 metrics API，同时仍可读取 task attempt context。
- built-in metrics 数量少，命名和 labels 可控。
- VictoriaMetrics 部署简单，不需要 OpenTelemetry Collector。

### 负向

- 启用 metrics 会改变 task function signature。
- `perago start` 对 metrics-enabled task 增加了 OTLP endpoint 配置要求。
- 第一版不支持鉴权 headers、自定义 resource attributes、service name 或通用 OTLP endpoint。

### 风险

- **Risk**: task author 在 labels 中放入高基数业务值，例如 user id、文件路径或 prompt。
  **缓解**: Perago 阻止已知 task-attempt identity labels，reserved label 冲突会 warning；quick start metrics 文档必须显眼说明 label 规范，PR review 继续把关业务高基数。
- **Risk**: 直连 VictoriaMetrics 后，resource attributes 被 VictoriaMetrics 提升成 labels 时产生额外维度。
  **缓解**: 第一版不开放 `OTEL_RESOURCE_ATTRIBUTES` 和 `OTEL_SERVICE_NAME`，查询主维度使用自动 `task_name` label。
- **Risk**: 未来部署需要鉴权 headers。
  **缓解**: 等出现真实部署需求时，再以显式 RuntimeConfig 字段支持 `OTEL_EXPORTER_OTLP_METRICS_HEADERS`。
