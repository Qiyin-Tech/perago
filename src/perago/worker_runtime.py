from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from perago.config import RuntimeConfig, resolve_worker_id
from perago.runtime_logging import configure_worker_logging


@dataclass(frozen=True)
class WorkerRuntime:
    """
    Prepared identity and logging state for one worker process.

    ``WorkerRuntime`` is returned after a child process has resolved its worker
    id and installed the per-worker JSONL log sink. It is runtime-local state
    and is not serialized into Conductor task input, task output, or generated
    TaskDefs.

    Parameters
    ----------
    worker_id : str
        Stable worker id for the current process. Supervisor-managed children
        receive this value from ``PERAGO_WORKER_ID``.
    log_file : pathlib.Path
        JSONL log file configured for this process.
    swept_workspaces : list of pathlib.Path
        Reserved for compatibility; worker startup no longer sweeps global
        workspace directories.

    Attributes
    ----------
    worker_id : str
        Stable worker id for the current process.
    log_file : pathlib.Path
        JSONL log file configured for this process.
    swept_workspaces : list of pathlib.Path
        Reserved for compatibility; worker startup no longer sweeps global
        workspace directories.

    See Also
    --------
    prepare_worker_runtime : Build this value for a worker child process.
    RuntimeConfig : Local roots and logging policy used to prepare the worker.

    Notes
    -----
    The dataclass is frozen. Sweeping only removes directories containing
    Perago's attempt workspace marker under ``RuntimeConfig.workspace_root``.

    Examples
    --------
    >>> WorkerRuntime(
    ...     worker_id="featuresBuild0001",
    ...     log_file=Path("/tmp/perago/logs/worker.jsonl"),
    ...     swept_workspaces=[],
    ... ).worker_id
    'featuresBuild0001'
    """

    worker_id: str
    log_file: Path
    swept_workspaces: list[Path]


def prepare_worker_runtime(
    *,
    config: RuntimeConfig,
    module_target: str,
    env: dict[str, str],
) -> WorkerRuntime:
    """
    Prepare local runtime state for one worker process.

    The worker process calls this before polling Conductor. Preparation
    resolves the worker id and configures Loguru to write serialized JSONL logs
    under the configured log root.

    Parameters
    ----------
    config : RuntimeConfig
        Worker-local runtime configuration containing workspace and log roots,
        log rotation settings, and the worker id prefix.
    module_target : str
        Python import path of the single task module served by this worker.
        It is used when a worker id must be derived from the module target.
    env : dict of str to str
        Environment mapping visible to the worker process. ``PERAGO_WORKER_ID``
        is used when present.

    Returns
    -------
    WorkerRuntime
        Prepared worker id, log file path, and swept attempt workspaces.

    See Also
    --------
    WorkerRuntime : Prepared runtime value returned by this function.
    load_runtime_config : Load the ``RuntimeConfig`` argument from local env.

    Notes
    -----
    This function mutates process logging configuration by replacing the
    current Loguru sinks with the worker JSONL file sink.

    Examples
    --------
    >>> from datetime import timedelta
    >>> config = RuntimeConfig(
    ...     workspace_root=Path("/tmp/perago/workspaces"),
    ...     log_root=Path("/tmp/perago/logs"),
    ...     log_file_max_size=1048576,
    ...     log_retention=timedelta(days=1),
    ...     worker_id_prefix="featuresBuild",
    ... )
    >>> runtime = prepare_worker_runtime(
    ...     config=config,
    ...     module_target="app.workers.features_build",
    ...     env={"PERAGO_WORKER_ID": "featuresBuild0001"},
    ... )
    >>> runtime.worker_id
    'featuresBuild0001'
    """
    worker_id = resolve_worker_id(module_target, env)
    swept: list[Path] = []
    log_file = configure_worker_logging(
        log_root=config.log_root,
        module_target=module_target,
        worker_id=worker_id,
        max_bytes=config.log_file_max_size,
        retention=config.log_retention,
    )
    return WorkerRuntime(
        worker_id=worker_id,
        log_file=log_file,
        swept_workspaces=swept,
    )
