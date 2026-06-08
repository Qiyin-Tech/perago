from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any


class MetricRecorder(ABC):
    """Perago-owned metrics API injected into metrics-enabled task workers."""

    @abstractmethod
    def histogram(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record a distribution sample."""

    @abstractmethod
    def gauge(self, name: str, value: int | float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record a current value sample."""

    @abstractmethod
    def timer(self, name: str, *, labels: Mapping[str, str] | None = None) -> AbstractContextManager[Any]:
        """Record elapsed time when the returned context manager exits."""
