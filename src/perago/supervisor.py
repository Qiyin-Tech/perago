from __future__ import annotations

import multiprocessing
import os
import signal
from dataclasses import dataclass
from types import FrameType

from loguru import logger

from perago.conductor_runtime import OrkesConductorRuntimeClient, run_worker_poll_loop
from perago.config import RuntimeConfig, child_environment
from perago.errors import RuntimeConfigError
from perago.lakefs_runtime import BoundLakeFSWorkspaceRuntime, LakeFSWorkspaceRuntime
from perago.task import load_module_task
from perago.worker_runtime import prepare_worker_runtime


RESTART_BACKOFF_SECONDS = (1, 2, 4, 8, 16)
MAX_RESTART_BACKOFF_SECONDS = 30


@dataclass(frozen=True)
class WorkerChildSpec:
    slot: int
    env: dict[str, str]

    @property
    def worker_id(self) -> str:
        return self.env["PERAGO_WORKER_ID"]


def restart_backoff_seconds(restart_count: int) -> int:
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
) -> None:
    specs = worker_child_specs(
        base_env={"PERAGO_WORKER_ID_PREFIX": config.worker_id_prefix},
        module_target=module_target,
        process_count=process_count,
    )
    stop = multiprocessing.Event()
    children: dict[int, tuple[WorkerChildSpec, multiprocessing.Process, int]] = {}

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop.set()

    previous_int = signal.signal(signal.SIGINT, request_stop)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        for spec in specs:
            process = _start_worker_process(config=config, module_target=module_target, spec=spec, stop=stop)
            children[spec.slot] = (spec, process, 0)

        while not stop.is_set():
            for slot, (spec, process, restart_count) in list(children.items()):
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
                replacement = _start_worker_process(config=config, module_target=module_target, spec=spec, stop=stop)
                children[slot] = (spec, replacement, restart_count + 1)
            stop.wait(0.5)
    finally:
        stop.set()
        _stop_worker_processes([process for _, process, _ in children.values()])
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


def _start_worker_process(
    *,
    config: RuntimeConfig,
    module_target: str,
    spec: WorkerChildSpec,
    stop: multiprocessing.synchronize.Event,
) -> multiprocessing.Process:
    process = multiprocessing.Process(
        target=_worker_process_main,
        kwargs={
            "config": config,
            "module_target": module_target,
            "child_env": spec.env,
            "stop": stop,
        },
        name=f"perago-worker-{spec.worker_id}",
    )
    process.start()
    return process


def _worker_process_main(
    *,
    config: RuntimeConfig,
    module_target: str,
    child_env: dict[str, str],
    stop: multiprocessing.synchronize.Event,
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
    conductor = OrkesConductorRuntimeClient.from_config(
        conductor_config,
        task_update_timeout_seconds=(
            None if publish_budget is None else publish_budget.conductor_completion_timeout_seconds
        ),
    )
    lakefs = BoundLakeFSWorkspaceRuntime(
        LakeFSWorkspaceRuntime.from_config(lakefs_config, publish_budget=publish_budget)
    )

    logger.bind(worker_id=runtime.worker_id, module_target=module_target, log_file=str(runtime.log_file)).info(
        "worker started"
    )
    run_worker_poll_loop(
        task=task,
        client=conductor,
        worker_id=runtime.worker_id,
        workspace_root=config.workspace_root,
        should_stop=stop.is_set,
        download_workspace=lakefs.download_workspace,
        stage_workspace=lakefs.stage_workspace,
        publish_workspace=lakefs.publish_workspace,
        cleanup_staging=lakefs.cleanup_staging,
    )
