from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from perago.metrics.core import (
    MetricRecorder,
    MetricTimer,
    TaskAttemptMetricContext,
    application_metric_name,
    merge_metric_labels,
    require_metric_context,
    warn_rebinding_context,
)


@dataclass(frozen=True)
class RecordedMetricSample:
    """Metric sample captured by the in-memory recorder used in tests."""

    name: str
    value: int | float
    labels: dict[str, str]


class InMemoryMetricRecorder(MetricRecorder):
    """MetricRecorder test adapter that preserves histogram and gauge semantics."""

    def __init__(self, *, context: TaskAttemptMetricContext | None = None) -> None:
        self._context = context
        self.histograms: list[RecordedMetricSample] = []
        self.gauges: list[RecordedMetricSample] = []

    def with_context(self, context: TaskAttemptMetricContext) -> MetricRecorder:
        if self._context is not None:
            warn_rebinding_context(self._context, context)
        return InMemoryMetricRecorder(context=context)

    @property
    def context(self) -> TaskAttemptMetricContext | None:
        return self._context

    def histogram(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        metric_name = application_metric_name(name)
        self.histograms.append(
            RecordedMetricSample(
                name=metric_name,
                value=value,
                labels=merge_metric_labels(metric_name, require_metric_context(self._context), labels),
            )
        )

    def gauge(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        metric_name = application_metric_name(name)
        self.gauges.append(
            RecordedMetricSample(
                name=metric_name,
                value=value,
                labels=merge_metric_labels(metric_name, require_metric_context(self._context), labels),
            )
        )

    def timer(self, name: str, *, labels: Mapping[str, str] | None = None) -> AbstractContextManager[Any]:
        return MetricTimer(self, name, labels)
