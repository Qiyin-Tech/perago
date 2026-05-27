from __future__ import annotations

import signal
from queue import Empty
from types import FrameType
from typing import Any

from loguru import logger

from perago.execution import LoadCurrentAttempt
from perago.task import TaskDefinition

from .constants import PROCESS_QUEUE_POLL_INTERVAL_SECONDS
from .execution import execute_polled_task
from .models import ConductorTaskAttempt, WorkspaceRuntime
from .process_ipc import (
    ProcessAttemptFenceRequest,
    ProcessAttemptFenceResponse,
    ProcessTaskAssignment,
    ProcessTaskCompletion,
    StopProcessExecutor,
)


def run_process_executor_loop(
    *,
    task: TaskDefinition,
    worker_id: str,
    workspace_root: Any,
    connection: Any,
    load_current_attempt: LoadCurrentAttempt,
    failure_reason_max_length: int,
    workspace_runtime: WorkspaceRuntime | None = None,
) -> None:
    logger.bind(worker_id=worker_id).info("process executor started")
    shutdown_requested = False

    def request_shutdown(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        nonlocal shutdown_requested
        shutdown_requested = True

    previous_int = signal.signal(signal.SIGINT, request_shutdown)
    previous_term = signal.signal(signal.SIGTERM, request_shutdown)
    try:
        while not shutdown_requested:
            try:
                if not connection.poll(PROCESS_QUEUE_POLL_INTERVAL_SECONDS):
                    continue
                assignment = connection.recv()
            except (BrokenPipeError, EOFError, OSError):
                logger.bind(worker_id=worker_id).info("process executor pipe closed")
                return
            except Empty:
                continue
            if isinstance(assignment, StopProcessExecutor):
                logger.bind(worker_id=worker_id).info("process executor stopping")
                return
            if not isinstance(assignment, ProcessTaskAssignment):
                logger.bind(worker_id=worker_id, assignment_type=type(assignment).__name__).error(
                    "process executor received invalid assignment"
                )
                continue

            attempt = assignment.attempt
            result = execute_polled_task(
                task=task,
                attempt=attempt,
                workspace_root=workspace_root,
                load_current_attempt=load_current_attempt,
                workspace_runtime=workspace_runtime,
                owner_worker_id=worker_id,
                execution_id=assignment.execution_id,
                failure_reason_max_length=failure_reason_max_length,
            )
            try:
                connection.send(
                    ProcessTaskCompletion(
                        task_id=attempt.task_id,
                        execution_id=assignment.execution_id,
                        result=result,
                    )
                )
            except (BrokenPipeError, EOFError, OSError):
                logger.bind(worker_id=worker_id, task_id=attempt.task_id).info(
                    "process executor pipe closed before completion could be sent"
                )
                return
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def load_current_attempt_via_broker(
    current_attempt: ConductorTaskAttempt,
    *,
    worker_id: str,
    request_queue: Any,
    response_queue: Any,
) -> ConductorTaskAttempt:
    request_queue.put(ProcessAttemptFenceRequest(worker_id=worker_id, task_id=current_attempt.task_id))
    response = response_queue.get()
    if not isinstance(response, ProcessAttemptFenceResponse):
        raise RuntimeError("broker returned invalid attempt-fence response")
    if response.task_id != current_attempt.task_id:
        raise RuntimeError(
            f"broker returned attempt-fence response for {response.task_id}; expected {current_attempt.task_id}"
        )
    if response.error is not None:
        raise RuntimeError(f"broker failed to reload attempt {current_attempt.task_id}: {response.error}")
    if response.attempt is None:
        raise RuntimeError(f"broker returned empty attempt-fence response for {current_attempt.task_id}")
    return response.attempt
