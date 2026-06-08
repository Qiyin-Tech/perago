from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from loguru import logger


RESERVED_ATTEMPT_LABELS = frozenset({"task_id", "workflow_instance_id", "execution_id", "worker_id"})


@dataclass(frozen=True)
class TaskAttemptMetricContext:
    """Read-only identity attached to a task attempt metric recorder."""

    task_name: str
    task_id: str
    workflow_instance_id: str
    execution_id: str
    worker_id: str
    retry_count: int


class MetricRecorder(ABC):
    """Perago-owned metrics API injected into metrics-enabled task workers."""

    @abstractmethod
    def with_context(self, context: TaskAttemptMetricContext) -> MetricRecorder:
        """Bind this recorder implementation to one task attempt context."""

    @property
    @abstractmethod
    def context(self) -> TaskAttemptMetricContext | None:
        """Task attempt identity, present only after ``with_context`` binding."""

    @abstractmethod
    def histogram(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record a distribution sample."""

    @abstractmethod
    def gauge(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record a current value sample."""

    @abstractmethod
    def timer(self, name: str, *, labels: Mapping[str, str] | None = None) -> AbstractContextManager[Any]:
        """Record elapsed time when the returned context manager exits."""


def application_metric_name(name: str) -> str:
    return f"app.{name}"


def merge_metric_labels(
    metric_name: str,
    context: TaskAttemptMetricContext,
    labels: Mapping[str, str] | None,
    *,
    perago_labels: Mapping[str, str] | None = None,
) -> dict[str, str]:
    merged_labels = {"task_name": context.task_name}
    if perago_labels:
        merged_labels.update(perago_labels)

    if labels:
        for key, label_value in labels.items():
            if key in merged_labels:
                _warn_ignored_label(metric_name, key, merged_labels[key], label_value)
                continue
            if key in RESERVED_ATTEMPT_LABELS:
                _warn_ignored_label(metric_name, key, "<reserved>", label_value)
                continue
            merged_labels[key] = label_value
    return merged_labels


def require_metric_context(context: TaskAttemptMetricContext | None) -> TaskAttemptMetricContext:
    if context is None:
        raise RuntimeError("MetricRecorder context is not bound")
    return context


class MetricTimer(AbstractContextManager[None]):
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


def warn_rebinding_context(
    current_context: TaskAttemptMetricContext,
    next_context: TaskAttemptMetricContext,
) -> None:
    logger.warning(
        "MetricRecorder.with_context called on an already bound recorder; "
        "current_task_id={current_task_id} next_task_id={next_task_id}",
        current_task_id=current_context.task_id,
        next_task_id=next_context.task_id,
    )


def _warn_ignored_label(
    metric_name: str,
    label_key: str,
    perago_label_value: str,
    ignored_user_label_value: str,
) -> None:
    logger.warning(
        "metric_name={metric_name} label_key={label_key} "
        "perago_label_value={perago_label_value} "
        "ignored_user_label_value={ignored_user_label_value}",
        metric_name=metric_name,
        label_key=label_key,
        perago_label_value=perago_label_value,
        ignored_user_label_value=ignored_user_label_value,
    )
