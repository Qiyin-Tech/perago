from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from perago.errors import TaskDefinitionError
from perago.models import TaskControls, WorkspaceSpec


_REGISTERED_TASKS: dict[str, list["TaskDefinition"]] = {}


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    owner_email: str
    fn: Callable[..., BaseModel]
    params_model: type[BaseModel]
    output_model: type[BaseModel]
    description: str | None = None
    workspace: WorkspaceSpec | None = None
    controls: TaskControls = field(default_factory=TaskControls)

    @property
    def has_workspace(self) -> bool:
        return self.workspace is not None


def task(
    *,
    name: str,
    owner_email: str,
    description: str | None = None,
    workspace: WorkspaceSpec | None = None,
    controls: TaskControls | None = None,
    **unsupported: object,
) -> Callable[[Callable[..., BaseModel]], Callable[..., BaseModel]]:
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise TaskDefinitionError(f"unsupported task decorator fields: {names}")

    def decorate(fn: Callable[..., BaseModel]) -> Callable[..., BaseModel]:
        try:
            definition = _build_task_definition(
                fn=fn,
                name=name,
                owner_email=owner_email,
                description=description,
                workspace=workspace,
                controls=controls or TaskControls(),
            )
        except ValidationError as exc:
            raise TaskDefinitionError(str(exc)) from exc

        _REGISTERED_TASKS.setdefault(fn.__module__, []).append(definition)
        setattr(fn, "__perago_task__", definition)
        return fn

    return decorate


def load_module_task(module_target: str) -> TaskDefinition:
    _validate_module_target(module_target)
    module = importlib.import_module(module_target)
    tasks = [
        task_def
        for task_def in _REGISTERED_TASKS.get(module.__name__, [])
        if task_def.fn.__module__ == module.__name__
    ]
    unique_tasks = []
    seen_ids: set[int] = set()
    for task_def in tasks:
        task_id = id(task_def)
        if task_id not in seen_ids:
            unique_tasks.append(task_def)
            seen_ids.add(task_id)
    if not unique_tasks:
        raise TaskDefinitionError(f"{module_target} does not declare a Perago task")
    if len(unique_tasks) > 1:
        raise TaskDefinitionError(f"{module_target} declares more than one Perago task")
    return unique_tasks[0]


def _build_task_definition(
    *,
    fn: Callable[..., BaseModel],
    name: str,
    owner_email: str,
    description: str | None,
    workspace: WorkspaceSpec | None,
    controls: TaskControls,
) -> TaskDefinition:
    signature = inspect.signature(fn)
    parameters = list(signature.parameters.values())
    if any(parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parameters):
        raise TaskDefinitionError("task function must not use *args, **kwargs, or keyword-only fields")

    try:
        hints = get_type_hints(fn)
    except Exception as exc:  # noqa: BLE001
        raise TaskDefinitionError(f"failed to resolve task type hints: {exc}") from exc

    if len(parameters) == 2:
        _validate_workspace_signature(parameters, hints, workspace)
    elif len(parameters) == 1:
        _validate_workspace_free_signature(parameters, workspace)
    else:
        raise TaskDefinitionError(
            "task function must be exactly (workspace: Path, params: ParamsModel) or (params: ParamsModel)"
        )

    params_model = hints.get("params")
    output_model = hints.get("return")
    if not _is_pydantic_model(params_model):
        raise TaskDefinitionError("params must be annotated as a Pydantic BaseModel subclass")
    if not _is_pydantic_model(output_model):
        raise TaskDefinitionError("return value must be annotated as a Pydantic BaseModel subclass")

    return TaskDefinition(
        name=name,
        owner_email=owner_email,
        description=description,
        workspace=workspace,
        controls=controls,
        fn=fn,
        params_model=params_model,
        output_model=output_model,
    )


def _validate_workspace_signature(
    parameters: list[inspect.Parameter],
    hints: dict[str, Any],
    workspace: WorkspaceSpec | None,
) -> None:
    if parameters[0].name != "workspace" or parameters[1].name != "params":
        raise TaskDefinitionError("workspace task parameters must be named workspace and params")
    if hints.get("workspace") is not Path:
        raise TaskDefinitionError("workspace must be annotated as pathlib.Path")
    if workspace is None:
        raise TaskDefinitionError("workspace task functions require workspace=WorkspaceSpec(...)")


def _validate_workspace_free_signature(
    parameters: list[inspect.Parameter],
    workspace: WorkspaceSpec | None,
) -> None:
    if parameters[0].name != "params":
        raise TaskDefinitionError("workspace-free task parameter must be named params")
    if workspace is not None:
        raise TaskDefinitionError("workspace-free task functions must not declare workspace=WorkspaceSpec(...)")


def _is_pydantic_model(value: object) -> bool:
    return inspect.isclass(value) and issubclass(value, BaseModel)


def _validate_module_target(module_target: str) -> None:
    if "/" in module_target or "\\" in module_target or ":" in module_target or module_target.endswith(".py"):
        raise TaskDefinitionError("module target must be a Python import path, not a file path or object path")
