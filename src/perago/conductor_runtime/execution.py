from __future__ import annotations

from typing import Any

from perago.errors import RuntimeConfigError
from perago.execution import (
    LoadCurrentAttempt,
    run_workspace_free_task_attempt,
    run_workspace_task_attempt,
)
from perago.result import RuntimeTaskResult
from perago.task import TaskDefinition

from .models import ConductorTaskAttempt, WorkspaceRuntime


def execute_polled_task(
    *,
    task: TaskDefinition,
    attempt: ConductorTaskAttempt,
    workspace_root: Any,
    load_current_attempt: LoadCurrentAttempt,
    owner_worker_id: str | None = None,
    execution_id: str | None = None,
    failure_reason_max_length: int,
    workspace_runtime: WorkspaceRuntime | None = None,
) -> RuntimeTaskResult:
    if task.has_workspace:
        workspace_runtime = _require_workspace_runtime(workspace_runtime)
        return run_workspace_task_attempt(
            task,
            attempt.input_data,
            attempt,
            workspace_root,
            download_workspace=workspace_runtime.download_workspace,
            load_current_attempt=load_current_attempt,
            stage_workspace=workspace_runtime.stage_workspace,
            publish_workspace=workspace_runtime.publish_workspace,
            cleanup_staging=workspace_runtime.cleanup_staging,
            complete_noop_workspace=workspace_runtime.complete_noop_workspace,
            owner_worker_id=owner_worker_id,
            execution_id=execution_id,
            failure_reason_max_length=failure_reason_max_length,
        )
    return run_workspace_free_task_attempt(
        task,
        attempt.input_data,
        failure_reason_max_length=failure_reason_max_length,
    )


def _require_workspace_runtime(workspace_runtime: WorkspaceRuntime | None) -> WorkspaceRuntime:
    if workspace_runtime is None:
        raise RuntimeConfigError("workspace runtime is required for workspace tasks")
    return workspace_runtime
