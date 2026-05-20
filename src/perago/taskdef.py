from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from perago.models import WorkspaceInput, WorkspaceOutput
from perago.task import TaskDefinition


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
        **_control_fields(task),
        "inputKeys": input_required,
        "outputKeys": output_required,
        "inputSchema": {
            "name": f"{task.name}.input",
            "version": 1,
            "type": "JSON",
            "data": _object_schema(input_properties, input_required),
        },
        "outputSchema": {
            "name": f"{task.name}.output",
            "version": 1,
            "type": "JSON",
            "data": _object_schema(output_properties, output_required),
        },
    }
    if task.description is not None:
        data["description"] = task.description
    return data


def write_taskdef(task: TaskDefinition, out: Path) -> Path:
    path = out / "taskdefs" / f"{task.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_taskdef(task), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def schema_for_model(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    inlined = _inline_refs(schema)
    inlined.pop("title", None)
    _close_object_schemas(inlined)
    return inlined


def _control_fields(task: TaskDefinition) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for conductor_name, path in CONTROL_FIELD_MAP.items():
        value: object = task.controls
        for segment in path:
            value = getattr(value, segment)
        if value is not None:
            fields[conductor_name] = value
    return fields


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
