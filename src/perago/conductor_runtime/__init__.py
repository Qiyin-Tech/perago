from __future__ import annotations

import signal
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from queue import Empty, Queue
from types import FrameType
from typing import Any, Protocol
from uuid import uuid4

from conductor.client.automator.task_runner import TaskRunner
from conductor.client.configuration.configuration import Configuration
from conductor.client.http.models.task import Task
from conductor.client.http.models.task_result import TaskResult
from conductor.client.http.models.task_result_status import TaskResultStatus
from conductor.client.orkes.orkes_metadata_client import OrkesMetadataClient
from conductor.client.orkes.orkes_task_client import OrkesTaskClient
from conductor.client.worker.worker_interface import WorkerInterface
from loguru import logger

from perago.config import ConductorConfig
from perago.errors import RuntimeConfigError
from perago.execution import (
    CleanupStaging,
    CompleteNoOpWorkspace,
    DownloadWorkspace,
    LoadCurrentAttempt,
    PublishWorkspace,
    StageWorkspace,
    run_workspace_free_task_attempt,
    run_workspace_task_attempt,
)
from perago.result import RuntimeTaskResult, failed_result
from perago.task import TaskDefinition

from .client import OrkesConductorRuntimeClient
from .constants import PROCESS_QUEUE_POLL_INTERVAL_SECONDS
from .execution import execute_polled_task
from .models import ConductorRuntimeClient, ConductorTaskAttempt, WorkspaceRuntime
from .process_executor import load_current_attempt_via_broker, run_process_executor_loop
from .process_ipc import (
    ProcessAttemptFenceRequest,
    ProcessAttemptFenceResponse,
    ProcessExecutorExited,
    ProcessExecutorSlot,
    ProcessExecutorStarted,
    ProcessTaskAssignment,
    ProcessTaskCompletion,
    StopProcessExecutor,
)
from .runners import run_conductor_process_broker, run_conductor_thread_runner
from .sdk_mapping import conductor_task_to_attempt, runtime_result_to_sdk_task_result
from .workers import PeragoProcessDispatchWorker, PeragoThreadWorker

__all__ = [
    "Any",
    "CleanupStaging",
    "CompleteNoOpWorkspace",
    "ConductorConfig",
    "ConductorRuntimeClient",
    "ConductorTaskAttempt",
    "Configuration",
    "DownloadWorkspace",
    "Empty",
    "FrameType",
    "LoadCurrentAttempt",
    "Mapping",
    "OrkesConductorRuntimeClient",
    "OrkesMetadataClient",
    "OrkesTaskClient",
    "PROCESS_QUEUE_POLL_INTERVAL_SECONDS",
    "PeragoProcessDispatchWorker",
    "PeragoThreadWorker",
    "ProcessAttemptFenceRequest",
    "ProcessAttemptFenceResponse",
    "ProcessExecutorExited",
    "ProcessExecutorSlot",
    "ProcessExecutorStarted",
    "ProcessTaskAssignment",
    "ProcessTaskCompletion",
    "Protocol",
    "PublishWorkspace",
    "Queue",
    "RuntimeConfigError",
    "RuntimeTaskResult",
    "StageWorkspace",
    "StopProcessExecutor",
    "Task",
    "TaskDefinition",
    "TaskResult",
    "TaskResultStatus",
    "TaskRunner",
    "WorkerInterface",
    "WorkspaceRuntime",
    "annotations",
    "conductor_task_to_attempt",
    "dataclass",
    "execute_polled_task",
    "failed_result",
    "load_current_attempt_via_broker",
    "logger",
    "run_conductor_process_broker",
    "run_conductor_thread_runner",
    "run_process_executor_loop",
    "run_workspace_free_task_attempt",
    "run_workspace_task_attempt",
    "runtime_result_to_sdk_task_result",
    "signal",
    "threading",
    "time",
    "uuid4",
]
