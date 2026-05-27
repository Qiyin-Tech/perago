from __future__ import annotations

import signal
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
from perago.errors import RuntimeConfigError
from perago.result import RuntimeTaskResult, failed_result
from perago.task import TaskDefinition


PROCESS_QUEUE_POLL_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class ConductorTaskAttempt:
    workflow_instance_id: str
    task_id: str
    retry_count: int
    task_def_name: str
    reference_task_name: str
    seq: int
    iteration: int
    status: str
    input_data: Mapping[str, Any]
    retried_task_id: str | None = None
    response_timeout_seconds: int | None = None


@dataclass(frozen=True)
class ProcessTaskAssignment:
    attempt: ConductorTaskAttempt
    execution_id: str


@dataclass(frozen=True)
class ProcessTaskCompletion:
    task_id: str
    execution_id: str
    result: RuntimeTaskResult


@dataclass(frozen=True)
class ProcessExecutorSlot:
    worker_id: str
    connection: Any


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


class ConductorRuntimeClient(Protocol):
    def taskdef_exists(self, task_name: str) -> bool: ...

    def get_task(self, task_id: str) -> ConductorTaskAttempt: ...


class WorkspaceRuntime(Protocol):
    download_workspace: DownloadWorkspace
    stage_workspace: StageWorkspace
    publish_workspace: PublishWorkspace
    cleanup_staging: CleanupStaging
    complete_noop_workspace: CompleteNoOpWorkspace


class OrkesConductorRuntimeClient:
    def __init__(
        self,
        *,
        task_client: OrkesTaskClient,
        metadata_client: OrkesMetadataClient,
    ) -> None:
        self._task_client = task_client
        self._metadata_client = metadata_client

    @classmethod
    def from_config(
        cls,
        config: ConductorConfig,
    ) -> OrkesConductorRuntimeClient:
        sdk_config = Configuration(server_api_url=config.server_url)
        return cls(task_client=OrkesTaskClient(sdk_config), metadata_client=OrkesMetadataClient(sdk_config))

    def taskdef_exists(self, task_name: str) -> bool:
        try:
            self._metadata_client.get_task_def(task_name)
        except Exception as exc:  # noqa: BLE001
            if _looks_like_not_found(exc):
                return False
            raise
        return True

    def get_task(self, task_id: str) -> ConductorTaskAttempt:
        return conductor_task_to_attempt(self._task_client.get_task(task_id))


class PeragoThreadWorker(WorkerInterface):
    def __init__(
        self,
        *,
        task: TaskDefinition,
        worker_id: str,
        thread_count: int,
        client: ConductorRuntimeClient,
        workspace_root: Any,
        failure_reason_max_length: int,
        workspace_runtime: WorkspaceRuntime | None = None,
    ) -> None:
        super().__init__(task.name)
        self.task = task
        self.worker_id = worker_id
        self.thread_count = thread_count
        self.register_task_def = False
        self.register_schema = False
        self.lease_extend_enabled = True
        self._client = client
        self._workspace_root = workspace_root
        self._workspace_runtime = workspace_runtime
        self._failure_reason_max_length = failure_reason_max_length

    def get_identity(self) -> str:
        return self.worker_id

    def execute(self, task: Task) -> TaskResult:
        attempt = conductor_task_to_attempt(task)
        execution_id = uuid4().hex
        result = execute_polled_task(
            task=self.task,
            attempt=attempt,
            workspace_root=self._workspace_root,
            load_current_attempt=lambda current_attempt: self._client.get_task(current_attempt.task_id),
            workspace_runtime=self._workspace_runtime,
            owner_worker_id=self.worker_id,
            execution_id=execution_id,
            failure_reason_max_length=self._failure_reason_max_length,
        )
        return runtime_result_to_sdk_task_result(attempt, result, worker_id=self.worker_id)


class PeragoProcessDispatchWorker(WorkerInterface):
    def __init__(
        self,
        *,
        task: TaskDefinition,
        worker_id: str,
        thread_count: int,
        slots: list[ProcessExecutorSlot],
        attempt_fence_request_queue: Any | None = None,
        attempt_fence_response_queues: Mapping[str, Any] | None = None,
        client: ConductorRuntimeClient | None = None,
        completion_timeout_seconds: float | None = None,
        failure_reason_max_length: int,
    ) -> None:
        super().__init__(task.name)
        self.task = task
        self.worker_id = worker_id
        self.thread_count = thread_count
        self.register_task_def = False
        self.register_schema = False
        self.lease_extend_enabled = True
        self._slots = slots
        self._available_slots: Queue[ProcessExecutorSlot] = Queue()
        for slot in slots:
            self._available_slots.put(slot)
        self._attempt_fence_request_queue = attempt_fence_request_queue
        self._attempt_fence_response_queues = attempt_fence_response_queues or {}
        self._client = client
        self._completion_timeout_seconds = completion_timeout_seconds
        self._failure_reason_max_length = failure_reason_max_length

    def get_identity(self) -> str:
        return self.worker_id

    def execute(self, task: Task) -> TaskResult:
        attempt = conductor_task_to_attempt(task)
        execution_id = uuid4().hex
        slot = self._available_slots.get()
        try:
            try:
                slot.connection.send(ProcessTaskAssignment(attempt=attempt, execution_id=execution_id))
            except (BrokenPipeError, EOFError, OSError):
                result = failed_result(
                    f"executor pipe for worker {slot.worker_id} is broken for task {attempt.task_id}",
                    max_length=self._failure_reason_max_length,
                )
            else:
                result = self._wait_for_completion(slot, attempt, execution_id)
        finally:
            self._available_slots.put(slot)
        return runtime_result_to_sdk_task_result(attempt, result, worker_id=self.worker_id)

    def _wait_for_completion(
        self,
        slot: ProcessExecutorSlot,
        attempt: ConductorTaskAttempt,
        execution_id: str,
    ) -> RuntimeTaskResult:
        deadline = (
            None
            if self._completion_timeout_seconds is None
            else time.monotonic() + self._completion_timeout_seconds
        )
        while True:
            self._drain_attempt_fence_requests()
            try:
                timeout = PROCESS_QUEUE_POLL_INTERVAL_SECONDS
                if deadline is None:
                    if not slot.connection.poll(timeout):
                        continue
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return failed_result(
                            f"executor did not return result for task {attempt.task_id}",
                            max_length=self._failure_reason_max_length,
                        )
                    if not slot.connection.poll(min(timeout, remaining)):
                        continue
                completion = slot.connection.recv()
            except (BrokenPipeError, EOFError, OSError):
                return failed_result(
                    f"executor pipe for worker {slot.worker_id} is broken for task {attempt.task_id}",
                    max_length=self._failure_reason_max_length,
                )
            break

        if not isinstance(completion, ProcessTaskCompletion):
            return failed_result(
                f"executor returned invalid completion for task {attempt.task_id}",
                max_length=self._failure_reason_max_length,
            )
        if completion.task_id != attempt.task_id:
            return failed_result(
                f"executor returned completion for task {completion.task_id}; expected {attempt.task_id}",
                max_length=self._failure_reason_max_length,
            )
        if completion.execution_id != execution_id:
            return failed_result(
                f"executor returned completion for execution {completion.execution_id}; expected {execution_id}",
                max_length=self._failure_reason_max_length,
            )
        return completion.result

    def _drain_attempt_fence_requests(self) -> None:
        if self._attempt_fence_request_queue is None:
            return
        while True:
            try:
                request = self._attempt_fence_request_queue.get_nowait()
            except Empty:
                return
            self._handle_attempt_fence_request(request)

    def _handle_attempt_fence_request(self, request: object) -> None:
        if not isinstance(request, ProcessAttemptFenceRequest):
            logger.bind(request_type=type(request).__name__).error("broker received invalid attempt-fence request")
            return
        response_queue = self._attempt_fence_response_queues.get(request.worker_id)
        if response_queue is None:
            logger.bind(worker_id=request.worker_id, task_id=request.task_id).error(
                "broker has no attempt-fence response queue for worker"
            )
            return
        if self._client is None:
            response_queue.put(
                ProcessAttemptFenceResponse(task_id=request.task_id, error="broker has no conductor client")
            )
            return
        try:
            attempt = self._client.get_task(request.task_id)
        except Exception as exc:  # noqa: BLE001
            response_queue.put(ProcessAttemptFenceResponse(task_id=request.task_id, error=str(exc)))
            return
        response_queue.put(ProcessAttemptFenceResponse(task_id=request.task_id, attempt=attempt))


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


def conductor_task_to_attempt(task: object) -> ConductorTaskAttempt:
    return ConductorTaskAttempt(
        workflow_instance_id=str(_required_task_attr(task, "workflow_instance_id")),
        task_id=str(_required_task_attr(task, "task_id")),
        retry_count=int(_required_task_attr(task, "retry_count")),
        task_def_name=str(_required_task_attr(task, "task_def_name")),
        reference_task_name=str(_required_task_attr(task, "reference_task_name")),
        seq=int(_required_task_attr(task, "seq")),
        iteration=int(_task_attr(task, "iteration", 0) or 0),
        status=str(_required_task_attr(task, "status")),
        input_data=_mapping_attr(task, "input_data"),
        retried_task_id=_optional_str(_task_attr(task, "retried_task_id", None)),
        response_timeout_seconds=_optional_int(_task_attr(task, "response_timeout_seconds", None)),
    )


def runtime_result_to_sdk_task_result(
    attempt: ConductorTaskAttempt,
    result: RuntimeTaskResult,
    *,
    worker_id: str,
) -> TaskResult:
    task_result = TaskResult(
        workflow_instance_id=attempt.workflow_instance_id,
        task_id=attempt.task_id,
        worker_id=worker_id,
        status=TaskResultStatus(result.status),
    )
    if result.status == "COMPLETED":
        task_result.output_data = result.output
    else:
        task_result.reason_for_incompletion = result.reason_for_incompletion
    return task_result


def execute_polled_task(
    *,
    task: TaskDefinition,
    attempt: ConductorTaskAttempt,
    workspace_root: Any,
    load_current_attempt: LoadCurrentAttempt,
    owner_worker_id: str | None = None,
    execution_id: str | None = None,
    failure_reason_max_length: int,
    workspace_runtime: WorkspaceRuntime | None = None,
) -> RuntimeTaskResult:
    if task.has_workspace:
        workspace_runtime = _require_workspace_runtime(workspace_runtime)
        return run_workspace_task_attempt(
            task,
            attempt.input_data,
            attempt,
            workspace_root,
            download_workspace=workspace_runtime.download_workspace,
            load_current_attempt=load_current_attempt,
            stage_workspace=workspace_runtime.stage_workspace,
            publish_workspace=workspace_runtime.publish_workspace,
            cleanup_staging=workspace_runtime.cleanup_staging,
            complete_noop_workspace=workspace_runtime.complete_noop_workspace,
            owner_worker_id=owner_worker_id,
            execution_id=execution_id,
            failure_reason_max_length=failure_reason_max_length,
        )
    return run_workspace_free_task_attempt(
        task,
        attempt.input_data,
        failure_reason_max_length=failure_reason_max_length,
    )


def _require_workspace_runtime(workspace_runtime: WorkspaceRuntime | None) -> WorkspaceRuntime:
    if workspace_runtime is None:
        raise RuntimeConfigError("workspace runtime is required for workspace tasks")
    return workspace_runtime


def _required_task_attr(task: object, name: str) -> Any:
    value = _task_attr(task, name, None)
    if value is None:
        raise AttributeError(f"Conductor task is missing required field {name}")
    return value


def _task_attr(task: object, name: str, default: Any) -> Any:
    if isinstance(task, Mapping):
        return task.get(name, default)
    return getattr(task, name, default)


def _mapping_attr(task: object, name: str) -> Mapping[str, Any]:
    value = _required_task_attr(task, name)
    if not isinstance(value, Mapping):
        raise TypeError(f"Conductor task field {name} must be a mapping")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _looks_like_not_found(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status == 404:
        return True
    return "404" in str(exc) and "not" in str(exc).lower()
