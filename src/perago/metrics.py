from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class TaskAttemptMetricContext:
    """Read-only identity attached to a task attempt metric recorder."""

    task_name: str
    task_id: str
    workflow_instance_id: str
    execution_id: str
    worker_id: str
    retry_count: int


@dataclass(frozen=True)
class RecordedMetricSample:
    """Metric sample captured by an in-memory sink for tests and adapters."""

    name: str
    value: int | float
    labels: dict[str, str]


@dataclass
class InMemoryMetricSink:
    """Simple metric sink that stores emitted samples in process memory."""

    samples: list[RecordedMetricSample] = field(default_factory=list)

    def record(self, sample: RecordedMetricSample) -> None:
        self.samples.append(sample)


class MetricRecorder(ABC):
    """Perago-owned metrics API injected into metrics-enabled task workers."""

    @property
    @abstractmethod
    def context(self) -> TaskAttemptMetricContext:
        """Task attempt identity visible to metrics-enabled task workers."""

    @abstractmethod
    def histogram(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record a distribution sample."""

    @abstractmethod
    def gauge(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record a current value sample."""

    @abstractmethod
    def timer(self, name: str, *, labels: Mapping[str, str] | None = None) -> AbstractContextManager[Any]:
        """Record elapsed time when the returned context manager exits."""


class PeragoMetricRecorder(MetricRecorder):
    """Metric recorder that applies Perago naming and label rules."""

    def __init__(self, context: TaskAttemptMetricContext, sink: InMemoryMetricSink) -> None:
        self._context = context
        self._sink = sink

    @property
    def context(self) -> TaskAttemptMetricContext:
        return self._context

    def histogram(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        self._record(f"app.{name}", value, labels)

    def gauge(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        self._record(f"app.{name}", value, labels)

    def timer(self, name: str, *, labels: Mapping[str, str] | None = None) -> AbstractContextManager[Any]:
        return _MetricTimer(self, name, labels)

    def _record(self, metric_name: str, value: int | float, labels: Mapping[str, str] | None) -> None:
        merged_labels = {"task_name": self._context.task_name}
        if labels:
            for key, label_value in labels.items():
                if key == "task_name":
                    logger.warning(
                        "metric_name={metric_name} label_key={label_key} "
                        "perago_label_value={perago_label_value} "
                        "ignored_user_label_value={ignored_user_label_value}",
                        metric_name=metric_name,
                        label_key=key,
                        perago_label_value=self._context.task_name,
                        ignored_user_label_value=label_value,
                    )
                    continue
                merged_labels[key] = label_value
        self._sink.record(RecordedMetricSample(name=metric_name, value=value, labels=merged_labels))


def create_metric_recorder(context: TaskAttemptMetricContext, *, sink: InMemoryMetricSink) -> MetricRecorder:
    """Create a MetricRecorder for a task attempt."""

    return PeragoMetricRecorder(context, sink)


class _MetricTimer(AbstractContextManager[None]):
    def __init__(self, recorder: MetricRecorder, name: str, labels: Mapping[str, str] | None) -> None:
        self._recorder = recorder
        self._name = name
        self._labels = labels
        self._started_at: float | None = None

    def __enter__(self) -> None:
        self._started_at = perf_counter()
        return None

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        if self._started_at is None:
            return
        self._recorder.histogram(self._name, perf_counter() - self._started_at, labels=self._labels)
