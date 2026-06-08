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

    metrics.histogram("batch_rows", 100, labels={"stage": "normalize"})
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
    metrics.histogram("payload_bytes", 2048)
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
runtime.workspace_io_duration_seconds{task_name, operation}
runtime.workspace_io_bytes{task_name, operation}
runtime.busy_slots{task_name, perago_instance_id}
```

第一版不设计 counter 指标，也不记录 runtime failure count。失败细节留在 Conductor 状态和 worker logs 中。

`operation` 只使用低基数值：

```text
download
upload
publish
```

`runtime.busy_slots` 表示一个 Perago runtime instance 内当前正在执行 task body 的 slot 数，不表示全局 task 并发总量。process mode 必须由 broker 汇总 executor slot state 后导出一次；executor process 和 supervisor parent 都不导出 worker capacity metric。thread mode 由运行 `TaskRunner` 的 worker runtime owner 导出一次。多个 Perago instance 跑同一个 Task Worker 时，operator 通过 `PERAGO_INSTANCE_ID` 提供低基数实例身份，并在 dashboard 中按 `task_name` 聚合：

```text
sum by (task_name) (runtime_busy_slots)
```

`perago_instance_id` 不是 task attempt 身份，也不是 `worker_id`。它来自 worker-local runtime config `PERAGO_INSTANCE_ID`，用于区分同一个 `task_name` 的多个 Perago runtime instance。Nomad 部署可以由 job/group/allocation 生成稳定短标识后注入该环境变量。

Application metrics 使用 `app.` 前缀，并自动带 `task_name` label：

```python
metrics.histogram("audio_seconds", 31.4)
metrics.histogram("audio_seconds", 31.4, labels={"format": "wav"})
metrics.histogram("batch_rows", 100, labels={"stage": "normalize"})

metrics.gauge("busy_slots", 3)

with metrics.timer("model_call"):
    ...

with metrics.timer("model_call", labels={"provider": "openai"}):
    ...
```

`timer` context 只用于包住短小、已经成块的操作。一个 `with metrics.timer(...):` block 下不应直接包含超过 20 行代码；如果需要计时的流程更长，应先把该流程抽成独立函数，再在调用点用 timer 包住函数调用。这个约束避免 metrics 埋点把复杂业务流程藏进长 context block，也让被计时的操作有清晰名称和测试边界。

上面的 application metrics 导出时命名为：

```text
app.audio_seconds{task_name="features.build", format="wav"}
app.batch_rows{task_name="features.build", stage="normalize"}
app.model_call{task_name="features.build", provider="openai"}
```

`MetricRecorder` 是 Perago 抽象，不是裸 OpenTelemetry SDK object。进程级 recorder 由 runtime config 创建，初始 `context` 为 `None`；当前 task attempt 的 recorder 必须通过 `with_context(TaskAttemptMetricContext(...))` 派生。`with_context` 是 runtime 内部绑定 API，不是 task 函数入参。对已经绑定 context 的 recorder 再调用 `with_context` 必须写 warning。

`OtelMetricRecorder` 是 OTel SDK 特化实现，构造函数接收 Perago metrics 配置并在内部创建 OTel provider/meter；`Meter`、OTel lifecycle helper、factory 之类概念不能泄漏到 execution core 或 task author API。测试用 in-memory recorder 独立放在测试 adapter 路径，不作为生产创建路径。

当前代码结构按职责拆分为 `perago.metrics.core`、`perago.metrics.otel` 和 `perago.metrics.in_memory`，同时保留 `from perago.metrics import MetricRecorder` 的公开导入入口。

task body 收到的是已经绑定 task attempt context 的 recorder：

```python
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
    metrics: MetricRecorder,
) -> BuildFeaturesOutput:
    context = metrics.context
    assert context is not None
    task_id = context.task_id
    workflow_instance_id = context.workflow_instance_id
    retry_count = context.retry_count
    ...
```

task author 可以读取这些字段，但不能把它们放进 metric labels。Perago 内置 metrics 不使用 `task_id`、`workflow_instance_id`、`execution_id`、`worker_id` 作为 label。用户 labels 中出现这些 key 时，Perago 丢弃对应用户 label 并记录 warning。

Perago 自动 labels 优先于用户 labels。冲突时保留 Perago 值，丢弃用户值，并写 worker log：

```python
metrics.histogram(
    "audio_seconds",
    31.4,
    labels={"task_name": "fake", "format": "wav"},
)
```

实际导出：

```text
app.audio_seconds{task_name="features.build", format="wav"}
```

warning 至少包含：

```text
metric_name=app.audio_seconds
label_key=task_name
perago_label_value=features.build
ignored_user_label_value=fake
```

metrics export 使用 OpenTelemetry Python SDK 的 OTLP/HTTP protobuf exporter，直接写入 VictoriaMetrics。Perago 只支持 metrics-specific endpoint，不支持通用 endpoint 自动拼接：

```text
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://victoria-metrics:8428/opentelemetry/v1/metrics
OTEL_EXPORTER_OTLP_METRICS_COMPRESSION=gzip
OTEL_EXPORTER_OTLP_METRICS_TIMEOUT=10
OTEL_METRIC_EXPORT_INTERVAL=60000
PERAGO_INSTANCE_ID=features-build-prod-a-alloc-01
```

第一版只支持这些 env：

```text
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
OTEL_EXPORTER_OTLP_METRICS_COMPRESSION
OTEL_EXPORTER_OTLP_METRICS_TIMEOUT
OTEL_METRIC_EXPORT_INTERVAL
PERAGO_INSTANCE_ID
```

`OTEL_EXPORTER_OTLP_METRICS_TIMEOUT` 直接按 OpenTelemetry Python OTLP/HTTP metrics exporter 当前接受的秒数数字传递，例如 `10` 表示 10 秒；不接受 `10s` 这类带单位后缀。`OTEL_METRIC_EXPORT_INTERVAL` 仍使用 OTel SDK 的毫秒整数语义。

第一版不支持：

```text
OTEL_EXPORTER_OTLP_METRICS_HEADERS
OTEL_SERVICE_NAME
OTEL_RESOURCE_ATTRIBUTES
OTEL_EXPORTER_OTLP_ENDPOINT
```

`perago check` 和 `perago extract` 可以在未配置 metrics endpoint 时运行。它们校验 task declaration、function signature 和 TaskDef 生成，并报告 metrics 配置状态。

`perago start` 对 metrics-enabled task 更严格：只要 task 声明 `metrics=MetricSpec(...)`，就必须配置 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`。如果该 task 保留 `worker_capacity=True`，还必须配置 `PERAGO_INSTANCE_ID`，否则启动失败。这和 workspace task 在启动时要求 LakeFS config 的边界一致。

## 备选方案

### 方案 1: 对所有 runtime 分支做大范围 OTel 埋点

- **优点**: 可以快速暴露大量内部事件和耗时。
- **缺点**: metrics 会退化成 log-like event stream，dashboard 噪音大，标签和指标名难以治理。
- **不采用原因**: Perago 第一版 metrics 只记录少量可长期聚合的问题：attempt 耗时、workspace I/O 成本、busy slots。

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
- **Risk**: 多个 Perago instance 跑同一个 Task Worker 时，`runtime.busy_slots` 时序互相覆盖。
  **缓解**: worker capacity metric 必须带 `perago_instance_id`；process mode 只由 broker 汇总导出，executor process 和 supervisor parent 不导出 busy slots。
