from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from perago.attempt import assert_current_attempt_snapshot
from perago.errors import (
    GuardrailViolation,
    PostGuardrailViolation,
    PreGuardrailViolation,
    TaskInputError,
)
from perago.guards import check_guardrails
from perago.models import WorkspaceInput, WorkspaceSpec
from perago.result import RuntimeTaskResult, completed_result, result_for_exception
from perago.task import TaskDefinition
from perago.workspace import cleanup_attempt_workspace_safely, prepare_attempt_workspace


DownloadWorkspace = Callable[[WorkspaceInput, WorkspaceSpec, Path], None]
LoadCurrentAttempt = Callable[[object], object]
PublishWorkspace = Callable[[Path, WorkspaceInput, WorkspaceSpec], str]


def run_workspace_task_attempt(
    task: TaskDefinition,
    input_data: Mapping[str, Any],
    attempt: object,
    workspace_root: Path,
    *,
    download_workspace: DownloadWorkspace,
    load_current_attempt: LoadCurrentAttempt,
    publish_workspace: PublishWorkspace,
) -> RuntimeTaskResult:
    if not task.has_workspace:
        raise TaskInputError("run_workspace_task_attempt only supports workspace tasks")
    workspace = task.workspace
    if workspace is None:
        raise TaskInputError("workspace task definition is missing WorkspaceSpec")

    workspace_dir: Path | None = None
    try:
        if set(input_data) != {"workspace", "params"}:
            raise TaskInputError("workspace task input must contain only workspace and params")
        workspace_input = WorkspaceInput.model_validate(input_data["workspace"])
        workspace_dir = prepare_attempt_workspace(workspace_root, attempt)
        download_workspace(workspace_input, workspace, workspace_dir)
        body_output = invoke_workspace_task_body(task, input_data, workspace_dir)
        assert_current_attempt_snapshot(attempt, load_current_attempt(attempt))
        published_ref = publish_workspace(workspace_dir, workspace_input, workspace)
        output_workspace = WorkspaceInput.model_validate(
            {
                **workspace_input.model_dump(mode="json"),
                "ref_type": "commit",
                "ref": published_ref,
            }
        )
        return completed_result(
            {
                "workspace": output_workspace.model_dump(mode="json"),
                **body_output,
            }
        )
    except Exception as exc:
        return result_for_exception(exc)
    finally:
        if workspace_dir is not None:
            cleanup_attempt_workspace_safely(workspace_dir, attempt)


def run_workspace_free_task_attempt(
    task: TaskDefinition,
    input_data: Mapping[str, Any],
) -> RuntimeTaskResult:
    if task.has_workspace:
        raise TaskInputError("run_workspace_free_task_attempt only supports workspace-free tasks")

    try:
        return completed_result(invoke_workspace_free_task(task, input_data))
    except Exception as exc:
        return result_for_exception(exc)


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
    return build_workspace_free_task_output(task, raw_result)


def build_workspace_free_task_output(task: TaskDefinition, raw_result: object) -> dict[str, Any]:
    if task.has_workspace:
        raise TaskInputError("workspace-free output can only be built for workspace-free tasks")
    result = _validate_result(task, raw_result)
    return {"result": result.model_dump(mode="json")}


def build_workspace_task_output(
    task: TaskDefinition,
    input_workspace: WorkspaceInput | Mapping[str, Any],
    published_ref: str,
    raw_result: object,
) -> dict[str, Any]:
    if not task.has_workspace:
        raise TaskInputError("workspace output can only be built for workspace tasks")
    workspace_input = WorkspaceInput.model_validate(input_workspace)
    workspace_output = WorkspaceInput.model_validate(
        {
            **workspace_input.model_dump(mode="json"),
            "ref_type": "commit",
            "ref": published_ref,
        }
    )
    result = _validate_result(task, raw_result)
    return {
        "workspace": workspace_output.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
    }


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
