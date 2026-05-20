from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from perago.errors import (
    GuardrailViolation,
    PostGuardrailViolation,
    PreGuardrailViolation,
    TaskInputError,
)
from perago.guards import check_guardrails
from perago.models import WorkspaceInput
from perago.task import TaskDefinition


def invoke_workspace_task_body(
    task: TaskDefinition,
    input_data: Mapping[str, Any],
    workspace_dir: Path,
) -> dict[str, Any]:
    if not task.has_workspace:
        raise TaskInputError("invoke_workspace_task_body only supports workspace tasks")
    if set(input_data) != {"workspace", "params"}:
        raise TaskInputError("workspace task input must contain only workspace and params")

    WorkspaceInput.model_validate(input_data["workspace"])
    params = task.params_model.model_validate(input_data["params"])
    workspace = task.workspace
    if workspace is None:
        raise TaskInputError("workspace task definition is missing WorkspaceSpec")

    _check_phase_guardrails(workspace_dir, workspace.pre, "pre", PreGuardrailViolation)
    raw_result = task.fn(workspace_dir, params)
    result = _validate_result(task, raw_result)
    _check_phase_guardrails(workspace_dir, workspace.post, "post", PostGuardrailViolation)
    return {"result": result.model_dump(mode="json")}


def invoke_workspace_free_task(task: TaskDefinition, input_data: Mapping[str, Any]) -> dict[str, Any]:
    if task.has_workspace:
        raise TaskInputError("invoke_workspace_free_task only supports workspace-free tasks")
    if set(input_data) != {"params"}:
        raise TaskInputError("workspace-free task input must contain only params")

    params = task.params_model.model_validate(input_data["params"])
    raw_result = task.fn(params)
    result = _validate_result(task, raw_result)
    return {"result": result.model_dump(mode="json")}


def _check_phase_guardrails(
    workspace_dir: Path,
    guardrails: list[Any],
    phase: str,
    error: type[GuardrailViolation],
) -> None:
    try:
        check_guardrails(workspace_dir, guardrails, phase)
    except GuardrailViolation as exc:
        raise error(str(exc)) from exc


def _validate_result(task: TaskDefinition, raw_result: object) -> BaseModel:
    if isinstance(raw_result, BaseModel):
        return task.output_model.model_validate(raw_result.model_dump())
    return task.output_model.model_validate(raw_result)
