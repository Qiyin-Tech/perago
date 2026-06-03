from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol

from loguru import logger

from perago._version import __version__
from perago.config import TelemetryConfig


COUNTER_DESCRIPTIONS = {
    "perago.task.attempts": "Perago task attempts by final runtime status.",
    "perago.executor.lifecycle.events": "Perago process executor lifecycle events observed by the broker.",
}
HISTOGRAM_DESCRIPTIONS = {
    "perago.task.attempt.duration": "Perago task attempt runtime duration.",
    "perago.task.phase.duration": "Perago task execution phase duration.",
    "perago.conductor.operation.duration": "Conductor runtime client operation duration.",
    "perago.lakefs.operation.duration": "LakeFS workspace runtime operation duration.",
    "perago.broker.slot.wait.duration": "Time spent waiting for an executor slot.",
}
UNIT_SECONDS = "s"
UNIT_COUNT = "1"


class Recorder(Protocol):
    def record_counter(self, name: str, amount: int, attributes: Mapping[str, object]) -> None: ...

    def record_histogram(self, name: str, value: float, attributes: Mapping[str, object]) -> None: ...

    def shutdown(self) -> None: ...


class NoopRecorder:
    def record_counter(self, name: str, amount: int, attributes: Mapping[str, object]) -> None:
        del name, amount, attributes

    def record_histogram(self, name: str, value: float, attributes: Mapping[str, object]) -> None:
        del name, value, attributes

    def shutdown(self) -> None:
        return


class OtelRecorder:
    def __init__(self, *, config: TelemetryConfig, module_target: str, worker_id: str, runtime_role: str) -> None:
        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.http import Compression
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        compression = None
        if config.metrics_compression is not None:
            compression = {
                "none": Compression.NoCompression,
                "gzip": Compression.Gzip,
                "deflate": Compression.Deflate,
            }[config.metrics_compression]
        exporter = OTLPMetricExporter(
            endpoint=config.metrics_endpoint,
            headers={key: value.get_secret_value() for key, value in config.metrics_headers.items()} or None,
            compression=compression,
            timeout=config.metric_export_timeout_millis / 1000,
        )
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=config.metric_export_interval_millis,
            export_timeout_millis=config.metric_export_timeout_millis,
        )
        resource = Resource.create(
            {
                **config.resource_attributes,
                "service.name": config.service_name,
                "service.version": __version__,
                "perago.module_target": module_target,
                "perago.runtime_role": runtime_role,
                "perago.worker_id": worker_id,
            }
        )
        self._provider = MeterProvider(resource=resource, metric_readers=[reader])
        self._meter = self._provider.get_meter("perago", __version__)
        metrics.set_meter_provider(self._provider)
        self._counters: dict[str, object] = {}
        self._histograms: dict[str, object] = {}

    def record_counter(self, name: str, amount: int, attributes: Mapping[str, object]) -> None:
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter.create_counter(
                name,
                unit=UNIT_COUNT,
                description=COUNTER_DESCRIPTIONS.get(name, name),
            )
            self._counters[name] = counter
        counter.add(amount, attributes=dict(attributes))

    def record_histogram(self, name: str, value: float, attributes: Mapping[str, object]) -> None:
        histogram = self._histograms.get(name)
        if histogram is None:
            histogram = self._meter.create_histogram(
                name,
                unit=UNIT_SECONDS,
                description=HISTOGRAM_DESCRIPTIONS.get(name, name),
            )
            self._histograms[name] = histogram
        histogram.record(value, attributes=dict(attributes))

    def shutdown(self) -> None:
        self._provider.shutdown()


_RECORDER: Recorder = NoopRecorder()
_CONFIGURED = False


def configure_telemetry(
    *,
    config: TelemetryConfig,
    module_target: str,
    worker_id: str,
    runtime_role: str,
) -> None:
    global _CONFIGURED, _RECORDER
    shutdown_telemetry()
    if not config.enabled:
        _RECORDER = NoopRecorder()
        _CONFIGURED = False
        return
    try:
        _RECORDER = OtelRecorder(
            config=config,
            module_target=module_target,
            worker_id=worker_id,
            runtime_role=runtime_role,
        )
    except Exception as exc:  # noqa: BLE001
        logger.bind(error_type=exc.__class__.__name__).opt(exception=exc).error(
            "failed to configure OpenTelemetry metrics"
        )
        raise
    _CONFIGURED = True
    logger.bind(
        module_target=module_target,
        worker_id=worker_id,
        runtime_role=runtime_role,
        metrics_endpoint=config.metrics_endpoint,
        metric_export_interval_millis=config.metric_export_interval_millis,
    ).info("configured OpenTelemetry metrics")


def shutdown_telemetry() -> None:
    global _CONFIGURED, _RECORDER
    if not _CONFIGURED:
        return
    try:
        _RECORDER.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.bind(error_type=exc.__class__.__name__).opt(exception=exc).warning(
            "failed to shutdown OpenTelemetry metrics"
        )
    finally:
        _RECORDER = NoopRecorder()
        _CONFIGURED = False


def record_task_attempt(*, task_name: str, workspace_kind: str, status: str, duration_seconds: float) -> None:
    attributes = {
        "task_name": task_name,
        "workspace_kind": workspace_kind,
        "status": status,
    }
    _RECORDER.record_counter("perago.task.attempts", 1, attributes)
    _RECORDER.record_histogram("perago.task.attempt.duration", duration_seconds, attributes)


def record_executor_lifecycle(*, event: str, exit_code: int | None = None) -> None:
    attributes = {
        "event": event,
        "exit_status": _exit_status(exit_code),
    }
    _RECORDER.record_counter("perago.executor.lifecycle.events", 1, attributes)


def record_broker_slot_wait(*, task_name: str, duration_seconds: float) -> None:
    _RECORDER.record_histogram(
        "perago.broker.slot.wait.duration",
        duration_seconds,
        {"task_name": task_name},
    )


@contextmanager
def task_phase_timer(*, task_name: str, workspace_kind: str, phase: str) -> Iterator[None]:
    with _duration_timer(
        "perago.task.phase.duration",
        {
            "task_name": task_name,
            "workspace_kind": workspace_kind,
            "phase": phase,
        },
    ):
        yield


@contextmanager
def conductor_operation_timer(*, operation: str) -> Iterator[None]:
    with _duration_timer("perago.conductor.operation.duration", {"operation": operation}):
        yield


@contextmanager
def lakefs_operation_timer(*, operation: str) -> Iterator[None]:
    with _duration_timer("perago.lakefs.operation.duration", {"operation": operation}):
        yield


@contextmanager
def _duration_timer(name: str, attributes: Mapping[str, object]) -> Iterator[None]:
    start = time.monotonic()
    try:
        yield
    except Exception:
        _RECORDER.record_histogram(
            name,
            time.monotonic() - start,
            {**attributes, "status": "failed"},
        )
        raise
    else:
        _RECORDER.record_histogram(
            name,
            time.monotonic() - start,
            {**attributes, "status": "succeeded"},
        )


def _exit_status(exit_code: int | None) -> str:
    if exit_code is None:
        return "unknown"
    if exit_code == 0:
        return "zero"
    if exit_code < 0:
        return "signal"
    return "nonzero"


def _set_recorder_for_tests(recorder: Recorder) -> None:
    global _CONFIGURED, _RECORDER
    shutdown_telemetry()
    _RECORDER = recorder
    _CONFIGURED = False
