from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from queue import Empty, Queue
from typing import Any
from uuid import uuid4

from conductor.client.http.models.task import Task
from conductor.client.http.models.task_result import TaskResult
from conductor.client.worker.worker_interface import WorkerInterface
from loguru import logger

from perago.result import RuntimeTaskResult, failed_result
from perago.task import TaskDefinition

from .constants import PROCESS_QUEUE_POLL_INTERVAL_SECONDS
from .execution import execute_polled_task
from .models import ConductorRuntimeClient, ConductorTaskAttempt, WorkspaceRuntime
from .process_ipc import (
    ProcessAttemptFenceRequest,
    ProcessAttemptFenceResponse,
    ProcessExecutorExited,
    ProcessExecutorSlot,
    ProcessExecutorStarted,
    ProcessTaskAssignment,
    ProcessTaskCompletion,
)
from .sdk_mapping import conductor_task_to_attempt, runtime_result_to_sdk_task_result


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
        executor_event_queue: Any | None = None,
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
        self._slots_by_worker_id = {slot.worker_id: slot for slot in slots}
        self._available_slots: Queue[ProcessExecutorSlot] = Queue()
        self._slot_lock = threading.Lock()
        self._available_worker_ids: set[str] = set()
        self._busy_worker_ids: set[str] = set()
        for slot in slots:
            self._mark_slot_available_locked(slot)
        self._executor_event_queue = executor_event_queue
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
        slot, generation, connection = self._lease_slot()
        try:
            try:
                connection.send(ProcessTaskAssignment(attempt=attempt, execution_id=execution_id))
            except (BrokenPipeError, EOFError, OSError):
                self._mark_slot_exited(slot, generation)
                result = failed_result(
                    f"executor pipe for worker {slot.worker_id} is broken for task {attempt.task_id}",
                    max_length=self._failure_reason_max_length,
                )
            else:
                result = self._wait_for_completion(slot, generation, connection, attempt, execution_id)
        finally:
            self._release_slot(slot)
        return runtime_result_to_sdk_task_result(attempt, result, worker_id=self.worker_id)

    def _lease_slot(self) -> tuple[ProcessExecutorSlot, int, Any]:
        while True:
            self._drain_executor_events()
            try:
                slot = self._available_slots.get(timeout=PROCESS_QUEUE_POLL_INTERVAL_SECONDS)
            except Empty:
                continue
            with self._slot_lock:
                if slot.worker_id not in self._available_worker_ids or slot.connection is None:
                    continue
                self._available_worker_ids.remove(slot.worker_id)
                self._busy_worker_ids.add(slot.worker_id)
                return slot, slot.generation, slot.connection

    def _release_slot(self, slot: ProcessExecutorSlot) -> None:
        self._drain_executor_events()
        with self._slot_lock:
            self._busy_worker_ids.discard(slot.worker_id)
            if slot.connection is not None:
                self._mark_slot_available_locked(slot)

    def _mark_slot_available_locked(self, slot: ProcessExecutorSlot) -> None:
        if slot.worker_id in self._available_worker_ids:
            return
        self._available_worker_ids.add(slot.worker_id)
        self._available_slots.put(slot)

    def _mark_slot_exited(self, slot: ProcessExecutorSlot, generation: int) -> None:
        with self._slot_lock:
            if slot.generation != generation:
                return
            slot.connection = None
            slot.exited_generation = generation
            self._available_worker_ids.discard(slot.worker_id)

    def _wait_for_completion(
        self,
        slot: ProcessExecutorSlot,
        generation: int,
        connection: Any,
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
            self._drain_executor_events()
            if self._slot_generation_exited(slot, generation):
                return failed_result(
                    f"executor process for worker {slot.worker_id} exited while running task {attempt.task_id}",
                    max_length=self._failure_reason_max_length,
                )
            try:
                timeout = PROCESS_QUEUE_POLL_INTERVAL_SECONDS
                if deadline is None:
                    if not connection.poll(timeout):
                        continue
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return failed_result(
                            f"executor did not return result for task {attempt.task_id}",
                            max_length=self._failure_reason_max_length,
                        )
                    if not connection.poll(min(timeout, remaining)):
                        continue
                completion = connection.recv()
            except (BrokenPipeError, EOFError, OSError):
                self._mark_slot_exited(slot, generation)
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

    def _slot_generation_exited(self, slot: ProcessExecutorSlot, generation: int) -> bool:
        with self._slot_lock:
            return slot.exited_generation == generation or slot.generation != generation

    def _drain_executor_events(self) -> None:
        if self._executor_event_queue is None:
            return
        while True:
            try:
                event = self._executor_event_queue.get_nowait()
            except Empty:
                return
            self._handle_executor_event(event)

    def _handle_executor_event(self, event: object) -> None:
        if isinstance(event, ProcessExecutorExited):
            self._handle_executor_exited(event)
            return
        if isinstance(event, ProcessExecutorStarted):
            self._handle_executor_started(event)
            return
        logger.bind(event_type=type(event).__name__).error("broker received invalid executor lifecycle event")

    def _handle_executor_exited(self, event: ProcessExecutorExited) -> None:
        with self._slot_lock:
            slot = self._slots_by_worker_id.get(event.worker_id)
            if slot is None or slot.generation != event.generation:
                return
            slot.connection = None
            slot.exited_generation = event.generation
            self._available_worker_ids.discard(event.worker_id)

    def _handle_executor_started(self, event: ProcessExecutorStarted) -> None:
        with self._slot_lock:
            slot = self._slots_by_worker_id.get(event.worker_id)
            if slot is None or event.generation <= slot.generation:
                return
            slot.connection = event.connection
            slot.generation = event.generation
            slot.exited_generation = None
            if event.worker_id not in self._busy_worker_ids:
                self._mark_slot_available_locked(slot)

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
