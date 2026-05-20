from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from perago.config import RuntimeConfig, resolve_worker_id
from perago.runtime_logging import configure_worker_logging
from perago.workspace import sweep_abandoned_attempt_workspaces


@dataclass(frozen=True)
class WorkerRuntime:
    worker_id: str
    log_file: Path
    swept_workspaces: list[Path]


def prepare_worker_runtime(
    *,
    config: RuntimeConfig,
    module_target: str,
    env: dict[str, str],
) -> WorkerRuntime:
    worker_id = resolve_worker_id(module_target, env)
    swept = sweep_abandoned_attempt_workspaces(config.workspace_root)
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
