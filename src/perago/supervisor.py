from __future__ import annotations

from dataclasses import dataclass

from perago.config import child_environment
from perago.errors import RuntimeConfigError


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
