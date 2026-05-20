from __future__ import annotations

import json
from typing import Any

from perago.models import WorkspaceInput, WorkspaceSpec


def logical_task_key(task: object) -> str:
    parts = [
        _task_attr(task, "workflow_instance_id"),
        _task_attr(task, "reference_task_name"),
        str(_task_attr(task, "seq")),
        str(_task_attr(task, "iteration")),
        _task_attr(task, "task_def_name"),
    ]
    return ":".join(parts)


def metadata_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def perago_metadata(
    *,
    task: object,
    workspace: WorkspaceInput | dict[str, Any],
    workspace_spec: WorkspaceSpec,
    logical_task_key: str,
    phase: str,
    extra: dict[str, object] | None = None,
) -> dict[str, str]:
    workspace_input = WorkspaceInput.model_validate(workspace)
    data: dict[str, object] = {
        "perago.phase": phase,
        "perago.logical_task_key": logical_task_key,
        "perago.workflow_instance_id": _task_attr(task, "workflow_instance_id"),
        "perago.task_def_name": _task_attr(task, "task_def_name"),
        "perago.reference_task_name": _task_attr(task, "reference_task_name"),
        "perago.seq": _task_attr(task, "seq"),
        "perago.iteration": _task_attr(task, "iteration"),
        "perago.input_ref": workspace_input.ref,
        "perago.target_branch": workspace_input.branch,
        "perago.prefix": workspace_spec.prefix,
        "perago.task_id": _task_attr(task, "task_id"),
        "perago.retry_count": _task_attr(task, "retry_count"),
        "perago.retried_task_id": getattr(task, "retried_task_id", None),
    }
    if extra:
        data.update(extra)
    return {key: metadata_value(value) for key, value in data.items()}


def _task_attr(task: object, name: str) -> object:
    try:
        return getattr(task, name)
    except AttributeError as exc:
        raise AttributeError(f"task is missing required attribute {name}") from exc
