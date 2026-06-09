from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from threading import Lock
from typing import Any

from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from perago.config import MetricsConfig
from perago.metrics.core import (
    MetricRecorder,
    MetricTimer,
    RuntimeMetricContext,
    RuntimeMetricTimer,
    TaskAttemptMetricContext,
    application_metric_name,
    merge_metric_labels,
    merge_runtime_metric_labels,
    require_metric_context,
    runtime_metric_name,
    warn_rebinding_context,
)


class OtelMetricRecorder(MetricRecorder):
    """MetricRecorder implementation backed by OpenTelemetry SDK instruments."""

    def __init__(self, config: MetricsConfig) -> None:
        meter_provider = _meter_provider_from_config(config)
        self._context: TaskAttemptMetricContext | None = None
        self._meter_provider = meter_provider
        self._meter = meter_provider.get_meter("perago")
        self._histograms: dict[str, Any] = {}
        self._gauges: dict[str, Any] = {}
        self._instruments_lock = Lock()

    @classmethod
    def _bound(
        cls,
        *,
        context: TaskAttemptMetricContext,
        meter_provider: MeterProvider,
        meter: Meter,
        histograms: dict[str, Any],
        gauges: dict[str, Any],
        instruments_lock: Lock,
    ) -> OtelMetricRecorder:
        recorder = cls.__new__(cls)
        recorder._context = context
        recorder._meter_provider = meter_provider
        recorder._meter = meter
        recorder._histograms = histograms
        recorder._gauges = gauges
        recorder._instruments_lock = instruments_lock
        return recorder

    def with_context(self, context: TaskAttemptMetricContext) -> MetricRecorder:
        if self._context is not None:
            warn_rebinding_context(self._context, context)
        return OtelMetricRecorder._bound(
            context=context,
            meter_provider=self._meter_provider,
            meter=self._meter,
            histograms=self._histograms,
            gauges=self._gauges,
            instruments_lock=self._instruments_lock,
        )

    @property
    def context(self) -> TaskAttemptMetricContext | None:
        return self._context

    def histogram(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        metric_name = application_metric_name(name)
        attributes = merge_metric_labels(metric_name, require_metric_context(self._context), labels)
        histogram = self._histogram_instrument(metric_name)
        histogram.record(value, attributes)

    def gauge(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        metric_name = application_metric_name(name)
        attributes = merge_metric_labels(metric_name, require_metric_context(self._context), labels)
        gauge = self._gauge_instrument(metric_name)
        gauge.set(value, attributes)

    def timer(self, name: str, *, labels: Mapping[str, str] | None = None) -> AbstractContextManager[Any]:
        return MetricTimer(self, name, labels)

    def runtime_histogram(
        self,
        name: str,
        value: int | float,
        *,
        context: RuntimeMetricContext,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        metric_name = runtime_metric_name(name)
        attributes = merge_runtime_metric_labels(metric_name, context, labels)
        histogram = self._histogram_instrument(metric_name)
        histogram.record(value, attributes)

    def runtime_gauge(
        self,
        name: str,
        value: int | float,
        *,
        context: RuntimeMetricContext,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        metric_name = runtime_metric_name(name)
        attributes = merge_runtime_metric_labels(metric_name, context, labels)
        gauge = self._gauge_instrument(metric_name)
        gauge.set(value, attributes)

    def runtime_timer(
        self,
        name: str,
        *,
        context: RuntimeMetricContext,
        labels: Mapping[str, str] | None = None,
    ) -> AbstractContextManager[Any]:
        return RuntimeMetricTimer(self, name, context, labels)

    def shutdown(self) -> None:
        self._meter_provider.shutdown()

    def _histogram_instrument(self, metric_name: str) -> Any:
        with self._instruments_lock:
            histogram = self._histograms.get(metric_name)
            if histogram is None:
                histogram = self._meter.create_histogram(metric_name, unit="1")
                self._histograms[metric_name] = histogram
            return histogram

    def _gauge_instrument(self, metric_name: str) -> Any:
        with self._instruments_lock:
            gauge = self._gauges.get(metric_name)
            if gauge is None:
                gauge = self._meter.create_gauge(metric_name, unit="1")
                self._gauges[metric_name] = gauge
            return gauge


def _meter_provider_from_config(config: MetricsConfig) -> MeterProvider:
    timeout_seconds = config.timeout_millis / 1000 if config.timeout_millis is not None else None
    exporter = OTLPMetricExporter(
        endpoint=config.endpoint,
        compression=_otel_compression(config.compression),
        timeout=timeout_seconds,
    )
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=config.export_interval_millis,
        export_timeout_millis=config.timeout_millis,
    )
    return MeterProvider(metric_readers=[reader])


def _otel_compression(configured: str | None) -> Compression | None:
    if configured == "gzip":
        return Compression.Gzip
    return None
