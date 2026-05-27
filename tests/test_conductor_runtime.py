import multiprocessing
import threading
import time
from queue import Queue
import signal
from types import SimpleNamespace

import pytest
from conductor.client.configuration.configuration import Configuration
from conductor.client.http.models.task import Task

from perago.conductor_runtime import (
    ConductorTaskAttempt,
    OrkesConductorRuntimeClient,
    PeragoProcessDispatchWorker,
    PeragoThreadWorker,
    ProcessExecutorSlot,
    ProcessAttemptFenceRequest,
    ProcessAttemptFenceResponse,
    ProcessTaskAssignment,
    ProcessTaskCompletion,
    StopProcessExecutor,
    conductor_task_to_attempt,
    execute_polled_task,
    load_current_attempt_via_broker,
    run_conductor_process_broker,
    run_process_executor_loop,
    run_conductor_thread_runner,
    runtime_result_to_sdk_task_result,
)
from perago.config import DEFAULT_FAILURE_REASON_MAX_LENGTH, ConductorConfig
from perago.result import completed_result, failed_result, terminal_failed_result
from perago.task import load_module_task


def _attempt(input_data=None) -> ConductorTaskAttempt:
    return ConductorTaskAttempt(
        workflow_instance_id="wf-7f3d",
        task_id="task-9b4c",
        retry_count=2,
        task_def_name="metadata.validate",
        reference_task_name="validate_metadata",
        seq=3,
        iteration=1,
        status="IN_PROGRESS",
        input_data=input_data
        or {
            "params": {
                "song_id": "song-000123",
                "min_duration_seconds": 30,
            }
        },
    )


def _sdk_task(task_id: str = "task-9b4c") -> Task:
    return Task(
        workflow_instance_id="wf-7f3d",
        task_id=task_id,
        retry_count=2,
        task_def_name="metadata.validate",
        reference_task_name="validate_metadata",
        seq=3,
        iteration=1,
        status="IN_PROGRESS",
        input_data={"params": {"song_id": "song-000123", "min_duration_seconds": 30}},
        response_timeout_seconds=75,
    )


def _process_slot(worker_id: str = "metadata0001"):
    broker_connection, executor_connection = multiprocessing.Pipe()
    return ProcessExecutorSlot(worker_id=worker_id, connection=broker_connection), executor_connection


class _FakeProcessConnection:
    def __init__(self, incoming: list[object]) -> None:
        self.incoming = incoming
        self.sent: list[object] = []
        self.poll_calls = 0

    def poll(self, timeout=None) -> bool:
        del timeout
        self.poll_calls += 1
        return bool(self.incoming)

    def recv(self):
        return self.incoming.pop(0)

    def send(self, item) -> None:
        self.sent.append(item)


def test_conductor_task_to_attempt_maps_runtime_fields() -> None:
    task = Task(
        workflow_instance_id="wf-7f3d",
        task_id="task-9b4c",
        retry_count=2,
        task_def_name="features.build",
        reference_task_name="build_features",
        seq=3,
        iteration=1,
        status="IN_PROGRESS",
        input_data={"params": {"value": 1}},
        response_timeout_seconds=75,
    )

    attempt = conductor_task_to_attempt(task)

    assert attempt.workflow_instance_id == "wf-7f3d"
    assert attempt.task_id == "task-9b4c"
    assert attempt.retry_count == 2
    assert attempt.task_def_name == "features.build"
    assert attempt.reference_task_name == "build_features"
    assert attempt.seq == 3
    assert attempt.iteration == 1
    assert attempt.input_data == {"params": {"value": 1}}
    assert attempt.response_timeout_seconds == 75


def test_conductor_task_to_attempt_keeps_missing_response_timeout_optional() -> None:
    task = {
        "workflow_instance_id": "wf-7f3d",
        "task_id": "task-9b4c",
        "retry_count": 2,
        "task_def_name": "features.build",
        "reference_task_name": "build_features",
        "seq": 3,
        "status": "IN_PROGRESS",
        "input_data": {"params": {"value": 1}},
    }

    attempt = conductor_task_to_attempt(task)

    assert attempt.response_timeout_seconds is None


def test_conductor_task_to_attempt_validates_required_and_mapping_fields() -> None:
    task = {
        "workflow_instance_id": "wf-7f3d",
        "task_id": "task-9b4c",
        "retry_count": 2,
        "task_def_name": "features.build",
        "reference_task_name": "build_features",
        "seq": 3,
        "status": "IN_PROGRESS",
        "input_data": "not-a-mapping",
    }

    with pytest.raises(TypeError, match="input_data must be a mapping"):
        conductor_task_to_attempt(task)

    task.pop("task_id")
    with pytest.raises(AttributeError, match="missing required field task_id"):
        conductor_task_to_attempt(task)


def test_conductor_task_to_attempt_coerces_optional_retry_fields() -> None:
    task = {
        "workflow_instance_id": "wf-7f3d",
        "task_id": "task-9b4c",
        "retry_count": 2,
        "task_def_name": "features.build",
        "reference_task_name": "build_features",
        "seq": 3,
        "status": "IN_PROGRESS",
        "input_data": {"params": {"value": 1}},
        "retried_task_id": 123,
        "response_timeout_seconds": "75",
    }

    attempt = conductor_task_to_attempt(task)

    assert attempt.retried_task_id == "123"
    assert attempt.response_timeout_seconds == 75


def test_orkes_conductor_runtime_client_wraps_metadata_and_task_clients() -> None:
    class NotFoundByStatus(Exception):
        status = 404

    class NotFoundByMessage(Exception):
        pass

    class FakeMetadataClient:
        def __init__(self) -> None:
            self.errors = []
            self.task_names = []

        def get_task_def(self, task_name: str) -> None:
            self.task_names.append(task_name)
            if self.errors:
                raise self.errors.pop(0)

    class FakeTaskClient:
        def get_task(self, task_id: str):
            assert task_id == "task-9b4c"
            return {
                "workflow_instance_id": "wf-7f3d",
                "task_id": task_id,
                "retry_count": 2,
                "task_def_name": "features.build",
                "reference_task_name": "build_features",
                "seq": 3,
                "status": "IN_PROGRESS",
                "input_data": {"params": {"value": 1}},
            }

    metadata_client = FakeMetadataClient()
    client = OrkesConductorRuntimeClient(task_client=FakeTaskClient(), metadata_client=metadata_client)

    assert client.taskdef_exists("features.build") is True
    metadata_client.errors.append(NotFoundByStatus())
    assert client.taskdef_exists("features.missing") is False
    metadata_client.errors.append(NotFoundByMessage("HTTP 404 not found"))
    assert client.taskdef_exists("features.also_missing") is False
    metadata_client.errors.append(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        client.taskdef_exists("features.error")

    attempt = client.get_task("task-9b4c")
    assert attempt.task_id == "task-9b4c"
    assert attempt.input_data == {"params": {"value": 1}}


def test_orkes_conductor_runtime_client_from_config_builds_sdk_clients() -> None:
    client = OrkesConductorRuntimeClient.from_config(ConductorConfig(server_url="http://conductor.local/api"))

    assert client._task_client is not None
    assert client._metadata_client is not None


def test_runtime_result_to_sdk_task_result_maps_completed_and_failures() -> None:
    attempt = _attempt()

    completed = runtime_result_to_sdk_task_result(
        attempt,
        completed_result({"result": {"valid": True}}),
        worker_id="worker-1",
    )
    failed = runtime_result_to_sdk_task_result(
        attempt,
        failed_result("bad input", max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH),
        worker_id="worker-1",
    )
    terminal = runtime_result_to_sdk_task_result(
        attempt,
        terminal_failed_result("pre guardrail", max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH),
        worker_id="worker-1",
    )

    assert completed.workflow_instance_id == "wf-7f3d"
    assert completed.task_id == "task-9b4c"
    assert completed.worker_id == "worker-1"
    assert completed.status == "COMPLETED"
    assert completed.output_data == {"result": {"valid": True}}
    assert failed.status == "FAILED"
    assert failed.reason_for_incompletion == "bad input"
    assert terminal.status == "FAILED_WITH_TERMINAL_ERROR"
    assert terminal.reason_for_incompletion == "pre guardrail"


def test_thread_worker_configures_sdk_worker_contract() -> None:
    worker = PeragoThreadWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=4,
        client=object(),
        workspace_root="unused",
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    assert worker.get_identity() == "metadataBroker"
    assert worker.thread_count == 4
    assert worker.lease_extend_enabled is True
    assert worker.register_task_def is False
    assert worker.register_schema is False
    assert worker.get_task_definition_name() == "metadata.validate"


def test_thread_worker_executes_polled_task_and_maps_result() -> None:
    task = Task(
        workflow_instance_id="wf-7f3d",
        task_id="task-9b4c",
        retry_count=2,
        task_def_name="metadata.validate",
        reference_task_name="validate_metadata",
        seq=3,
        iteration=1,
        status="IN_PROGRESS",
        input_data={
            "params": {
                "song_id": "song-000123",
                "min_duration_seconds": 30,
            }
        },
        response_timeout_seconds=75,
    )
    worker = PeragoThreadWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        client=object(),
        workspace_root="unused",
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    result = worker.execute(task)

    assert result.workflow_instance_id == "wf-7f3d"
    assert result.task_id == "task-9b4c"
    assert result.worker_id == "metadataBroker"
    assert result.status == "COMPLETED"
    assert result.output_data == {"result": {"valid": True, "reason": None}}


def test_thread_worker_passes_failure_reason_limit_to_execution(monkeypatch) -> None:
    captured = {}

    def fake_execute_polled_task(**kwargs):
        captured.update(kwargs)
        return completed_result({"result": {"valid": True, "reason": None}})

    monkeypatch.setattr("perago.conductor_runtime.execute_polled_task", fake_execute_polled_task)
    worker = PeragoThreadWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        client=object(),
        workspace_root="unused",
        failure_reason_max_length=37,
    )

    worker.execute(
        Task(
            workflow_instance_id="wf-7f3d",
            task_id="task-9b4c",
            retry_count=2,
            task_def_name="metadata.validate",
            reference_task_name="validate_metadata",
            seq=3,
            status="IN_PROGRESS",
            input_data={"params": {"song_id": "song-000123", "min_duration_seconds": 30}},
        )
    )

    assert captured["failure_reason_max_length"] == 37


def test_process_dispatch_worker_configures_sdk_worker_contract() -> None:
    slot, _ = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=4,
        slots=[slot],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    assert worker.get_identity() == "metadataBroker"
    assert worker.thread_count == 4
    assert worker.lease_extend_enabled is True
    assert worker.register_task_def is False
    assert worker.register_schema is False
    assert worker.get_task_definition_name() == "metadata.validate"


def test_process_dispatch_worker_dispatches_attempt_and_maps_completion() -> None:
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    result_queue: Queue = Queue()
    thread = threading.Thread(target=lambda: result_queue.put(worker.execute(_sdk_task())))
    thread.start()

    assert executor_connection.poll(1)
    assignment = executor_connection.recv()
    assert isinstance(assignment, ProcessTaskAssignment)
    assert assignment.attempt.task_id == "task-9b4c"
    assert assignment.execution_id
    assert assignment.attempt.response_timeout_seconds == 75
    executor_connection.send(
        ProcessTaskCompletion(
            task_id="task-9b4c",
            execution_id=assignment.execution_id,
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    thread.join(timeout=1)
    assert not thread.is_alive()
    result = result_queue.get_nowait()
    assert result.workflow_instance_id == "wf-7f3d"
    assert result.task_id == "task-9b4c"
    assert result.worker_id == "metadataBroker"
    assert result.status == "COMPLETED"
    assert result.output_data == {"result": {"valid": True, "reason": None}}


def test_process_dispatch_worker_uses_distinct_slots_for_concurrent_tasks_and_accepts_reverse_completion() -> None:
    first_slot, first_executor = _process_slot("metadata0001")
    second_slot, second_executor = _process_slot("metadata0002")
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=2,
        slots=[first_slot, second_slot],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    results: Queue = Queue()
    first_thread = threading.Thread(target=lambda: results.put(("first", worker.execute(_sdk_task("task-a")))))
    second_thread = threading.Thread(target=lambda: results.put(("second", worker.execute(_sdk_task("task-b")))))

    first_thread.start()
    assert first_executor.poll(1)
    first_assignment = first_executor.recv()
    second_thread.start()
    assert second_executor.poll(1)
    second_assignment = second_executor.recv()
    second_executor.send(
        ProcessTaskCompletion(
            task_id="task-b",
            execution_id=second_assignment.execution_id,
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    first_executor.send(
        ProcessTaskCompletion(
            task_id="task-a",
            execution_id=first_assignment.execution_id,
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    completed = {results.get_nowait()[0], results.get_nowait()[0]}
    assert completed == {"first", "second"}


def test_process_dispatch_worker_waits_when_all_slots_are_busy() -> None:
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    results: Queue = Queue()
    first_thread = threading.Thread(target=lambda: results.put(worker.execute(_sdk_task("task-a"))))
    second_thread = threading.Thread(target=lambda: results.put(worker.execute(_sdk_task("task-b"))))

    first_thread.start()
    assert executor_connection.poll(1)
    first_assignment = executor_connection.recv()
    second_thread.start()
    assert not executor_connection.poll(0.05)
    executor_connection.send(
        ProcessTaskCompletion(
            task_id="task-a",
            execution_id=first_assignment.execution_id,
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    first_thread.join(timeout=1)
    assert executor_connection.poll(1)
    second_assignment = executor_connection.recv()
    assert second_assignment.attempt.task_id == "task-b"
    executor_connection.send(
        ProcessTaskCompletion(
            task_id="task-b",
            execution_id=second_assignment.execution_id,
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    second_thread.join(timeout=1)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert [results.get_nowait().status, results.get_nowait().status] == ["COMPLETED", "COMPLETED"]


def test_process_dispatch_worker_fails_closed_on_mismatched_completion() -> None:
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    result_queue: Queue = Queue()
    thread = threading.Thread(target=lambda: result_queue.put(worker.execute(_sdk_task())))
    thread.start()
    assert executor_connection.poll(1)
    assignment = executor_connection.recv()
    executor_connection.send(
        ProcessTaskCompletion(
            task_id="other-task",
            execution_id=assignment.execution_id,
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    thread.join(timeout=1)
    result = result_queue.get_nowait()

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "executor returned completion for task other-task; expected task-9b4c"


def test_process_dispatch_worker_fails_closed_on_mismatched_execution_completion() -> None:
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    result_queue: Queue = Queue()
    thread = threading.Thread(target=lambda: result_queue.put(worker.execute(_sdk_task())))
    thread.start()
    assert executor_connection.poll(1)
    executor_connection.recv()
    executor_connection.send(
        ProcessTaskCompletion(
            task_id="task-9b4c",
            execution_id="old-execution",
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    thread.join(timeout=1)
    result = result_queue.get_nowait()

    assert result.status == "FAILED"
    assert "executor returned completion for execution old-execution; expected" in result.reason_for_incompletion


def test_process_dispatch_worker_fails_closed_on_invalid_completion() -> None:
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    result_queue: Queue = Queue()
    thread = threading.Thread(target=lambda: result_queue.put(worker.execute(_sdk_task())))
    thread.start()
    assert executor_connection.poll(1)
    executor_connection.recv()
    executor_connection.send(object())
    thread.join(timeout=1)
    result = result_queue.get_nowait()

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "executor returned invalid completion for task task-9b4c"


def test_process_dispatch_worker_truncates_broker_side_failure_reasons() -> None:
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        failure_reason_max_length=8,
    )
    result_queue: Queue = Queue()
    thread = threading.Thread(target=lambda: result_queue.put(worker.execute(_sdk_task())))
    thread.start()
    assert executor_connection.poll(1)
    executor_connection.recv()
    executor_connection.send(object())
    thread.join(timeout=1)
    result = result_queue.get_nowait()

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "executor"


def test_process_dispatch_worker_times_out_waiting_for_completion() -> None:
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        completion_timeout_seconds=0,
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    result = worker.execute(_sdk_task())

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "executor did not return result for task task-9b4c"
    assert executor_connection.poll(1)
    assert isinstance(executor_connection.recv(), ProcessTaskAssignment)


def test_process_dispatch_worker_fails_closed_on_broken_pipe() -> None:
    class BrokenConnection:
        def send(self, item) -> None:
            del item
            raise BrokenPipeError

    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[ProcessExecutorSlot(worker_id="metadata0001", connection=BrokenConnection())],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    result = worker.execute(_sdk_task())

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "executor pipe for worker metadata0001 is broken for task task-9b4c"


def test_process_dispatch_worker_waits_for_pipe_readiness_before_success() -> None:
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        completion_timeout_seconds=1,
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    result_queue: Queue = Queue()
    thread = threading.Thread(target=lambda: result_queue.put(worker.execute(_sdk_task())))
    thread.start()
    assert executor_connection.poll(1)
    assignment = executor_connection.recv()
    time.sleep(0.15)
    executor_connection.send(
        ProcessTaskCompletion(
            task_id="task-9b4c",
            execution_id=assignment.execution_id,
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    thread.join(timeout=1)

    result = result_queue.get_nowait()
    assert result.status == "COMPLETED"


def test_process_dispatch_worker_services_attempt_fence_requests_while_waiting() -> None:
    fresh_attempt = _attempt()

    class FakeRequestQueue:
        def __init__(self) -> None:
            self.items = [ProcessAttemptFenceRequest(worker_id="metadata0001", task_id="task-9b4c")]

        def get_nowait(self):
            from queue import Empty

            if not self.items:
                raise Empty
            return self.items.pop(0)

    class FakeResponseQueue:
        def __init__(self) -> None:
            self.items = []

        def put(self, item) -> None:
            self.items.append(item)

    class FakeClient:
        def get_task(self, task_id: str):
            assert task_id == "task-9b4c"
            return fresh_attempt

    response_queue = FakeResponseQueue()
    slot, executor_connection = _process_slot()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[slot],
        attempt_fence_request_queue=FakeRequestQueue(),
        attempt_fence_response_queues={"metadata0001": response_queue},
        client=FakeClient(),
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )
    result_queue: Queue = Queue()
    thread = threading.Thread(target=lambda: result_queue.put(worker.execute(_sdk_task())))
    thread.start()
    assert executor_connection.poll(1)
    assignment = executor_connection.recv()
    executor_connection.send(
        ProcessTaskCompletion(
            task_id="task-9b4c",
            execution_id=assignment.execution_id,
            result=completed_result({"result": {"valid": True, "reason": None}}),
        )
    )
    thread.join(timeout=1)

    result = result_queue.get_nowait()

    assert result.status == "COMPLETED"
    assert response_queue.items == [ProcessAttemptFenceResponse(task_id="task-9b4c", attempt=fresh_attempt)]


def test_process_dispatch_worker_reports_attempt_fence_client_errors() -> None:
    class FakeResponseQueue:
        def __init__(self) -> None:
            self.items = []

        def put(self, item) -> None:
            self.items.append(item)

    response_queue = FakeResponseQueue()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[_process_slot()[0]],
        attempt_fence_response_queues={"metadata0001": response_queue},
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    worker._handle_attempt_fence_request(ProcessAttemptFenceRequest(worker_id="metadata0001", task_id="task-9b4c"))

    assert response_queue.items == [
        ProcessAttemptFenceResponse(task_id="task-9b4c", error="broker has no conductor client")
    ]


def test_process_dispatch_worker_reports_attempt_fence_reload_errors() -> None:
    class FakeResponseQueue:
        def __init__(self) -> None:
            self.items = []

        def put(self, item) -> None:
            self.items.append(item)

    class FakeClient:
        def get_task(self, task_id: str):
            assert task_id == "task-9b4c"
            raise RuntimeError("conductor unavailable")

    response_queue = FakeResponseQueue()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[_process_slot()[0]],
        attempt_fence_response_queues={"metadata0001": response_queue},
        client=FakeClient(),
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    worker._handle_attempt_fence_request(ProcessAttemptFenceRequest(worker_id="metadata0001", task_id="task-9b4c"))

    assert response_queue.items == [ProcessAttemptFenceResponse(task_id="task-9b4c", error="conductor unavailable")]


def test_process_dispatch_worker_rejects_invalid_attempt_fence_request() -> None:
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[_process_slot()[0]],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    worker._handle_attempt_fence_request(object())


def test_process_dispatch_worker_rejects_attempt_fence_request_without_response_queue() -> None:
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        slots=[_process_slot()[0]],
        attempt_fence_response_queues={},
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    worker._handle_attempt_fence_request(ProcessAttemptFenceRequest(worker_id="missing", task_id="task-9b4c"))


def test_thread_runner_signal_handler_stops_runner_and_restores_handlers(monkeypatch) -> None:
    handlers = {}
    restored = []

    class FakeRunner:
        def __init__(self, worker, *, configuration) -> None:
            self.worker = worker
            self.configuration = configuration
            self.stop_calls = 0

        def run(self) -> None:
            handlers[signal.SIGINT](signal.SIGINT, None)

        def stop(self) -> None:
            self.stop_calls += 1

    def fake_signal(signum, handler):
        previous = f"previous-{signum}"
        if callable(handler):
            handlers[signum] = handler
        else:
            restored.append((signum, handler))
        return previous

    monkeypatch.setattr("perago.conductor_runtime.signal.signal", fake_signal)

    run_conductor_thread_runner(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        conductor_config=ConductorConfig(server_url="http://conductor.local/api"),
        client=object(),
        workspace_root="unused",
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
        runner_cls=FakeRunner,
    )

    assert restored == [
        (signal.SIGINT, f"previous-{signal.SIGINT}"),
        (signal.SIGTERM, f"previous-{signal.SIGTERM}"),
    ]


def test_process_broker_signal_handler_stops_runner_and_restores_handlers(monkeypatch) -> None:
    handlers = {}
    restored = []

    class FakeRunner:
        def __init__(self, worker, *, configuration) -> None:
            self.worker = worker
            self.configuration = configuration

        def run(self) -> None:
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        def stop(self) -> None:
            pass

    def fake_signal(signum, handler):
        previous = f"previous-{signum}"
        if callable(handler):
            handlers[signum] = handler
        else:
            restored.append((signum, handler))
        return previous

    monkeypatch.setattr("perago.conductor_runtime.signal.signal", fake_signal)

    run_conductor_process_broker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        process_count=1,
        conductor_config=ConductorConfig(server_url="http://conductor.local/api"),
        slots=[_process_slot()[0]],
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
        runner_cls=FakeRunner,
    )

    assert restored == [
        (signal.SIGINT, f"previous-{signal.SIGINT}"),
        (signal.SIGTERM, f"previous-{signal.SIGTERM}"),
    ]


def test_load_current_attempt_via_broker_round_trips_request_and_response() -> None:
    current_attempt = _attempt()
    fresh_attempt = ConductorTaskAttempt(
        workflow_instance_id="wf-7f3d",
        task_id="task-9b4c",
        retry_count=2,
        task_def_name="metadata.validate",
        reference_task_name="validate_metadata",
        seq=3,
        iteration=1,
        status="IN_PROGRESS",
        input_data={"params": {"song_id": "song-000123", "min_duration_seconds": 30}},
        response_timeout_seconds=75,
    )

    class FakeRequestQueue:
        def __init__(self) -> None:
            self.items = []

        def put(self, item) -> None:
            self.items.append(item)

    class FakeResponseQueue:
        def get(self):
            return ProcessAttemptFenceResponse(task_id="task-9b4c", attempt=fresh_attempt)

    request_queue = FakeRequestQueue()
    loaded = load_current_attempt_via_broker(
        current_attempt,
        worker_id="metadata0001",
        request_queue=request_queue,
        response_queue=FakeResponseQueue(),
    )

    assert loaded is fresh_attempt
    assert request_queue.items == [ProcessAttemptFenceRequest(worker_id="metadata0001", task_id="task-9b4c")]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (object(), "invalid attempt-fence response"),
        (ProcessAttemptFenceResponse(task_id="other-task", attempt=_attempt()), "response for other-task"),
        (ProcessAttemptFenceResponse(task_id="task-9b4c", error="conductor failed"), "conductor failed"),
        (ProcessAttemptFenceResponse(task_id="task-9b4c"), "empty attempt-fence response"),
    ],
)
def test_load_current_attempt_via_broker_rejects_invalid_responses(response, message: str) -> None:
    class FakeRequestQueue:
        def put(self, item) -> None:
            self.item = item

    class FakeResponseQueue:
        def get(self):
            return response

    with pytest.raises(RuntimeError, match=message):
        load_current_attempt_via_broker(
            _attempt(),
            worker_id="metadata0001",
            request_queue=FakeRequestQueue(),
            response_queue=FakeResponseQueue(),
        )


def test_process_executor_loop_executes_assignment_and_returns_completion() -> None:
    connection = _FakeProcessConnection(
        [
            ProcessTaskAssignment(attempt=_attempt(), execution_id="exec-1"),
            StopProcessExecutor(),
        ]
    )

    run_process_executor_loop(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadata0001",
        workspace_root="unused",
        connection=connection,
        load_current_attempt=lambda current_attempt: current_attempt,
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    assert len(connection.sent) == 1
    completion = connection.sent[0]
    assert isinstance(completion, ProcessTaskCompletion)
    assert completion.task_id == "task-9b4c"
    assert completion.execution_id == "exec-1"
    assert completion.result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {"result": {"valid": True, "reason": None}},
    }


def test_process_executor_loop_passes_failure_reason_limit_to_execution(monkeypatch) -> None:
    captured = {}
    connection = _FakeProcessConnection(
        [
            ProcessTaskAssignment(attempt=_attempt(), execution_id="exec-1"),
            StopProcessExecutor(),
        ]
    )

    def fake_execute_polled_task(**kwargs):
        captured.update(kwargs)
        return completed_result({"result": {"valid": True, "reason": None}})

    monkeypatch.setattr("perago.conductor_runtime.execute_polled_task", fake_execute_polled_task)

    run_process_executor_loop(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadata0001",
        workspace_root="unused",
        connection=connection,
        load_current_attempt=lambda current_attempt: current_attempt,
        failure_reason_max_length=41,
    )

    assert captured["failure_reason_max_length"] == 41


def test_process_executor_loop_signal_does_not_interrupt_current_assignment(monkeypatch) -> None:
    handlers = {}

    def fake_signal(signum, handler):
        previous = handlers.get(signum)
        handlers[signum] = handler
        return previous

    def fake_execute_polled_task(**kwargs):
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return completed_result({"result": {"valid": True, "reason": None}})

    import signal

    monkeypatch.setattr("perago.conductor_runtime.signal.signal", fake_signal)
    monkeypatch.setattr("perago.conductor_runtime.execute_polled_task", fake_execute_polled_task)
    connection = _FakeProcessConnection([ProcessTaskAssignment(attempt=_attempt(), execution_id="exec-1")])

    run_process_executor_loop(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadata0001",
        workspace_root="unused",
        connection=connection,
        load_current_attempt=lambda current_attempt: current_attempt,
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    assert len(connection.sent) == 1
    assert connection.sent[0].execution_id == "exec-1"


def test_idle_process_executor_loop_exits_after_signal(monkeypatch) -> None:
    handlers = {}

    def fake_signal(signum, handler):
        previous = handlers.get(signum)
        handlers[signum] = handler
        return previous

    class FakeConnection:
        def __init__(self) -> None:
            self.calls = 0

        def poll(self, timeout=None):
            del timeout
            self.calls += 1
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return False

        def recv(self):
            raise AssertionError("idle shutdown must not read an assignment")

        def send(self, item) -> None:
            raise AssertionError("idle shutdown must not complete an assignment")

    import signal

    monkeypatch.setattr("perago.conductor_runtime.signal.signal", fake_signal)
    connection = FakeConnection()

    run_process_executor_loop(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadata0001",
        workspace_root="unused",
        connection=connection,
        load_current_attempt=lambda current_attempt: current_attempt,
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    assert connection.calls == 1


def test_process_executor_loop_ignores_invalid_assignments() -> None:
    connection = _FakeProcessConnection([object(), StopProcessExecutor()])

    run_process_executor_loop(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadata0001",
        workspace_root="unused",
        connection=connection,
        load_current_attempt=lambda current_attempt: current_attempt,
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    assert connection.sent == []


def test_execute_polled_task_uses_workspace_attempt_runner(monkeypatch, tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt(
        {
            "params": {"song_id": "song-000123"},
            "workspace": {"repo": "catalog", "branch": "main", "prefix": "songs/song-000123/features"},
        }
    )
    calls = {}

    def fake_run_workspace_task_attempt(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return completed_result({"ok": True})

    monkeypatch.setattr("perago.conductor_runtime.run_workspace_task_attempt", fake_run_workspace_task_attempt)

    result = execute_polled_task(
        task=task,
        attempt=attempt,
        workspace_root=tmp_path,
        load_current_attempt=lambda current_attempt: current_attempt,
        workspace_runtime=SimpleNamespace(
            download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
            stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: None,
            publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
            cleanup_staging=lambda staged: None,
            complete_noop_workspace=lambda workspace_input, workspace_spec, attempt: workspace_input.ref,
        ),
        owner_worker_id="featuresBuild0001",
        failure_reason_max_length=321,
    )

    assert result == completed_result({"ok": True})
    assert calls["args"][:4] == (task, attempt.input_data, attempt, tmp_path)
    assert calls["kwargs"]["owner_worker_id"] == "featuresBuild0001"
    assert calls["kwargs"]["failure_reason_max_length"] == 321


def test_run_conductor_thread_runner_builds_sdk_runner() -> None:
    created = {}

    class FakeRunner:
        def __init__(self, worker, *, configuration) -> None:
            created["worker"] = worker
            created["configuration"] = configuration

        def run(self) -> None:
            created["ran"] = True

        def stop(self) -> None:
            created["stopped"] = True

    run_conductor_thread_runner(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=3,
        conductor_config=ConductorConfig(server_url="http://conductor.local/api"),
        client=object(),
        workspace_root="unused",
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
        runner_cls=FakeRunner,
    )

    assert created["ran"] is True
    assert created["stopped"] is True
    assert created["worker"].thread_count == 3
    assert created["worker"].lease_extend_enabled is True
    assert created["worker"].get_identity() == "metadataBroker"
    assert isinstance(created["configuration"], Configuration)
    assert created["configuration"].host == "http://conductor.local/api"
    assert not hasattr(created["configuration"], "request_timeout")


def test_run_conductor_process_broker_builds_sdk_runner() -> None:
    created = {}
    slots = [_process_slot("metadata0001")[0], _process_slot("metadata0002")[0], _process_slot("metadata0003")[0]]

    class FakeRunner:
        def __init__(self, worker, *, configuration) -> None:
            created["worker"] = worker
            created["configuration"] = configuration

        def run(self) -> None:
            created["ran"] = True

        def stop(self) -> None:
            created["stopped"] = True

    run_conductor_process_broker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        process_count=3,
        conductor_config=ConductorConfig(server_url="http://conductor.local/api"),
        slots=slots,
        failure_reason_max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
        runner_cls=FakeRunner,
    )

    assert created["ran"] is True
    assert created["stopped"] is True
    assert created["worker"].thread_count == 3
    assert created["worker"].lease_extend_enabled is True
    assert created["worker"].get_identity() == "metadataBroker"
    assert created["worker"]._slots is slots
    assert isinstance(created["configuration"], Configuration)
    assert created["configuration"].host == "http://conductor.local/api"
    assert not hasattr(created["configuration"], "request_timeout")
