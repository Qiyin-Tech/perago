from __future__ import annotations

import signal
from collections.abc import Mapping
from types import FrameType
from typing import Any

from conductor.client.automator.task_runner import TaskRunner
from conductor.client.configuration.configuration import Configuration

from perago.config import ConductorConfig
from perago.task import TaskDefinition

from .models import ConductorRuntimeClient, WorkspaceRuntime
from .process_ipc import ProcessExecutorSlot
from .workers import PeragoProcessDispatchWorker, PeragoThreadWorker


def run_conductor_thread_runner(
    *,
    task: TaskDefinition,
    worker_id: str,
    thread_count: int,
    conductor_config: ConductorConfig,
    client: ConductorRuntimeClient,
    workspace_root: Any,
    failure_reason_max_length: int,
    workspace_runtime: WorkspaceRuntime | None = None,
    runner_cls: type[TaskRunner] = TaskRunner,
) -> None:
    worker = PeragoThreadWorker(
        task=task,
        worker_id=worker_id,
        thread_count=thread_count,
        client=client,
        workspace_root=workspace_root,
        failure_reason_max_length=failure_reason_max_length,
        workspace_runtime=workspace_runtime,
    )
    runner = runner_cls(
        worker,
        configuration=Configuration(server_api_url=conductor_config.server_url),
    )

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        runner.stop()

    previous_int = signal.signal(signal.SIGINT, request_stop)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        runner.run()
    finally:
        runner.stop()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def run_conductor_process_broker(
    *,
    task: TaskDefinition,
    worker_id: str,
    process_count: int,
    conductor_config: ConductorConfig,
    slots: list[ProcessExecutorSlot],
    executor_event_queue: Any | None = None,
    attempt_fence_request_queue: Any | None = None,
    attempt_fence_response_queues: Mapping[str, Any] | None = None,
    client: ConductorRuntimeClient | None = None,
    completion_timeout_seconds: float | None = None,
    failure_reason_max_length: int,
    runner_cls: type[TaskRunner] = TaskRunner,
) -> None:
    worker = PeragoProcessDispatchWorker(
        task=task,
        worker_id=worker_id,
        thread_count=process_count,
        slots=slots,
        executor_event_queue=executor_event_queue,
        attempt_fence_request_queue=attempt_fence_request_queue,
        attempt_fence_response_queues=attempt_fence_response_queues,
        client=client,
        completion_timeout_seconds=completion_timeout_seconds,
        failure_reason_max_length=failure_reason_max_length,
    )
    runner = runner_cls(
        worker,
        configuration=Configuration(server_api_url=conductor_config.server_url),
    )

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        runner.stop()

    previous_int = signal.signal(signal.SIGINT, request_stop)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        runner.run()
    finally:
        runner.stop()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
