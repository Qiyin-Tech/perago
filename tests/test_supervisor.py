from datetime import timedelta
from types import SimpleNamespace

import pytest

from perago import ConductorConfig, LakeFSConfig, RuntimeConfig, RuntimeConfigError, restart_backoff_seconds, worker_child_specs
from perago.supervisor import (
    _broker_environment,
    _broker_process_main,
    _process_executor_main,
    _start_broker_process,
    _start_process_executor,
    _stop_worker_processes,
    _thread_runner_main,
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


def test_run_worker_supervisor_rejects_invalid_process_count(tmp_path) -> None:
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

    with pytest.raises(RuntimeConfigError, match="at least 1"):
        run_worker_supervisor(
            config=config,
            module_target="app.workers.features_build",
            process_count=0,
        )


def test_run_worker_supervisor_rejects_invalid_execution_mode(tmp_path) -> None:
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

    with pytest.raises(RuntimeConfigError, match="execution mode"):
        run_worker_supervisor(
            config=config,
            module_target="app.workers.features_build",
            process_count=1,
            execution_mode="invalid",  # type: ignore[arg-type]
        )


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


def test_broker_process_main_prepares_runtime_and_runs_dispatch_broker(monkeypatch, tmp_path) -> None:
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
    task = SimpleNamespace(controls=SimpleNamespace(publish_budget=None))
    runtime = SimpleNamespace(worker_id="workerBroker", log_file=tmp_path / "worker.log")
    conductor = object()
    queues = {
        "assignment_queue": object(),
        "completion_queue": object(),
        "attempt_fence_request_queue": object(),
        "attempt_fence_response_queues": {"worker0001": object()},
    }
    ran = {}

    monkeypatch.setattr("perago.supervisor.load_module_task", lambda module_target: task)
    monkeypatch.setattr("perago.supervisor.prepare_worker_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr("perago.supervisor.OrkesConductorRuntimeClient.from_config", lambda conductor_config: conductor)
    monkeypatch.setattr("perago.supervisor.run_conductor_process_broker", lambda **kwargs: ran.update(kwargs))

    _broker_process_main(
        config=config,
        module_target="app.workers.features_build",
        process_count=2,
        **queues,
    )

    assert ran == {
        "task": task,
        "worker_id": "workerBroker",
        "process_count": 2,
        "conductor_config": config.conductor,
        "client": conductor,
        **queues,
    }


def test_broker_process_main_requires_conductor_config(monkeypatch, tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
        conductor=None,
        lakefs=LakeFSConfig(
            endpoint_url="http://lakefs.local",
            access_key_id="lakefs-key",
            secret_access_key="lakefs-secret",
        ),
    )
    task = SimpleNamespace(controls=SimpleNamespace(publish_budget=None))
    runtime = SimpleNamespace(worker_id="workerBroker", log_file=tmp_path / "worker.log")

    monkeypatch.setattr("perago.supervisor.load_module_task", lambda module_target: task)
    monkeypatch.setattr("perago.supervisor.prepare_worker_runtime", lambda **kwargs: runtime)

    with pytest.raises(RuntimeConfigError, match="CONDUCTOR_SERVER_URL"):
        _broker_process_main(
            config=config,
            module_target="app.workers.features_build",
            process_count=1,
            assignment_queue=object(),
            completion_queue=object(),
            attempt_fence_request_queue=object(),
            attempt_fence_response_queues={},
        )


def test_process_executor_main_prepares_lakefs_and_runs_executor_loop(monkeypatch, tmp_path) -> None:
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
    task = SimpleNamespace(controls=SimpleNamespace(publish_budget=3))
    runtime = SimpleNamespace(worker_id="worker0001", log_file=tmp_path / "worker.log")
    lakefs_runtime = SimpleNamespace(
        download_workspace=object(),
        stage_workspace=object(),
        publish_workspace=object(),
        cleanup_staging=object(),
    )
    queues = {
        "assignment_queue": object(),
        "completion_queue": object(),
        "attempt_fence_request_queue": object(),
        "attempt_fence_response_queue": object(),
    }
    ran = {}
    created_lakefs = {}

    monkeypatch.setattr("perago.supervisor.load_module_task", lambda module_target: task)
    monkeypatch.setattr("perago.supervisor.prepare_worker_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(
        "perago.supervisor.LakeFSWorkspaceRuntime.from_config",
        lambda lakefs_config, publish_budget: created_lakefs.update(
            {"lakefs_config": lakefs_config, "publish_budget": publish_budget}
        )
        or lakefs_runtime,
    )
    monkeypatch.setattr("perago.supervisor.BoundLakeFSWorkspaceRuntime", lambda lakefs: lakefs)
    monkeypatch.setattr("perago.supervisor.run_process_executor_loop", lambda **kwargs: ran.update(kwargs))

    _process_executor_main(
        config=config,
        module_target="app.workers.features_build",
        child_env={"PERAGO_WORKER_ID": "worker0001"},
        **queues,
    )

    assert created_lakefs == {"lakefs_config": config.lakefs, "publish_budget": 3}
    assert ran["task"] is task
    assert ran["worker_id"] == "worker0001"
    assert ran["workspace_root"] == config.workspace_root
    assert ran["assignment_queue"] is queues["assignment_queue"]
    assert ran["completion_queue"] is queues["completion_queue"]
    assert ran["download_workspace"] is lakefs_runtime.download_workspace
    assert ran["stage_workspace"] is lakefs_runtime.stage_workspace
    assert ran["publish_workspace"] is lakefs_runtime.publish_workspace
    assert ran["cleanup_staging"] is lakefs_runtime.cleanup_staging


def test_process_executor_main_requires_lakefs_config(monkeypatch, tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
        conductor=ConductorConfig(server_url="http://conductor.local/api"),
        lakefs=None,
    )
    task = SimpleNamespace(controls=SimpleNamespace(publish_budget=None))
    runtime = SimpleNamespace(worker_id="worker0001", log_file=tmp_path / "worker.log")

    monkeypatch.setattr("perago.supervisor.load_module_task", lambda module_target: task)
    monkeypatch.setattr("perago.supervisor.prepare_worker_runtime", lambda **kwargs: runtime)

    with pytest.raises(RuntimeConfigError, match="LakeFS config"):
        _process_executor_main(
            config=config,
            module_target="app.workers.features_build",
            child_env={"PERAGO_WORKER_ID": "worker0001"},
            assignment_queue=object(),
            completion_queue=object(),
            attempt_fence_request_queue=object(),
            attempt_fence_response_queue=object(),
        )


def test_thread_runner_main_prepares_clients_and_runs_thread_runner(monkeypatch, tmp_path) -> None:
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
    task = SimpleNamespace(controls=SimpleNamespace(publish_budget=5))
    runtime = SimpleNamespace(worker_id="workerBroker", log_file=tmp_path / "worker.log")
    conductor = object()
    lakefs_runtime = SimpleNamespace(
        download_workspace=object(),
        stage_workspace=object(),
        publish_workspace=object(),
        cleanup_staging=object(),
    )
    ran = {}

    monkeypatch.setattr("perago.supervisor.load_module_task", lambda module_target: task)
    monkeypatch.setattr("perago.supervisor.prepare_worker_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr("perago.supervisor.OrkesConductorRuntimeClient.from_config", lambda conductor_config: conductor)
    monkeypatch.setattr(
        "perago.supervisor.LakeFSWorkspaceRuntime.from_config",
        lambda lakefs_config, publish_budget: lakefs_runtime,
    )
    monkeypatch.setattr("perago.supervisor.BoundLakeFSWorkspaceRuntime", lambda lakefs: lakefs)
    monkeypatch.setattr("perago.supervisor.run_conductor_thread_runner", lambda **kwargs: ran.update(kwargs))

    _thread_runner_main(config=config, module_target="app.workers.features_build", thread_count=4)

    assert ran == {
        "task": task,
        "worker_id": "workerBroker",
        "thread_count": 4,
        "conductor_config": config.conductor,
        "client": conductor,
        "workspace_root": config.workspace_root,
        "download_workspace": lakefs_runtime.download_workspace,
        "stage_workspace": lakefs_runtime.stage_workspace,
        "publish_workspace": lakefs_runtime.publish_workspace,
        "cleanup_staging": lakefs_runtime.cleanup_staging,
    }


def test_thread_runner_main_requires_runtime_configs(monkeypatch, tmp_path) -> None:
    base_config = {
        "workspace_root": tmp_path / "workspaces",
        "log_root": tmp_path / "logs",
        "log_file_max_size": 1024,
        "log_retention": timedelta(days=1),
        "worker_id_prefix": "worker",
    }
    task = SimpleNamespace(controls=SimpleNamespace(publish_budget=None))
    runtime = SimpleNamespace(worker_id="workerBroker", log_file=tmp_path / "worker.log")

    monkeypatch.setattr("perago.supervisor.load_module_task", lambda module_target: task)
    monkeypatch.setattr("perago.supervisor.prepare_worker_runtime", lambda **kwargs: runtime)

    with pytest.raises(RuntimeConfigError, match="CONDUCTOR_SERVER_URL"):
        _thread_runner_main(
            config=RuntimeConfig(
                **base_config,
                conductor=None,
                lakefs=LakeFSConfig(
                    endpoint_url="http://lakefs.local",
                    access_key_id="lakefs-key",
                    secret_access_key="lakefs-secret",
                ),
            ),
            module_target="app.workers.features_build",
            thread_count=1,
        )

    with pytest.raises(RuntimeConfigError, match="LakeFS config"):
        _thread_runner_main(
            config=RuntimeConfig(
                **base_config,
                conductor=ConductorConfig(server_url="http://conductor.local/api"),
                lakefs=None,
            ),
            module_target="app.workers.features_build",
            thread_count=1,
        )


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
