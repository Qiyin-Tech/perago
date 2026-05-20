from datetime import timedelta

import pytest

from perago import ConductorConfig, LakeFSConfig, RuntimeConfig, RuntimeConfigError, restart_backoff_seconds, worker_child_specs
from perago.supervisor import (
    _broker_environment,
    _start_broker_process,
    _start_process_executor,
    _stop_worker_processes,
    run_worker_supervisor,
)


class FakeProcess:
    def __init__(self) -> None:
        self.alive = True
        self.events: list[tuple[str, int | None]] = []

    def join(self, timeout: int) -> None:
        self.events.append(("join", timeout))

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.events.append(("terminate", None))

    def kill(self) -> None:
        self.events.append(("kill", None))
        self.alive = False


def test_restart_backoff_sequence_caps_at_maximum() -> None:
    assert [restart_backoff_seconds(index) for index in range(7)] == [1, 2, 4, 8, 16, 30, 30]


def test_restart_backoff_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="restart_count"):
        restart_backoff_seconds(-1)


def test_worker_child_specs_assign_stable_slot_worker_ids() -> None:
    specs = worker_child_specs(
        base_env={},
        module_target="app.workers.features_build",
        process_count=4,
    )

    assert [spec.slot for spec in specs] == [1, 2, 3, 4]
    assert [spec.worker_id for spec in specs] == [
        "appworkersfeaturesbuild0001",
        "appworkersfeaturesbuild0002",
        "appworkersfeaturesbuild0003",
        "appworkersfeaturesbuild0004",
    ]


def test_worker_child_specs_reuse_configured_prefix() -> None:
    specs = worker_child_specs(
        base_env={"PERAGO_WORKER_ID_PREFIX": "prodAFeaturesBuild"},
        module_target="app.workers.features_build",
        process_count=2,
    )

    assert [spec.worker_id for spec in specs] == [
        "prodAFeaturesBuild0001",
        "prodAFeaturesBuild0002",
    ]


def test_worker_child_specs_reject_invalid_process_count() -> None:
    with pytest.raises(RuntimeConfigError, match="at least 1"):
        worker_child_specs(base_env={}, module_target="app.workers.features_build", process_count=0)


def test_run_worker_supervisor_uses_thread_runner_without_child_processes(monkeypatch, tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
        conductor=ConductorConfig(server_url="http://conductor.local/api"),
        lakefs=LakeFSConfig(
            endpoint_url="http://lakefs.local",
            access_key_id="lakefs-key",
            secret_access_key="lakefs-secret",
        ),
    )
    started = {}

    def fake_thread_runner_main(**kwargs) -> None:
        started.update(kwargs)

    monkeypatch.setattr("perago.supervisor._thread_runner_main", fake_thread_runner_main)
    monkeypatch.setattr(
        "perago.supervisor._start_process_executor",
        lambda **kwargs: pytest.fail("thread mode must not start executor child processes"),
    )

    run_worker_supervisor(
        config=config,
        module_target="app.workers.features_build",
        process_count=3,
        execution_mode="thread",
    )

    assert started == {
        "config": config,
        "module_target": "app.workers.features_build",
        "thread_count": 3,
    }


def test_broker_environment_derives_visible_worker_id() -> None:
    assert _broker_environment("featuresBuild") == {
        "PERAGO_WORKER_ID_PREFIX": "featuresBuild",
        "PERAGO_WORKER_ID": "featuresBuildBroker",
    }


def test_run_worker_supervisor_process_mode_starts_broker_and_executors(monkeypatch, tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
        conductor=ConductorConfig(server_url="http://conductor.local/api"),
        lakefs=LakeFSConfig(
            endpoint_url="http://lakefs.local",
            access_key_id="lakefs-key",
            secret_access_key="lakefs-secret",
        ),
    )
    started = {"executors": []}
    stopped = []

    class FakeBroker:
        exitcode = 7

        def is_alive(self) -> bool:
            return False

    class FakeExecutor:
        def __init__(self, worker_id: str) -> None:
            self.worker_id = worker_id

        def is_alive(self) -> bool:
            return True

    def fake_start_broker_process(**kwargs):
        started["broker"] = kwargs
        return FakeBroker()

    def fake_start_process_executor(**kwargs):
        started["executors"].append(kwargs)
        return FakeExecutor(kwargs["spec"].worker_id)

    monkeypatch.setattr("perago.supervisor._start_broker_process", fake_start_broker_process)
    monkeypatch.setattr("perago.supervisor._start_process_executor", fake_start_process_executor)
    monkeypatch.setattr("perago.supervisor._stop_worker_processes", lambda processes: stopped.extend(processes))

    run_worker_supervisor(
        config=config,
        module_target="app.workers.features_build",
        process_count=2,
        execution_mode="process",
    )

    assert started["broker"]["config"] is config
    assert started["broker"]["module_target"] == "app.workers.features_build"
    assert started["broker"]["process_count"] == 2
    assert len(started["executors"]) == 2
    assert [item["spec"].worker_id for item in started["executors"]] == ["worker0001", "worker0002"]
    assert all(item["assignment_queue"] is started["broker"]["assignment_queue"] for item in started["executors"])
    assert all(item["completion_queue"] is started["broker"]["completion_queue"] for item in started["executors"])
    assert all(
        item["attempt_fence_request_queue"] is started["broker"]["attempt_fence_request_queue"]
        for item in started["executors"]
    )
    assert [item["attempt_fence_response_queue"] for item in started["executors"]] == [
        started["broker"]["attempt_fence_response_queues"]["worker0001"],
        started["broker"]["attempt_fence_response_queues"]["worker0002"],
    ]
    assert len(stopped) == 3


def test_process_runtime_start_helpers_use_named_processes(monkeypatch, tmp_path) -> None:
    created = []

    class FakeProcess:
        def __init__(self, *, target, kwargs, name) -> None:
            self.target = target
            self.kwargs = kwargs
            self.name = name
            self.started = False
            created.append(self)

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr("perago.supervisor.multiprocessing.Process", FakeProcess)
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
        conductor=ConductorConfig(server_url="http://conductor.local/api"),
        lakefs=LakeFSConfig(
            endpoint_url="http://lakefs.local",
            access_key_id="lakefs-key",
            secret_access_key="lakefs-secret",
        ),
    )
    assignment_queue = object()
    completion_queue = object()
    attempt_fence_request_queue = object()
    attempt_fence_response_queue = object()
    attempt_fence_response_queues = {"worker0001": attempt_fence_response_queue}
    spec = worker_child_specs(
        base_env={"PERAGO_WORKER_ID_PREFIX": "worker"},
        module_target="app.workers.features_build",
        process_count=1,
    )[0]

    broker = _start_broker_process(
        config=config,
        module_target="app.workers.features_build",
        process_count=3,
        assignment_queue=assignment_queue,
        completion_queue=completion_queue,
        attempt_fence_request_queue=attempt_fence_request_queue,
        attempt_fence_response_queues=attempt_fence_response_queues,
    )
    executor = _start_process_executor(
        config=config,
        module_target="app.workers.features_build",
        spec=spec,
        assignment_queue=assignment_queue,
        completion_queue=completion_queue,
        attempt_fence_request_queue=attempt_fence_request_queue,
        attempt_fence_response_queue=attempt_fence_response_queue,
    )

    assert broker.started is True
    assert executor.started is True
    assert created[0].name == "perago-conductor-broker"
    assert created[0].kwargs["process_count"] == 3
    assert created[0].kwargs["attempt_fence_request_queue"] is attempt_fence_request_queue
    assert created[0].kwargs["attempt_fence_response_queues"] is attempt_fence_response_queues
    assert created[1].name == "perago-executor-0001"
    assert created[1].kwargs["child_env"]["PERAGO_WORKER_ID"] == "worker0001"
    assert created[1].kwargs["assignment_queue"] is assignment_queue
    assert created[1].kwargs["completion_queue"] is completion_queue
    assert created[1].kwargs["attempt_fence_request_queue"] is attempt_fence_request_queue
    assert created[1].kwargs["attempt_fence_response_queue"] is attempt_fence_response_queue


def test_stop_worker_processes_escalates_after_grace_periods() -> None:
    process = FakeProcess()

    _stop_worker_processes([process])  # type: ignore[list-item]

    assert process.events == [
        ("join", 10),
        ("terminate", None),
        ("join", 5),
        ("kill", None),
        ("join", 5),
    ]
