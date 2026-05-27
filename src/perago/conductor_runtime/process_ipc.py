from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from perago.result import RuntimeTaskResult

from .models import ConductorTaskAttempt


@dataclass(frozen=True)
class ProcessTaskAssignment:
    attempt: ConductorTaskAttempt
    execution_id: str


@dataclass(frozen=True)
class ProcessTaskCompletion:
    task_id: str
    execution_id: str
    result: RuntimeTaskResult


@dataclass
class ProcessExecutorSlot:
    worker_id: str
    connection: Any
    generation: int = 1
    exited_generation: int | None = None


@dataclass(frozen=True)
class ProcessExecutorStarted:
    worker_id: str
    generation: int
    connection: Any


@dataclass(frozen=True)
class ProcessExecutorExited:
    worker_id: str
    generation: int
    exit_code: int | None


@dataclass(frozen=True)
class ProcessAttemptFenceRequest:
    worker_id: str
    task_id: str


@dataclass(frozen=True)
class ProcessAttemptFenceResponse:
    task_id: str
    attempt: ConductorTaskAttempt | None = None
    error: str | None = None


@dataclass(frozen=True)
class StopProcessExecutor:
    pass
