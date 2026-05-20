from __future__ import annotations

import multiprocessing
import os
import signal
from dataclasses import dataclass
from types import FrameType

from loguru import logger

from perago.conductor_runtime import (
    OrkesConductorRuntimeClient,
    StopProcessExecutor,
    run_conductor_process_broker,
    run_conductor_thread_runner,
    run_process_executor_loop,
)
from perago.config import ExecutionMode, RuntimeConfig, child_environment
from perago.errors import RuntimeConfigError
from perago.lakefs_runtime import BoundLakeFSWorkspaceRuntime, LakeFSWorkspaceRuntime
from perago.task import load_module_task
from perago.worker_runtime import prepare_worker_runtime


RESTART_BACKOFF_SECONDS = (1, 2, 4, 8, 16)
MAX_RESTART_BACKOFF_SECONDS = 30


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
    if execution_mode == "thread":
        _thread_runner_main(config=config, module_target=module_target, thread_count=process_count)
        return
    if execution_mode != "process":
        raise RuntimeConfigError("execution mode must be either 'process' or 'thread'")
    specs = worker_child_specs(
        base_env={"PERAGO_WORKER_ID_PREFIX": config.worker_id_prefix},
        module_target=module_target,
        process_count=process_count,
    )
    assignment_queue: multiprocessing.Queue = multiprocessing.Queue()
    completion_queue: multiprocessing.Queue = multiprocessing.Queue()
    stop = multiprocessing.Event()
    broker = _start_broker_process(
        config=config,
        module_target=module_target,
        process_count=process_count,
        assignment_queue=assignment_queue,
        completion_queue=completion_queue,
    )
    executors: dict[int, tuple[WorkerChildSpec, multiprocessing.Process, int]] = {}

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
                assignment_queue=assignment_queue,
                completion_queue=completion_queue,
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
                delay = restart_backoff_seconds(restart_count)
                logger.bind(
                    worker_id=spec.worker_id,
                    slot=slot,
                    exit_code=exit_code,
                    restart_delay_seconds=delay,
                ).error("worker process exited; restarting")
                if stop.wait(delay):
                    break
                replacement = _start_process_executor(
                    config=config,
                    module_target=module_target,
                    spec=spec,
                    assignment_queue=assignment_queue,
                    completion_queue=completion_queue,
                )
                executors[slot] = (spec, replacement, restart_count + 1)
            stop.wait(0.5)
    finally:
        stop.set()
        for _ in executors:
            assignment_queue.put(StopProcessExecutor())
        _stop_worker_processes([broker, *[process for _, process, _ in executors.values()]])
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def _stop_worker_processes(processes: list[multiprocessing.Process]) -> None:
    for process in processes:
        process.join(timeout=10)
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=5)
    for process in processes:
        if process.is_alive():
            process.kill()
    for process in processes:
        process.join(timeout=5)


def _start_broker_process(
    *,
    config: RuntimeConfig,
    module_target: str,
    process_count: int,
    assignment_queue: multiprocessing.Queue,
    completion_queue: multiprocessing.Queue,
) -> multiprocessing.Process:
    process = multiprocessing.Process(
        target=_broker_process_main,
        kwargs={
            "config": config,
            "module_target": module_target,
            "process_count": process_count,
            "assignment_queue": assignment_queue,
            "completion_queue": completion_queue,
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
    assignment_queue: multiprocessing.Queue,
    completion_queue: multiprocessing.Queue,
) -> multiprocessing.Process:
    process = multiprocessing.Process(
        target=_process_executor_main,
        kwargs={
            "config": config,
            "module_target": module_target,
            "child_env": spec.env,
            "assignment_queue": assignment_queue,
            "completion_queue": completion_queue,
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
    assignment_queue: multiprocessing.Queue,
    completion_queue: multiprocessing.Queue,
) -> None:
    os.environ.update(_broker_environment(config.worker_id_prefix))
    task = load_module_task(module_target)
    runtime = prepare_worker_runtime(config=config, module_target=module_target, env=os.environ.copy())
    conductor_config = config.conductor
    if conductor_config is None:
        raise RuntimeConfigError("CONDUCTOR_SERVER_URL is required for perago start")

    logger.bind(worker_id=runtime.worker_id, module_target=module_target, log_file=str(runtime.log_file)).info(
        "process broker started"
    )
    run_conductor_process_broker(
        task=task,
        worker_id=runtime.worker_id,
        process_count=process_count,
        conductor_config=conductor_config,
        assignment_queue=assignment_queue,
        completion_queue=completion_queue,
    )


def _process_executor_main(
    *,
    config: RuntimeConfig,
    module_target: str,
    child_env: dict[str, str],
    assignment_queue: multiprocessing.Queue,
    completion_queue: multiprocessing.Queue,
) -> None:
    os.environ.update(child_env)
    task = load_module_task(module_target)
    runtime = prepare_worker_runtime(config=config, module_target=module_target, env=os.environ.copy())
    conductor_config = config.conductor
    lakefs_config = config.lakefs
    if conductor_config is None:
        raise RuntimeConfigError("CONDUCTOR_SERVER_URL is required for perago start")
    if lakefs_config is None:
        raise RuntimeConfigError("LakeFS config is required for perago start")

    publish_budget = task.controls.publish_budget
    conductor = OrkesConductorRuntimeClient.from_config(conductor_config)
    lakefs = BoundLakeFSWorkspaceRuntime(
        LakeFSWorkspaceRuntime.from_config(lakefs_config, publish_budget=publish_budget)
    )

    logger.bind(worker_id=runtime.worker_id, module_target=module_target, log_file=str(runtime.log_file)).info(
        "process executor started"
    )
    run_process_executor_loop(
        task=task,
        worker_id=runtime.worker_id,
        workspace_root=config.workspace_root,
        assignment_queue=assignment_queue,
        completion_queue=completion_queue,
        load_current_attempt=lambda current_attempt: conductor.get_task(current_attempt.task_id),
        download_workspace=lakefs.download_workspace,
        stage_workspace=lakefs.stage_workspace,
        publish_workspace=lakefs.publish_workspace,
        cleanup_staging=lakefs.cleanup_staging,
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
    lakefs_config = config.lakefs
    if conductor_config is None:
        raise RuntimeConfigError("CONDUCTOR_SERVER_URL is required for perago start")
    if lakefs_config is None:
        raise RuntimeConfigError("LakeFS config is required for perago start")

    publish_budget = task.controls.publish_budget
    conductor = OrkesConductorRuntimeClient.from_config(conductor_config)
    lakefs = BoundLakeFSWorkspaceRuntime(
        LakeFSWorkspaceRuntime.from_config(lakefs_config, publish_budget=publish_budget)
    )

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
        download_workspace=lakefs.download_workspace,
        stage_workspace=lakefs.stage_workspace,
        publish_workspace=lakefs.publish_workspace,
        cleanup_staging=lakefs.cleanup_staging,
    )


def _broker_environment(worker_id_prefix: str) -> dict[str, str]:
    return {
        "PERAGO_WORKER_ID_PREFIX": worker_id_prefix,
        "PERAGO_WORKER_ID": f"{worker_id_prefix}Broker",
    }
