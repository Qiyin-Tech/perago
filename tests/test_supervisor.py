import json
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest

from perago import ConductorConfig, LakeFSConfig, RuntimeConfig, RuntimeConfigError, restart_backoff_seconds, worker_child_specs
from perago.supervisor import (
    SUPERVISOR_WORKSPACE_LOCK_FILE,
    SupervisorWorkspaceLock,
    WorkspaceGCLoop,
    _active_process_workspace_owners,
    _broker_environment,
    _broker_process_main,
    _duration_seconds,
    _pid_is_alive,
    _process_executor_main,
    _read_supervisor_workspace_lock,
    _start_broker_process,
    _start_process_executor,
    _stop_worker_processes,
    _targeted_workspace_gc,
    _thread_runner_main,
    _unlink_supervisor_workspace_lock_if_same,
    acquire_supervisor_workspace_lock,
    run_worker_supervisor,
)


class FakeProcess:
    def __init__(self, *, pid: int | None = 1234) -> None:
        self.alive = True
        self.pid = pid
        self.events: list[tuple[str, int | None]] = []

    def join(self, timeout: int | float | None) -> None:
        self.events.append(("join", timeout))

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.events.append(("terminate", None))

    def kill(self) -> None:
        self.events.append(("kill", None))
        self.alive = False


class FakeExecutorProcess:
    def __init__(self, *, pid: int | None, alive: bool) -> None:
        self.pid = pid
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


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
    assert not (config.workspace_root / SUPERVISOR_WORKSPACE_LOCK_FILE).exists()


def test_run_worker_supervisor_writes_workspace_lock_while_running(monkeypatch, tmp_path) -> None:
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
    observed = {}

    def fake_thread_runner_main(**kwargs) -> None:
        lock_path = kwargs["config"].workspace_root / SUPERVISOR_WORKSPACE_LOCK_FILE
        observed["lock"] = json.loads(lock_path.read_text(encoding="utf-8"))

    monkeypatch.setattr("perago.supervisor._thread_runner_main", fake_thread_runner_main)

    run_worker_supervisor(
        config=config,
        module_target="app.workers.features_build",
        process_count=1,
        execution_mode="thread",
    )

    assert observed == {"lock": {"supervisor_pid": os.getpid()}}
    assert not (config.workspace_root / SUPERVISOR_WORKSPACE_LOCK_FILE).exists()


def test_run_worker_supervisor_rejects_active_workspace_lock(monkeypatch, tmp_path) -> None:
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
    config.workspace_root.mkdir(parents=True)
    lock_path = config.workspace_root / SUPERVISOR_WORKSPACE_LOCK_FILE
    lock_path.write_text(json.dumps({"supervisor_pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(
        "perago.supervisor._thread_runner_main",
        lambda **kwargs: pytest.fail("locked workspace root must not start supervisor"),
    )

    with pytest.raises(RuntimeConfigError, match="already locked"):
        run_worker_supervisor(
            config=config,
            module_target="app.workers.features_build",
            process_count=1,
            execution_mode="thread",
        )


def test_run_worker_supervisor_replaces_stale_workspace_lock(monkeypatch, tmp_path) -> None:
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
    config.workspace_root.mkdir(parents=True)
    lock_path = config.workspace_root / SUPERVISOR_WORKSPACE_LOCK_FILE
    lock_path.write_text(json.dumps({"supervisor_pid": 987654321}), encoding="utf-8")
    started = {}

    def fake_kill(pid: int, signal_number: int) -> None:
        assert signal_number == 0
        if pid == 987654321:
            raise ProcessLookupError

    monkeypatch.setattr("perago.supervisor.os.kill", fake_kill)
    monkeypatch.setattr("perago.supervisor._thread_runner_main", lambda **kwargs: started.update(kwargs))

    run_worker_supervisor(
        config=config,
        module_target="app.workers.features_build",
        process_count=1,
        execution_mode="thread",
    )

    assert started["config"] is config
    assert not lock_path.exists()


def test_acquire_supervisor_workspace_lock_recovers_when_lock_disappears_before_stat(monkeypatch, tmp_path) -> None:
    real_open = os.open
    real_stat = type(tmp_path).stat
    lock_path = tmp_path / SUPERVISOR_WORKSPACE_LOCK_FILE
    lock_path.write_text(json.dumps({"supervisor_pid": os.getpid()}), encoding="utf-8")
    open_calls = {"count": 0}

    def fake_open(path, flags, mode=0o777, *, dir_fd=None):
        del flags, mode, dir_fd
        if Path(path) == lock_path and open_calls["count"] == 0:
            open_calls["count"] += 1
            raise FileExistsError
        return real_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)

    def fake_stat(self, *args, **kwargs):
        if self == lock_path and open_calls["count"] == 1:
            open_calls["count"] += 1
            lock_path.unlink()
            raise FileNotFoundError
        return real_stat(self, *args, **kwargs)

    from pathlib import Path

    monkeypatch.setattr("perago.supervisor.os.open", fake_open)
    monkeypatch.setattr(type(tmp_path), "stat", fake_stat)

    lock = acquire_supervisor_workspace_lock(tmp_path)
    lock.release()

    assert open_calls["count"] == 2


def test_acquire_supervisor_workspace_lock_recovers_when_lock_disappears_before_read(monkeypatch, tmp_path) -> None:
    real_open = os.open
    lock_path = tmp_path / SUPERVISOR_WORKSPACE_LOCK_FILE
    lock_path.write_text(json.dumps({"supervisor_pid": os.getpid()}), encoding="utf-8")
    open_calls = {"count": 0}
    read_calls = {"count": 0}

    def fake_open(path, flags, mode=0o777, *, dir_fd=None):
        del flags, mode, dir_fd
        if Path(path) == lock_path and open_calls["count"] == 0:
            open_calls["count"] += 1
            raise FileExistsError
        return real_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)

    def fake_read(path):
        if path == lock_path and read_calls["count"] == 0:
            read_calls["count"] += 1
            lock_path.unlink()
            raise FileNotFoundError
        return {"supervisor_pid": os.getpid()}

    from pathlib import Path

    monkeypatch.setattr("perago.supervisor.os.open", fake_open)
    monkeypatch.setattr("perago.supervisor._read_supervisor_workspace_lock", fake_read)

    lock = acquire_supervisor_workspace_lock(tmp_path)
    lock.release()

    assert open_calls["count"] == 1
    assert read_calls["count"] == 1


def test_supervisor_workspace_lock_release_ignores_unreadable_lock(tmp_path) -> None:
    lock_path = tmp_path / SUPERVISOR_WORKSPACE_LOCK_FILE
    lock_path.write_text("{bad-json", encoding="utf-8")
    lock = SupervisorWorkspaceLock(path=lock_path, supervisor_pid=os.getpid())

    lock.release()

    assert lock_path.exists()


def test_supervisor_workspace_lock_release_ignores_concurrent_unlink(monkeypatch, tmp_path) -> None:
    lock_path = tmp_path / SUPERVISOR_WORKSPACE_LOCK_FILE
    lock_path.write_text(json.dumps({"supervisor_pid": os.getpid()}), encoding="utf-8")
    lock = SupervisorWorkspaceLock(path=lock_path, supervisor_pid=os.getpid())

    def fake_unlink(self) -> None:
        if self == lock_path:
            raise FileNotFoundError
        Path.unlink(self)

    from pathlib import Path

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    lock.release()


def test_read_supervisor_workspace_lock_rejects_non_object(tmp_path) -> None:
    lock_path = tmp_path / SUPERVISOR_WORKSPACE_LOCK_FILE
    lock_path.write_text("[]", encoding="utf-8")

    with pytest.raises(RuntimeConfigError, match="must contain an object"):
        _read_supervisor_workspace_lock(lock_path)


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
    monkeypatch.setattr("perago.supervisor._stop_worker_processes", lambda processes, **kwargs: stopped.extend(processes))

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


def test_active_process_workspace_owners_uses_live_child_worker_id_and_pid() -> None:
    specs = worker_child_specs(
        base_env={"PERAGO_WORKER_ID_PREFIX": "worker"},
        module_target="app.workers.features_build",
        process_count=3,
    )
    executors = {
        1: (specs[0], FakeExecutorProcess(pid=101, alive=True), 0),
        2: (specs[1], FakeExecutorProcess(pid=102, alive=False), 0),
        3: (specs[2], FakeExecutorProcess(pid=None, alive=True), 0),
    }

    active = _active_process_workspace_owners(executors)  # type: ignore[arg-type]

    assert active == {("worker0001", 101)}


def test_workspace_gc_loop_runs_nonblocking_and_stops(monkeypatch, tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
        workspace_gc_interval=timedelta(hours=1),
    )
    calls = []

    def fake_gc(workspace_root, *, ttl, active_process_owners):
        calls.append((workspace_root, ttl, active_process_owners))
        return []

    monkeypatch.setattr("perago.supervisor.garbage_collect_attempt_workspaces", fake_gc)

    loop = WorkspaceGCLoop(config=config, active_process_owners=lambda: {("worker0001", 101)})
    removed = loop.run_once()
    loop.start()
    loop.stop()

    assert removed == []
    assert calls[0] == (config.workspace_root, config.workspace_gc_ttl, {("worker0001", 101)})
    assert len(calls) >= 1


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
        complete_noop_workspace=object(),
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
    assert ran["complete_noop_workspace"] is lakefs_runtime.complete_noop_workspace


def test_process_executor_main_ignores_publish_budget_for_read_only_workspace(monkeypatch, tmp_path) -> None:
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
    task = SimpleNamespace(
        workspace=SimpleNamespace(read_only=True),
        controls=SimpleNamespace(publish_budget=3),
    )
    runtime = SimpleNamespace(worker_id="worker0001", log_file=tmp_path / "worker.log")
    lakefs_runtime = SimpleNamespace(
        download_workspace=object(),
        stage_workspace=object(),
        publish_workspace=object(),
        cleanup_staging=object(),
        complete_noop_workspace=object(),
    )
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
    monkeypatch.setattr("perago.supervisor.run_process_executor_loop", lambda **kwargs: None)

    _process_executor_main(
        config=config,
        module_target="app.workers.read_only_budget",
        child_env={"PERAGO_WORKER_ID": "worker0001"},
        assignment_queue=object(),
        completion_queue=object(),
        attempt_fence_request_queue=object(),
        attempt_fence_response_queue=object(),
    )

    assert created_lakefs == {"lakefs_config": config.lakefs, "publish_budget": None}


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
        complete_noop_workspace=object(),
    )
    ran = {}

    monkeypatch.setattr("perago.supervisor.load_module_task", lambda module_target: task)
    monkeypatch.setattr("perago.supervisor.prepare_worker_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr("perago.supervisor.OrkesConductorRuntimeClient.from_config", lambda conductor_config: conductor)
    monkeypatch.setattr(
        "perago.supervisor.LakeFSWorkspaceRuntime.from_config",
        lambda lakefs_config, publish_budget: lakefs_runtime,
    )
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
        "complete_noop_workspace": lakefs_runtime.complete_noop_workspace,
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


def test_stop_worker_processes_waits_without_default_force_kill() -> None:
    process = FakeProcess()

    _stop_worker_processes([process])  # type: ignore[list-item]

    assert process.events == [("join", None)]


def test_stop_worker_processes_kills_only_after_configured_deadline(tmp_path) -> None:
    process = FakeProcess(pid=4321)

    _stop_worker_processes(
        [process],  # type: ignore[list-item]
        force_kill_after=timedelta(seconds=3),
        workspace_root=tmp_path,
        process_worker_ids={id(process): "worker0001"},
    )

    assert process.events == [
        ("join", 3.0),
        ("kill", None),
        ("join", 5),
    ]


def test_unlink_supervisor_workspace_lock_handles_races(monkeypatch, tmp_path) -> None:
    lock_path = tmp_path / SUPERVISOR_WORKSPACE_LOCK_FILE
    lock_path.write_text("lock", encoding="utf-8")
    lock_stat = lock_path.stat()
    lock_path.unlink()

    _unlink_supervisor_workspace_lock_if_same(lock_path, lock_stat)

    lock_path.write_text("lock", encoding="utf-8")
    other_path = tmp_path / "other.lock"
    other_path.write_text("other", encoding="utf-8")

    _unlink_supervisor_workspace_lock_if_same(lock_path, other_path.stat())

    assert lock_path.exists()

    lock_stat = lock_path.stat()
    original_unlink = type(lock_path).unlink

    def fake_unlink(self) -> None:
        if self == lock_path:
            raise FileNotFoundError
        original_unlink(self)

    monkeypatch.setattr(type(lock_path), "unlink", fake_unlink)

    _unlink_supervisor_workspace_lock_if_same(lock_path, lock_stat)


def test_pid_is_alive_handles_missing_invalid_and_permission_denied(monkeypatch) -> None:
    def fake_kill(pid: int, signal_number: int) -> None:
        assert signal_number == 0
        if pid == 111:
            raise ProcessLookupError
        if pid == 222:
            raise PermissionError

    monkeypatch.setattr("perago.supervisor.os.kill", fake_kill)

    assert _pid_is_alive(0) is False
    assert _pid_is_alive(111) is False
    assert _pid_is_alive(222) is True
    assert _pid_is_alive(333) is True


def test_targeted_workspace_gc_skips_process_without_pid(monkeypatch, tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
    )
    monkeypatch.setattr(
        "perago.supervisor.garbage_collect_workspace_owner",
        lambda *args, **kwargs: pytest.fail("processes without pids must not trigger targeted GC"),
    )

    _targeted_workspace_gc(config=config, worker_id="worker0001", process=SimpleNamespace(pid=None))


def test_targeted_workspace_gc_collects_dead_process_owner(monkeypatch, tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
    )
    removed_workspace = tmp_path / "workspaces" / "task_id=1"
    called = {}

    def fake_gc(workspace_root, *, owner_worker_id, owner_pid):
        called.update(
            {
                "workspace_root": workspace_root,
                "owner_worker_id": owner_worker_id,
                "owner_pid": owner_pid,
            }
        )
        return [removed_workspace]

    monkeypatch.setattr("perago.supervisor.garbage_collect_workspace_owner", fake_gc)

    _targeted_workspace_gc(config=config, worker_id="worker0001", process=SimpleNamespace(pid=4321))

    assert called == {
        "workspace_root": config.workspace_root,
        "owner_worker_id": "worker0001",
        "owner_pid": 4321,
    }


def test_workspace_gc_loop_run_logs_removed_workspaces_and_errors(monkeypatch, tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
        workspace_gc_interval=timedelta(seconds=0),
    )
    loop = WorkspaceGCLoop(config=config, active_process_owners=lambda: set())
    calls = []

    class FakeStop:
        def __init__(self) -> None:
            self.waits = 0

        def is_set(self) -> bool:
            return self.waits >= 2

        def wait(self, interval: float) -> bool:
            assert interval == 0.001
            self.waits += 1
            return self.waits >= 2

    def fake_run_once():
        calls.append("run")
        if len(calls) == 1:
            return [tmp_path / "removed"]
        raise RuntimeError("boom")

    monkeypatch.setattr(loop, "_stop", FakeStop())
    monkeypatch.setattr(loop, "run_once", fake_run_once)

    loop._run()

    assert calls == ["run", "run"]


def test_duration_seconds_never_returns_zero() -> None:
    assert _duration_seconds(timedelta(seconds=-1)) == 0.001
