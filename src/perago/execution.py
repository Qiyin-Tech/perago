from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from perago.errors import TaskInputError
from perago.task import TaskDefinition


def invoke_workspace_free_task(task: TaskDefinition, input_data: Mapping[str, Any]) -> dict[str, Any]:
    if task.has_workspace:
        raise TaskInputError("invoke_workspace_free_task only supports workspace-free tasks")
    if set(input_data) != {"params"}:
        raise TaskInputError("workspace-free task input must contain only params")

    params = task.params_model.model_validate(input_data["params"])
    raw_result = task.fn(params)
    result = _validate_result(task, raw_result)
    return {"result": result.model_dump(mode="json")}


def _validate_result(task: TaskDefinition, raw_result: object) -> BaseModel:
    if isinstance(raw_result, BaseModel):
        return task.output_model.model_validate(raw_result.model_dump())
    return task.output_model.model_validate(raw_result)
