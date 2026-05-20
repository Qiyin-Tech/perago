from conductor.client.http.models.task import Task

from perago.conductor_runtime import (
    ConductorTaskAttempt,
    OrkesConductorRuntimeClient,
    PeragoProcessDispatchWorker,
    PeragoThreadWorker,
    ProcessTaskAssignment,
    ProcessTaskCompletion,
    conductor_task_to_attempt,
    run_conductor_thread_runner,
    runtime_result_to_sdk_task_result,
    run_worker_poll_loop,
)
from perago.config import ConductorConfig
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


def test_runtime_result_to_sdk_task_result_maps_completed_and_failures() -> None:
    attempt = _attempt()

    completed = runtime_result_to_sdk_task_result(
        attempt,
        completed_result({"result": {"valid": True}}),
        worker_id="worker-1",
    )
    failed = runtime_result_to_sdk_task_result(attempt, failed_result("bad input"), worker_id="worker-1")
    terminal = runtime_result_to_sdk_task_result(
        attempt,
        terminal_failed_result("pre guardrail"),
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
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: None,
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
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
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: None,
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
    )

    result = worker.execute(task)

    assert result.workflow_instance_id == "wf-7f3d"
    assert result.task_id == "task-9b4c"
    assert result.worker_id == "metadataBroker"
    assert result.status == "COMPLETED"
    assert result.output_data == {"result": {"valid": True, "reason": None}}


def test_process_dispatch_worker_configures_sdk_worker_contract() -> None:
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=4,
        assignment_queue=object(),
        completion_queue=object(),
    )

    assert worker.get_identity() == "metadataBroker"
    assert worker.thread_count == 4
    assert worker.lease_extend_enabled is True
    assert worker.register_task_def is False
    assert worker.register_schema is False
    assert worker.get_task_definition_name() == "metadata.validate"


def test_process_dispatch_worker_dispatches_attempt_and_maps_completion() -> None:
    class FakeAssignmentQueue:
        def __init__(self) -> None:
            self.items = []

        def put(self, item) -> None:
            self.items.append(item)

    class FakeCompletionQueue:
        def get(self):
            return ProcessTaskCompletion(
                task_id="task-9b4c",
                result=completed_result({"result": {"valid": True, "reason": None}}),
            )

    assignment_queue = FakeAssignmentQueue()
    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        assignment_queue=assignment_queue,
        completion_queue=FakeCompletionQueue(),
    )
    task = Task(
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

    result = worker.execute(task)

    assert len(assignment_queue.items) == 1
    assignment = assignment_queue.items[0]
    assert isinstance(assignment, ProcessTaskAssignment)
    assert assignment.attempt.task_id == "task-9b4c"
    assert assignment.attempt.response_timeout_seconds == 75
    assert result.workflow_instance_id == "wf-7f3d"
    assert result.task_id == "task-9b4c"
    assert result.worker_id == "metadataBroker"
    assert result.status == "COMPLETED"
    assert result.output_data == {"result": {"valid": True, "reason": None}}


def test_process_dispatch_worker_fails_closed_on_mismatched_completion() -> None:
    class FakeAssignmentQueue:
        def put(self, item) -> None:
            self.item = item

    class FakeCompletionQueue:
        def get(self):
            return ProcessTaskCompletion(
                task_id="other-task",
                result=completed_result({"result": {"valid": True, "reason": None}}),
            )

    worker = PeragoProcessDispatchWorker(
        task=load_module_task("app.workers.metadata_validate"),
        worker_id="metadataBroker",
        thread_count=1,
        assignment_queue=FakeAssignmentQueue(),
        completion_queue=FakeCompletionQueue(),
    )
    task = Task(
        workflow_instance_id="wf-7f3d",
        task_id="task-9b4c",
        retry_count=2,
        task_def_name="metadata.validate",
        reference_task_name="validate_metadata",
        seq=3,
        status="IN_PROGRESS",
        input_data={"params": {"song_id": "song-000123", "min_duration_seconds": 30}},
    )

    result = worker.execute(task)

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "executor returned completion for task other-task; expected task-9b4c"


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
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: None,
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
        runner_cls=FakeRunner,
    )

    assert created["ran"] is True
    assert created["stopped"] is True
    assert created["worker"].thread_count == 3
    assert created["worker"].lease_extend_enabled is True
    assert created["worker"].get_identity() == "metadataBroker"


def test_orkes_conductor_update_task_uses_configured_request_timeout() -> None:
    class FakeTaskResourceApi:
        def __init__(self) -> None:
            self.calls = []

        def update_task(self, task_result, **kwargs):
            self.calls.append((task_result, kwargs))

    class FakeTaskClient:
        def __init__(self) -> None:
            self.taskResourceApi = FakeTaskResourceApi()

        def update_task(self, task_result):
            raise AssertionError("expected direct taskResourceApi update when timeout is configured")

    task_client = FakeTaskClient()
    client = OrkesConductorRuntimeClient(
        task_client=task_client,
        metadata_client=object(),
        task_update_timeout_seconds=15,
    )
    attempt = _attempt()

    client.update_task(attempt, completed_result({"result": {"valid": True}}), worker_id="worker-1")

    task_result, kwargs = task_client.taskResourceApi.calls[0]
    assert task_result.task_id == "task-9b4c"
    assert kwargs == {"_request_timeout": 15}


def test_workspace_free_poll_execute_update_flow() -> None:
    task = load_module_task("app.workers.metadata_validate")
    attempt = _attempt()

    class FakeConductor:
        def __init__(self) -> None:
            self.updated = None

        def taskdef_exists(self, task_name: str) -> bool:
            del task_name
            return True

        def poll_task(self, task_name: str, *, worker_id: str):
            assert task_name == "metadata.validate"
            assert worker_id == "worker-1"
            return attempt if self.updated is None else None

        def get_task(self, task_id: str):
            assert task_id == "task-9b4c"
            return attempt

        def update_task(self, attempt_arg, result, *, worker_id: str) -> None:
            assert attempt_arg == attempt
            assert worker_id == "worker-1"
            self.updated = result

    conductor = FakeConductor()

    run_worker_poll_loop(
        task=task,
        client=conductor,
        worker_id="worker-1",
        workspace_root="unused",
        should_stop=lambda: conductor.updated is not None,
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: None,
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
        poll_empty_sleep_seconds=0,
        poll_error_backoff_seconds=0,
    )

    assert conductor.updated.conductor_payload() == {
        "status": "COMPLETED",
        "output": {"result": {"valid": True, "reason": None}},
    }
