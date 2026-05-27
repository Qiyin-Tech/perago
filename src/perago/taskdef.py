from __future__ import annotations

import json
import warnings
from collections.abc import Collection
from copy import deepcopy
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel, RootModel

from perago.errors import TaskDefinitionError
from perago.models import WorkspaceInput, WorkspaceOutput
from perago.task import TaskDefinition


TASKDEF_SCHEMA_VERSION = 1
TASKDEF_SCHEMA_TYPE = "JSON"

_MODEL_SCHEMA_STRUCTURAL_KEYS = frozenset({"$defs", "additionalProperties", "properties", "required", "type"})
_GENERATED_SCHEMA_METADATA_KEYS = frozenset({"title"})
_SCHEMA_NAME_MAPPING_KEYS = frozenset({"properties"})


CONTROL_FIELD_MAP = {
    "retryCount": ("retry", "count"),
    "retryLogic": ("retry", "logic"),
    "retryDelaySeconds": ("retry", "delay_seconds"),
    "maxRetryDelaySeconds": ("retry", "max_delay_seconds"),
    "backoffJitterMs": ("retry", "jitter_ms"),
    "totalTimeoutSeconds": ("timeout", "total_seconds"),
    "timeoutPolicy": ("timeout", "policy"),
    "timeoutSeconds": ("timeout", "seconds"),
    "responseTimeoutSeconds": ("response_timeout_seconds",),
    "pollTimeoutSeconds": ("timeout", "poll_seconds"),
    "concurrentExecLimit": ("limits", "concurrent_exec_limit"),
    "rateLimitFrequencyInSeconds": ("limits", "rate_limit_frequency_in_seconds"),
    "rateLimitPerFrequency": ("limits", "rate_limit_per_frequency"),
}


def build_taskdef(task: TaskDefinition) -> dict[str, Any]:
    """
    Build the Conductor TaskDef dictionary for one Perago task.

    ``build_taskdef`` is the library equivalent of ``perago extract``. It
    converts a validated :class:`perago.TaskDefinition` into the JSON-compatible
    mapping registered with Conductor. The task function signature determines
    input and output keys, Pydantic models provide JSON Schema, and
    :class:`perago.TaskControls` provide retry, timeout, response timeout, and
    execution limit fields.

    Parameters
    ----------
    task : TaskDefinition
        Validated task definition returned by :func:`perago.load_module_task`
        or attached to a decorated function as ``__perago_task__``.

    Returns
    -------
    dict of str to Any
        JSON-compatible Conductor TaskDef mapping. Workspace tasks contain
        ``workspace`` and ``params`` input keys and ``workspace`` and
        ``result`` output keys; workspace-free tasks contain only ``params``
        and ``result``.

    See Also
    --------
    write_taskdef : Write the generated TaskDef mapping to a JSON file.

    Notes
    -----
    Workspace guardrails, workspace prefixes, LakeFS connection settings, and
    publish budget internals are not serialized into the TaskDef. A publish
    budget does not replace ``timeout.response_seconds``; writable workspace
    tasks warn if the configured response timeout is shorter than the derived
    publish budget.

    Examples
    --------
    >>> task_def = build_taskdef(load_module_task("app.workers.features_build"))
    >>> task_def["name"]
    'features.build'
    """
    validate_no_root_task_models(task)

    input_properties: dict[str, Any] = {}
    output_properties: dict[str, Any] = {}
    input_required: list[str] = []
    output_required: list[str] = []

    if task.has_workspace:
        input_properties["workspace"] = schema_for_model(WorkspaceInput)
        output_properties["workspace"] = schema_for_model(WorkspaceOutput)
        input_required.append("workspace")
        output_required.append("workspace")

    input_properties["params"] = schema_for_model(task.params_model)
    output_properties["result"] = schema_for_model(task.output_model)
    input_required.append("params")
    output_required.append("result")

    data: dict[str, Any] = {
        "name": task.name,
        "ownerEmail": task.owner_email,
    }
    if task.description is not None:
        data["description"] = task.description
    data.update(
        {
            **_control_fields(task),
            "inputKeys": input_required,
            "outputKeys": output_required,
            "inputSchema": {
                "name": f"{task.name}.input",
                "version": TASKDEF_SCHEMA_VERSION,
                "type": TASKDEF_SCHEMA_TYPE,
                "data": _object_schema(input_properties, input_required),
            },
            "outputSchema": {
                "name": f"{task.name}.output",
                "version": TASKDEF_SCHEMA_VERSION,
                "type": TASKDEF_SCHEMA_TYPE,
                "data": _object_schema(output_properties, output_required),
            },
        }
    )
    return data


def write_taskdef(task: TaskDefinition, output: Path) -> Path:
    """
    Write a generated Conductor TaskDef to a JSON file.

    The parent directory is created when needed, and the file is written with
    stable indentation so the generated TaskDef can be reviewed before it is
    registered with Conductor.

    Parameters
    ----------
    task : TaskDefinition
        Validated task definition to serialize.
    output : pathlib.Path
        Destination JSON file path. The path must end with ``.json`` and must
        not point to an existing directory.

    Returns
    -------
    pathlib.Path
        The output path after the JSON file has been written.

    Raises
    ------
    ValueError
        If ``output`` does not end with ``.json`` or points to a directory.

    See Also
    --------
    build_taskdef : Build the TaskDef mapping without writing a file.

    Examples
    --------
    >>> task_def = load_module_task("app.workers.metadata_validate")
    >>> write_taskdef(task_def, Path("generated/metadata.validate.json"))
    PosixPath('generated/metadata.validate.json')
    """
    if output.suffix != ".json":
        raise ValueError("output must be a JSON file path, for example generated/features.build.json")
    if output.exists() and output.is_dir():
        raise ValueError("output must be a JSON file path, not a directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_taskdef(task), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return output


def schema_for_model(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    _strip_model_schema_metadata(schema)
    inlined = _inline_refs(schema)
    _strip_schema_metadata_keys(
        inlined,
        _GENERATED_SCHEMA_METADATA_KEYS,
        preserve_mapping_keys=_SCHEMA_NAME_MAPPING_KEYS,
    )
    _close_object_schemas(inlined)
    return inlined


def task_models_with_config(task: TaskDefinition) -> list[type[BaseModel]]:
    configured: dict[type[BaseModel], None] = {}
    for model in (task.params_model, task.output_model):
        for schema_model in _iter_model_graph(model):
            if schema_model.model_config:
                configured[schema_model] = None
    return list(configured)


def task_models_with_root_model(task: TaskDefinition) -> list[type[BaseModel]]:
    root_models: dict[type[BaseModel], None] = {}
    for model in (task.params_model, task.output_model):
        for schema_model in _iter_model_graph(model):
            if issubclass(schema_model, RootModel):
                root_models[schema_model] = None
    return list(root_models)


def validate_no_root_task_models(task: TaskDefinition) -> None:
    root_models = task_models_with_root_model(task)
    if not root_models:
        return
    names = ", ".join(model.__name__ for model in root_models)
    raise TaskDefinitionError(
        "Pydantic RootModel on task model(s) "
        f"{names} is not supported; Perago task contracts must use ordinary BaseModel object models."
    )


def _control_fields(task: TaskDefinition) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for conductor_name, path in CONTROL_FIELD_MAP.items():
        if conductor_name == "responseTimeoutSeconds":
            value: object = _response_timeout_seconds(task)
        else:
            value = task.controls
            for segment in path:
                value = getattr(value, segment)
        if value is not None:
            fields[conductor_name] = value
    return fields


def _response_timeout_seconds(task: TaskDefinition) -> int:
    if task.workspace is not None and task.workspace.read_only:
        return task.controls.timeout.response_seconds
    publish_budget = task.controls.publish_budget
    response_seconds = task.controls.timeout.response_seconds
    if publish_budget is not None and response_seconds < publish_budget.response_timeout_seconds:
        warnings.warn(
            "TaskControls.timeout.response_seconds is shorter than "
            "publish_budget.response_timeout_seconds; responseTimeoutSeconds "
            "will use timeout.response_seconds",
            UserWarning,
            stacklevel=3,
        )
    return response_seconds


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    copied = deepcopy(schema)
    defs = copied.pop("$defs", {})

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                replacement = deepcopy(defs[name])
                siblings = {key: visit(item) for key, item in value.items() if key != "$ref"}
                replacement.update(siblings)
                return visit(replacement)
            return {key: visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    return visit(copied)


def _close_object_schemas(schema: Any) -> None:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)
        for value in schema.values():
            _close_object_schemas(value)
    elif isinstance(schema, list):
        for value in schema:
            _close_object_schemas(value)


def _strip_schema_metadata_keys(schema: Any, keys: Collection[str], *, preserve_mapping_keys: Collection[str]) -> None:
    def visit(value: Any, *, in_preserved_mapping: bool = False) -> None:
        if isinstance(value, dict):
            if not in_preserved_mapping:
                for key in keys:
                    value.pop(key, None)
            for key, item in value.items():
                visit(item, in_preserved_mapping=(key in preserve_mapping_keys))
        elif isinstance(value, list):
            for item in value:
                visit(item, in_preserved_mapping=in_preserved_mapping)

    visit(schema)


def _strip_model_schema_metadata(schema: dict[str, Any]) -> None:
    _strip_object_schema_metadata(schema)
    defs = schema.get("$defs", {})
    if not isinstance(defs, dict):
        return
    for definition in defs.values():
        if isinstance(definition, dict) and definition.get("type") == "object":
            _strip_object_schema_metadata(definition)


def _strip_object_schema_metadata(schema: dict[str, Any]) -> None:
    for key in list(schema):
        if key not in _MODEL_SCHEMA_STRUCTURAL_KEYS:
            schema.pop(key, None)


def _iter_model_graph(model: type[BaseModel]) -> list[type[BaseModel]]:
    seen: set[type[BaseModel]] = set()
    pending = [model]
    ordered: list[type[BaseModel]] = []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        for field in current.model_fields.values():
            pending.extend(_iter_annotation_models(field.annotation))
    return ordered


def _iter_annotation_models(annotation: Any) -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        models.append(annotation)
    for argument in get_args(annotation):
        models.extend(_iter_annotation_models(argument))
    origin = get_origin(annotation)
    if isinstance(origin, type) and issubclass(origin, BaseModel):
        models.append(origin)
    return models
