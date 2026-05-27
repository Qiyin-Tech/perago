from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from perago.execution import (
    CleanupStaging,
    CompleteNoOpWorkspace,
    DownloadWorkspace,
    PublishWorkspace,
    StageWorkspace,
)


@dataclass(frozen=True)
class ConductorTaskAttempt:
    workflow_instance_id: str
    task_id: str
    retry_count: int
    task_def_name: str
    reference_task_name: str
    seq: int
    iteration: int
    status: str
    input_data: Mapping[str, Any]
    retried_task_id: str | None = None
    response_timeout_seconds: int | None = None


class ConductorRuntimeClient(Protocol):
    def taskdef_exists(self, task_name: str) -> bool: ...

    def get_task(self, task_id: str) -> ConductorTaskAttempt: ...


class WorkspaceRuntime(Protocol):
    download_workspace: DownloadWorkspace
    stage_workspace: StageWorkspace
    publish_workspace: PublishWorkspace
    cleanup_staging: CleanupStaging
    complete_noop_workspace: CompleteNoOpWorkspace
