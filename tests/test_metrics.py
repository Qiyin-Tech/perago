from datetime import timedelta

from perago.config import MetricsConfig
from perago.metrics import (
    InMemoryMetricRecorder,
    OtelMetricRecorder,
    TaskAttemptMetricContext,
)


def _context() -> TaskAttemptMetricContext:
    return TaskAttemptMetricContext(
        task_name="features.build",
        task_id="task-9b4c",
        workflow_instance_id="wf-7f3d",
        execution_id="exec-1",
        worker_id="worker-1",
        retry_count=2,
    )


def _config() -> MetricsConfig:
    return MetricsConfig(
        endpoint="http://victoria.local/opentelemetry/v1/metrics",
        compression="gzip",
        timeout=timedelta(seconds=10),
        export_interval_millis=60000,
    )


def test_metric_recorder_records_application_histogram_and_gauge_with_task_label() -> None:
    recorder = InMemoryMetricRecorder().with_context(_context())

    recorder.histogram("audio_seconds", 31.4, labels={"format": "wav"})
    recorder.gauge("busy_slots", 3)

    assert [sample.name for sample in recorder.histograms] == ["app.audio_seconds"]
    assert [sample.name for sample in recorder.gauges] == ["app.busy_slots"]
    assert recorder.histograms[0].value == 31.4
    assert recorder.histograms[0].labels == {"task_name": "features.build", "format": "wav"}
    assert recorder.gauges[0].value == 3
    assert recorder.gauges[0].labels == {"task_name": "features.build"}


def test_metric_recorder_context_is_unbound_until_with_context() -> None:
    recorder = InMemoryMetricRecorder()

    bound_recorder = recorder.with_context(_context())

    assert recorder.context is None
    assert bound_recorder.context == _context()


def test_metric_recorder_keeps_perago_label_on_conflict_and_warns(monkeypatch) -> None:
    warnings: list[str] = []
    recorder = InMemoryMetricRecorder().with_context(_context())
    monkeypatch.setattr(
        "perago.metrics.core.logger.warning",
        lambda message, **kwargs: warnings.append(message.format(**kwargs)),
    )

    recorder.histogram("audio_seconds", 31.4, labels={"task_name": "fake", "format": "wav"})

    assert recorder.histograms[0].labels == {"task_name": "features.build", "format": "wav"}
    assert warnings == [
        "metric_name=app.audio_seconds label_key=task_name "
        "perago_label_value=features.build ignored_user_label_value=fake"
    ]


def test_metric_recorder_drops_reserved_attempt_labels_and_warns(monkeypatch) -> None:
    warnings: list[str] = []
    recorder = InMemoryMetricRecorder().with_context(_context())
    monkeypatch.setattr(
        "perago.metrics.core.logger.warning",
        lambda message, **kwargs: warnings.append(message.format(**kwargs)),
    )

    recorder.gauge("queue_depth", 8, labels={"task_id": "task-override", "queue": "high"})

    assert recorder.gauges[0].labels == {"task_name": "features.build", "queue": "high"}
    assert warnings == [
        "metric_name=app.queue_depth label_key=task_id "
        "perago_label_value=<reserved> ignored_user_label_value=task-override"
    ]


def test_metric_recorder_timer_records_elapsed_seconds(monkeypatch) -> None:
    clock = iter([10.0, 12.5])
    recorder = InMemoryMetricRecorder().with_context(_context())
    monkeypatch.setattr("perago.metrics.core.perf_counter", lambda: next(clock))

    with recorder.timer("model_call", labels={"provider": "openai"}):
        pass

    assert recorder.histograms[0].name == "app.model_call"
    assert recorder.histograms[0].value == 2.5
    assert recorder.histograms[0].labels == {"task_name": "features.build", "provider": "openai"}


def test_metric_recorder_warns_when_binding_context_twice(monkeypatch) -> None:
    warnings: list[str] = []
    recorder = InMemoryMetricRecorder().with_context(_context())
    monkeypatch.setattr(
        "perago.metrics.core.logger.warning",
        lambda message, **kwargs: warnings.append(message.format(**kwargs)),
    )

    recorder.with_context(
        TaskAttemptMetricContext(
            task_name="features.build",
            task_id="task-next",
            workflow_instance_id="wf-7f3d",
            execution_id="exec-2",
            worker_id="worker-1",
            retry_count=3,
        )
    )

    assert warnings == [
        "MetricRecorder.with_context called on an already bound recorder; "
        "current_task_id=task-9b4c next_task_id=task-next"
    ]


def test_otel_recorder_uses_distinct_histogram_and_gauge_paths(monkeypatch) -> None:
    meter = FakeMeter()
    monkeypatch.setattr("perago.metrics.otel._meter_provider_from_config", lambda config: FakeMeterProvider(meter))
    recorder = OtelMetricRecorder(_config()).with_context(_context())

    recorder.histogram("audio_seconds", 31.4, labels={"format": "wav"})
    recorder.gauge("queue_depth", 8, labels={"queue": "high"})
    recorder.gauge("queue_depth", 5, labels={"queue": "low"})

    assert meter.histograms["app.audio_seconds"].records == [
        (31.4, {"task_name": "features.build", "format": "wav"})
    ]
    assert meter.gauges["app.queue_depth"].sets == [
        (8, {"task_name": "features.build", "queue": "high"}),
        (5, {"task_name": "features.build", "queue": "low"}),
    ]


class FakeMeter:
    def __init__(self) -> None:
        self.histograms: dict[str, FakeHistogram] = {}
        self.gauges: dict[str, FakeGauge] = {}

    def create_histogram(self, name: str, unit: str = ""):
        del unit
        histogram = FakeHistogram()
        self.histograms[name] = histogram
        return histogram

    def create_gauge(self, name: str, unit: str = ""):
        del unit
        gauge = FakeGauge()
        self.gauges[name] = gauge
        return gauge


class FakeHistogram:
    def __init__(self) -> None:
        self.records = []

    def record(self, value, attributes) -> None:
        self.records.append((value, attributes))


class FakeGauge:
    def __init__(self) -> None:
        self.sets = []

    def set(self, value, attributes) -> None:
        self.sets.append((value, attributes))


class FakeMeterProvider:
    def __init__(self, meter: FakeMeter) -> None:
        self._meter = meter
        self.shutdown_called = False

    def get_meter(self, name: str):
        assert name == "perago"
        return self._meter

    def shutdown(self) -> None:
        self.shutdown_called = True
