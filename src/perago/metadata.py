from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from perago.errors import PublishFenceError
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


def choose_publish_base(
    *,
    workspace: WorkspaceInput | dict[str, Any],
    current_head: str,
    commits: Sequence[object],
    logical_task_key: str,
) -> tuple[str, str | None]:
    workspace_input = WorkspaceInput.model_validate(workspace)
    if current_head == workspace_input.ref:
        return current_head, None

    if commits and all(
        _commit_metadata(commit).get("perago.logical_task_key") == logical_task_key
        for commit in commits
    ):
        return current_head, _commit_id(commits[-1])

    raise PublishFenceError(
        f"{workspace_input.branch} advanced from {workspace_input.ref} to {current_head}"
    )


def _commit_id(commit: object) -> str:
    if isinstance(commit, Mapping):
        return str(commit["id"])
    return str(getattr(commit, "id"))


def _commit_metadata(commit: object) -> Mapping[str, str]:
    if isinstance(commit, Mapping):
        metadata = commit.get("metadata", {})
    else:
        metadata = getattr(commit, "metadata", {})
    if not isinstance(metadata, Mapping):
        return {}
    return metadata


def _task_attr(task: object, name: str) -> object:
    try:
        return getattr(task, name)
    except AttributeError as exc:
        raise AttributeError(f"task is missing required attribute {name}") from exc
