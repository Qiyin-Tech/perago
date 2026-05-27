from __future__ import annotations

import json
import multiprocessing
import os
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import FrameType
from typing import Any

from loguru import logger

from perago.conductor_runtime import (
    OrkesConductorRuntimeClient,
    ProcessExecutorExited,
    ProcessExecutorSlot,
    ProcessExecutorStarted,
    StopProcessExecutor,
    load_current_attempt_via_broker,
    run_conductor_process_broker,
    run_conductor_thread_runner,
    run_process_executor_loop,
)
from perago.config import ExecutionMode, RuntimeConfig, child_environment
from perago.errors import RuntimeConfigError
from perago.lakefs_runtime import LakeFSWorkspaceRuntime
from perago.task import load_module_task
from perago.worker_runtime import prepare_worker_runtime
from perago.workspace import garbage_collect_attempt_workspaces
from perago.workspace import garbage_collect_workspace_owner, sweep_abandoned_attempt_workspaces


RESTART_BACKOFF_SECONDS = (1, 2, 4, 8, 16)
MAX_RESTART_BACKOFF_SECONDS = 30
SUPERVISOR_WORKSPACE_LOCK_FILE = ".perago-supervisor.lock"
SUPERVISOR_PROCESS_POLL_INTERVAL_SECONDS = 0.5
PROCESS_JOIN_TIMEOUT_SECONDS = 5
WORKSPACE_GC_THREAD_JOIN_TIMEOUT_SECONDS = 5
MIN_DURATION_SECONDS = 0.001


@dataclass(frozen=True)
class WorkerChildSpec:
    """
    Supervisor launch specification for one worker child slot.

    ``WorkerChildSpec`` carries the per-child environment derived by the
    supervisor before a multiprocessing child is started. The slot number is
    stable across restarts, so a restarted child keeps the same worker id.

    Parameters
    ----------
    slot : int
        One-based supervisor slot assigned to the worker child.
    env : dict of str to str
        Environment mapping passed to the child process. It includes
        ``PERAGO_WORKER_ID_PREFIX`` and ``PERAGO_WORKER_ID``.

    Attributes
    ----------
    slot : int
        One-based supervisor slot assigned to the worker child.
    env : dict of str to str
        Environment mapping passed to the child process.
    worker_id : str
        Worker id read from ``env["PERAGO_WORKER_ID"]``.

    See Also
    --------
    worker_child_specs : Build child specifications for ``perago start -j``.
    restart_backoff_seconds : Delay used when a child exits and is restarted.

    Notes
    -----
    The dataclass is frozen, but the ``env`` mapping itself is a regular
    dictionary supplied by the caller.

    Examples
    --------
    >>> spec = WorkerChildSpec(slot=1, env={"PERAGO_WORKER_ID": "features0001"})
    >>> spec.worker_id
    'features0001'
    """

    slot: int
    env: dict[str, str]

    @property
    def worker_id(self) -> str:
        """Return the worker id assigned to this child slot."""
        return self.env["PERAGO_WORKER_ID"]


def restart_backoff_seconds(restart_count: int) -> int:
    """
    Return the supervisor restart delay for a child process.

    The sequence is intentionally short and bounded so a crashing child backs
    off without permanently stopping the supervisor. Counts beyond the explicit
    sequence use the maximum delay.

    Parameters
    ----------
    restart_count : int
        Zero-based number of previous restarts for the child slot.

    Returns
    -------
    int
        Delay in seconds before the supervisor starts the replacement process.

    Raises
    ------
    ValueError
        If ``restart_count`` is negative.

    See Also
    --------
    worker_child_specs : Build stable child slots that reuse this backoff.
    WorkerChildSpec : Per-slot child specification restarted by the supervisor.

    Examples
    --------
    >>> [restart_backoff_seconds(index) for index in range(7)]
    [1, 2, 4, 8, 16, 30, 30]
    """
    if restart_count < 0:
        raise ValueError("restart_count must be >= 0")
    if restart_count < len(RESTART_BACKOFF_SECONDS):
        return RESTART_BACKOFF_SECONDS[restart_count]
    return MAX_RESTART_BACKOFF_SECONDS


def worker_child_specs(
    *,
    base_env: dict[str, str],
    module_target: str,
    process_count: int,
) -> list[WorkerChildSpec]:
    """
    Build stable worker child specifications for a supervisor run.

    The supervisor uses these specs for ``perago start -j``. Each child receives
    a one-based slot and a deterministic ``PERAGO_WORKER_ID`` derived from the
    configured prefix or, when no prefix is configured, from ``module_target``.

    Parameters
    ----------
    base_env : dict of str to str
        Base environment copied into every child. ``PERAGO_WORKER_ID_PREFIX`` is
        honored when present.
    module_target : str
        Python import path of the single task module served by all child
        workers.
    process_count : int
        Number of worker child specs to create. Must be at least one.

    Returns
    -------
    list of WorkerChildSpec
        Child launch specs ordered by ascending slot.

    Raises
    ------
    RuntimeConfigError
        If ``process_count`` is less than one or if the worker id prefix is
        invalid.

    See Also
    --------
    WorkerChildSpec : Value object returned for each child slot.
    restart_backoff_seconds : Restart delay used after a child exits.

    Examples
    --------
    >>> [spec.worker_id for spec in worker_child_specs(
    ...     base_env={"PERAGO_WORKER_ID_PREFIX": "featuresBuild"},
    ...     module_target="app.workers.features_build",
    ...     process_count=2,
    ... )]
    ['featuresBuild0001', 'featuresBuild0002']
    """
    if process_count < 1:
        raise RuntimeConfigError("worker process count must be at least 1")
    return [
        WorkerChildSpec(
            slot=index,
            env=child_environment(base_env, module_target, index),
        )
        for index in range(1, process_count + 1)
    ]


def run_worker_supervisor(
    *,
    config: RuntimeConfig,
    module_target: str,
    process_count: int,
    execution_mode: ExecutionMode = "process",
) -> None:
    if process_count < 1:
        raise RuntimeConfigError("worker process count must be at least 1")
    if execution_mode not in ("process", "thread"):
        raise RuntimeConfigError("execution mode must be either 'process' or 'thread'")

    workspace_lock = acquire_supervisor_workspace_lock(config.workspace_root)
    try:
        removed = sweep_abandoned_attempt_workspaces(config.workspace_root)
        if removed:
            logger.bind(removed_count=len(removed)).info("swept abandoned attempt workspaces at startup")
        _run_worker_supervisor_locked(
            config=config,
            module_target=module_target,
            process_count=process_count,
            execution_mode=execution_mode,
        )
    finally:
        workspace_lock.release()


def _run_worker_supervisor_locked(
    *,
    config: RuntimeConfig,
    module_target: str,
    process_count: int,
    execution_mode: ExecutionMode,
) -> None:
    if execution_mode == "thread":
        gc_loop = start_workspace_gc_loop(
            config=config,
            active_process_owners=lambda: set(),
        )
        try:
            _thread_runner_main(config=config, module_target=module_target, thread_count=process_count)
        finally:
            gc_loop.stop()
        return
    specs = worker_child_specs(
        base_env={"PERAGO_WORKER_ID_PREFIX": config.worker_id_prefix},
        module_target=module_target,
        process_count=process_count,
    )
    broker_slots: list[ProcessExecutorSlot] = []
    executor_connections: dict[int, Any] = {}
    executor_generations: dict[int, int] = {}
    for spec in specs:
        broker_connection, executor_connection = multiprocessing.Pipe()
        generation = 1
        broker_slots.append(
            ProcessExecutorSlot(worker_id=spec.worker_id, connection=broker_connection, generation=generation)
        )
        executor_connections[spec.slot] = executor_connection
        executor_generations[spec.slot] = generation
    broker_slots_by_slot = {spec.slot: slot for spec, slot in zip(specs, broker_slots, strict=True)}
    executor_event_queue: multiprocessing.Queue = multiprocessing.Queue()
    attempt_fence_request_queue: multiprocessing.Queue = multiprocessing.Queue()
    attempt_fence_response_queues: dict[str, multiprocessing.Queue] = {
        spec.worker_id: multiprocessing.Queue() for spec in specs
    }
    stop = multiprocessing.Event()
    broker = _start_broker_process(
        config=config,
        module_target=module_target,
        process_count=process_count,
        slots=broker_slots,
        executor_event_queue=executor_event_queue,
        attempt_fence_request_queue=attempt_fence_request_queue,
        attempt_fence_response_queues=attempt_fence_response_queues,
    )
    executors: dict[int, tuple[WorkerChildSpec, multiprocessing.Process, int]] = {}
    gc_loop = start_workspace_gc_loop(
        config=config,
        active_process_owners=lambda: _active_process_workspace_owners(executors),
    )

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop.set()

    previous_int = signal.signal(signal.SIGINT, request_stop)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        for spec in specs:
            process = _start_process_executor(
                config=config,
                module_target=module_target,
                spec=spec,
                connection=executor_connections[spec.slot],
                attempt_fence_request_queue=attempt_fence_request_queue,
                attempt_fence_response_queue=attempt_fence_response_queues[spec.worker_id],
            )
            executors[spec.slot] = (spec, process, 0)

        while not stop.is_set():
            if not broker.is_alive():
                logger.bind(exit_code=broker.exitcode).error("broker process exited; stopping process runtime")
                stop.set()
                break

            for slot, (spec, process, restart_count) in list(executors.items()):
                if process.is_alive():
                    continue
                exit_code = process.exitcode
                generation = executor_generations[slot]
                executor_event_queue.put(
                    ProcessExecutorExited(worker_id=spec.worker_id, generation=generation, exit_code=exit_code)
                )
                _close_connection(executor_connections[slot])
                delay = restart_backoff_seconds(restart_count)
                logger.bind(
                    worker_id=spec.worker_id,
                    pid=getattr(process, "pid", None),
                    slot=slot,
                    exit_code=exit_code,
                    restart_delay_seconds=delay,
                ).error("worker process exited; restarting")
                _targeted_workspace_gc(config=config, worker_id=spec.worker_id, process=process)
                if stop.wait(delay):
                    break
                next_generation = generation + 1
                broker_connection, executor_connection = multiprocessing.Pipe()
                broker_slots_by_slot[slot].connection = broker_connection
                broker_slots_by_slot[slot].generation = next_generation
                broker_slots_by_slot[slot].exited_generation = None
                executor_connections[slot] = executor_connection
                executor_generations[slot] = next_generation
                replacement = _start_process_executor(
                    config=config,
                    module_target=module_target,
                    spec=spec,
                    connection=executor_connection,
                    attempt_fence_request_queue=attempt_fence_request_queue,
                    attempt_fence_response_queue=attempt_fence_response_queues[spec.worker_id],
                )
                executor_event_queue.put(
                    ProcessExecutorStarted(
                        worker_id=spec.worker_id,
                        generation=next_generation,
                        connection=broker_connection,
                    )
                )
                executors[slot] = (spec, replacement, restart_count + 1)
            stop.wait(SUPERVISOR_PROCESS_POLL_INTERVAL_SECONDS)
    finally:
        stop.set()
        gc_loop.stop()
        for slot in broker_slots:
            try:
                slot.connection.send(StopProcessExecutor())
            except (BrokenPipeError, EOFError, OSError):
                # Best-effort shutdown: child or broker endpoints may already be closed.
                continue
        if broker.is_alive():
            broker.terminate()
        _stop_worker_processes(
            [broker, *[process for _, process, _ in executors.values()]],
            force_kill_after=config.shutdown_force_kill_after,
            workspace_root=config.workspace_root,
            process_worker_ids={id(process): spec.worker_id for spec, process, _ in executors.values()},
        )
        final_removed = sweep_abandoned_attempt_workspaces(config.workspace_root)
        if final_removed:
            logger.bind(removed_count=len(final_removed)).info("swept abandoned attempt workspaces after shutdown")
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


@dataclass(frozen=True)
class SupervisorWorkspaceLock:
    path: Path
    supervisor_pid: int

    def release(self) -> None:
        try:
            data = _read_supervisor_workspace_lock(self.path)
        except (OSError, RuntimeConfigError):
            return
        if data.get("supervisor_pid") == self.supervisor_pid:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def acquire_supervisor_workspace_lock(workspace_root: os.PathLike[str]) -> SupervisorWorkspaceLock:
    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / SUPERVISOR_WORKSPACE_LOCK_FILE
    supervisor_pid = os.getpid()
    payload = json.dumps({"supervisor_pid": supervisor_pid}, sort_keys=True) + "\n"

    while True:
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            try:
                lock_stat = lock_path.stat()
            except FileNotFoundError:
                continue
            try:
                data = _read_supervisor_workspace_lock(lock_path)
            except FileNotFoundError:
                continue
            owner_pid = data.get("supervisor_pid")
            if isinstance(owner_pid, int) and not _pid_is_alive(owner_pid):
                _unlink_supervisor_workspace_lock_if_same(lock_path, lock_stat)
                continue
            raise RuntimeConfigError(
                f"workspace root {root} is already locked by supervisor pid {owner_pid}; "
                f"use a different PERAGO_WORKSPACE_ROOT for each supervisor"
            )
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(payload)
        return SupervisorWorkspaceLock(path=lock_path, supervisor_pid=supervisor_pid)


def _read_supervisor_workspace_lock(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeConfigError(
            f"workspace root lock {path} is not valid JSON; remove it only if no supervisor is using this root"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeConfigError(f"workspace root lock {path} must contain an object")
    return data


def _unlink_supervisor_workspace_lock_if_same(path: Path, lock_stat: os.stat_result) -> None:
    try:
        current_stat = path.stat()
    except FileNotFoundError:
        return
    if (current_stat.st_dev, current_stat.st_ino) != (lock_stat.st_dev, lock_stat.st_ino):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_worker_processes(
    processes: list[multiprocessing.Process],
    *,
    force_kill_after: timedelta | None = None,
    workspace_root: os.PathLike[str] | None = None,
    process_worker_ids: dict[int, str] | None = None,
) -> None:
    timeout = None if force_kill_after is None else _duration_seconds(force_kill_after)
    for process in processes:
        process.join(timeout=timeout)
    if force_kill_after is None:
        return

    deadline = force_kill_after.total_seconds()
    for process in processes:
        if process.is_alive():
            _log_force_kill(
                process=process,
                deadline_seconds=deadline,
                workspace_root=workspace_root,
                worker_id=(process_worker_ids or {}).get(id(process)),
            )
            process.kill()
    for process in processes:
        process.join(timeout=PROCESS_JOIN_TIMEOUT_SECONDS)


def _targeted_workspace_gc(*, config: RuntimeConfig, worker_id: str, process: multiprocessing.Process) -> None:
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int):
        return
    removed = garbage_collect_workspace_owner(
        config.workspace_root,
        owner_worker_id=worker_id,
        owner_pid=pid,
    )
    if removed:
        logger.bind(worker_id=worker_id, pid=pid, removed_count=len(removed)).info(
            "garbage-collected workspaces for dead executor"
        )


def _log_force_kill(
    *,
    process: multiprocessing.Process,
    deadline_seconds: float,
    workspace_root: os.PathLike[str] | None,
    worker_id: str | None,
) -> None:
    logger.bind(
        worker_id=worker_id,
        pid=getattr(process, "pid", None),
        task_id=None,
        execution_id=None,
        phase="shutdown-force-kill",
        deadline_seconds=deadline_seconds,
        workspace_root=str(workspace_root) if workspace_root is not None else None,
    ).error("force-killing worker process after shutdown drain deadline")


def _close_connection(connection: Any) -> None:
    try:
        connection.close()
    except (AttributeError, OSError):
        return


class WorkspaceGCLoop:
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        active_process_owners: Callable[[], set[tuple[str, int]]],
    ) -> None:
        self._config = config
        self._active_process_owners = active_process_owners
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="perago-workspace-gc", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=WORKSPACE_GC_THREAD_JOIN_TIMEOUT_SECONDS)

    def run_once(self) -> list[object]:
        return garbage_collect_attempt_workspaces(
            self._config.workspace_root,
            ttl=self._config.workspace_gc_ttl,
            active_process_owners=self._active_process_owners(),
        )

    def _run(self) -> None:
        interval_seconds = _duration_seconds(self._config.workspace_gc_interval)
        while not self._stop.is_set():
            try:
                removed = self.run_once()
                if removed:
                    logger.bind(removed_count=len(removed)).info("garbage-collected abandoned attempt workspaces")
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).error("workspace garbage collection failed")
            self._stop.wait(interval_seconds)


def start_workspace_gc_loop(
    *,
    config: RuntimeConfig,
    active_process_owners: Callable[[], set[tuple[str, int]]],
) -> WorkspaceGCLoop:
    loop = WorkspaceGCLoop(config=config, active_process_owners=active_process_owners)
    loop.start()
    return loop


def _active_process_workspace_owners(
    executors: dict[int, tuple[WorkerChildSpec, multiprocessing.Process, int]],
) -> set[tuple[str, int]]:
    active: set[tuple[str, int]] = set()
    for spec, process, _ in executors.values():
        pid = getattr(process, "pid", None)
        if isinstance(pid, int) and process.is_alive():
            active.add((spec.worker_id, pid))
    return active


def _duration_seconds(value: timedelta) -> float:
    return max(value.total_seconds(), MIN_DURATION_SECONDS)


def _start_broker_process(
    *,
    config: RuntimeConfig,
    module_target: str,
    process_count: int,
    slots: list[ProcessExecutorSlot],
    executor_event_queue: multiprocessing.Queue,
    attempt_fence_request_queue: multiprocessing.Queue,
    attempt_fence_response_queues: dict[str, multiprocessing.Queue],
) -> multiprocessing.Process:
    process = multiprocessing.Process(
        target=_broker_process_main,
        kwargs={
            "config": config,
            "module_target": module_target,
            "process_count": process_count,
            "slots": slots,
            "executor_event_queue": executor_event_queue,
            "attempt_fence_request_queue": attempt_fence_request_queue,
            "attempt_fence_response_queues": attempt_fence_response_queues,
        },
        name="perago-conductor-broker",
    )
    process.start()
    return process


def _start_process_executor(
    *,
    config: RuntimeConfig,
    module_target: str,
    spec: WorkerChildSpec,
    connection: Any,
    attempt_fence_request_queue: multiprocessing.Queue,
    attempt_fence_response_queue: multiprocessing.Queue,
) -> multiprocessing.Process:
    process = multiprocessing.Process(
        target=_process_executor_main,
        kwargs={
            "config": config,
            "module_target": module_target,
            "child_env": spec.env,
            "connection": connection,
            "attempt_fence_request_queue": attempt_fence_request_queue,
            "attempt_fence_response_queue": attempt_fence_response_queue,
        },
        name=f"perago-executor-{spec.slot:04d}",
    )
    process.start()
    return process


def _broker_process_main(
    *,
    config: RuntimeConfig,
    module_target: str,
    process_count: int,
    slots: list[ProcessExecutorSlot],
    executor_event_queue: multiprocessing.Queue,
    attempt_fence_request_queue: multiprocessing.Queue,
    attempt_fence_response_queues: dict[str, multiprocessing.Queue],
) -> None:
    os.environ.update(_broker_environment(config.worker_id_prefix))
    task = load_module_task(module_target)
    runtime = prepare_worker_runtime(config=config, module_target=module_target, env=os.environ.copy())
    conductor_config = config.conductor
    if conductor_config is None:
        raise RuntimeConfigError("CONDUCTOR_SERVER_URL is required for perago start")
    conductor = OrkesConductorRuntimeClient.from_config(conductor_config)

    logger.bind(worker_id=runtime.worker_id, module_target=module_target, log_file=str(runtime.log_file)).info(
        "process broker started"
    )
    run_conductor_process_broker(
        task=task,
        worker_id=runtime.worker_id,
        process_count=process_count,
        conductor_config=conductor_config,
        slots=slots,
        executor_event_queue=executor_event_queue,
        attempt_fence_request_queue=attempt_fence_request_queue,
        attempt_fence_response_queues=attempt_fence_response_queues,
        client=conductor,
        failure_reason_max_length=config.failure_reason_max_length,
    )


def _process_executor_main(
    *,
    config: RuntimeConfig,
    module_target: str,
    child_env: dict[str, str],
    connection: Any,
    attempt_fence_request_queue: multiprocessing.Queue,
    attempt_fence_response_queue: multiprocessing.Queue,
) -> None:
    os.environ.update(child_env)
    task = load_module_task(module_target)
    runtime = prepare_worker_runtime(config=config, module_target=module_target, env=os.environ.copy())
    lakefs = _lakefs_runtime_for_task(task, config)

    logger.bind(worker_id=runtime.worker_id, module_target=module_target, log_file=str(runtime.log_file)).info(
        "process executor started"
    )
    run_process_executor_loop(
        task=task,
        worker_id=runtime.worker_id,
        workspace_root=config.workspace_root,
        connection=connection,
        load_current_attempt=lambda current_attempt: load_current_attempt_via_broker(
            current_attempt,
            worker_id=runtime.worker_id,
            request_queue=attempt_fence_request_queue,
            response_queue=attempt_fence_response_queue,
        ),
        failure_reason_max_length=config.failure_reason_max_length,
        workspace_runtime=lakefs,
    )


def _thread_runner_main(
    *,
    config: RuntimeConfig,
    module_target: str,
    thread_count: int,
) -> None:
    os.environ.update(_broker_environment(config.worker_id_prefix))
    task = load_module_task(module_target)
    runtime = prepare_worker_runtime(config=config, module_target=module_target, env=os.environ.copy())
    conductor_config = config.conductor
    if conductor_config is None:
        raise RuntimeConfigError("CONDUCTOR_SERVER_URL is required for perago start")

    lakefs = _lakefs_runtime_for_task(task, config)
    conductor = OrkesConductorRuntimeClient.from_config(conductor_config)

    logger.bind(worker_id=runtime.worker_id, module_target=module_target, log_file=str(runtime.log_file)).info(
        "thread runner started"
    )
    run_conductor_thread_runner(
        task=task,
        worker_id=runtime.worker_id,
        thread_count=thread_count,
        conductor_config=conductor_config,
        client=conductor,
        workspace_root=config.workspace_root,
        failure_reason_max_length=config.failure_reason_max_length,
        workspace_runtime=lakefs,
    )


def _broker_environment(worker_id_prefix: str) -> dict[str, str]:
    return {
        "PERAGO_WORKER_ID_PREFIX": worker_id_prefix,
        "PERAGO_WORKER_ID": f"{worker_id_prefix}Broker",
    }


def _lakefs_runtime_for_task(task: object, config: RuntimeConfig) -> LakeFSWorkspaceRuntime | None:
    if not task.has_workspace:
        return None

    lakefs_config = config.lakefs
    if lakefs_config is None:
        raise RuntimeConfigError("LakeFS config is required for workspace tasks")

    return LakeFSWorkspaceRuntime.from_config(lakefs_config, publish_budget=_effective_publish_budget(task))


def _effective_publish_budget(task: object) -> object:
    workspace = getattr(task, "workspace", None)
    if workspace is not None and getattr(workspace, "read_only", False):
        return None
    return task.controls.publish_budget
