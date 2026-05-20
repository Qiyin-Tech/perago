import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from perago import (
    PublishBudget,
    TaskControls,
    TimeoutPolicy,
    WorkspaceSpec,
    build_taskdef,
    load_module_task,
    task,
    write_taskdef,
)
from perago.taskdef import schema_for_model


class NestedSettings(BaseModel):
    enabled: bool


class NestedParams(BaseModel):
    settings: NestedSettings


class ParamsWithDefaults(BaseModel):
    required_value: int
    optional_reason: str | None = None


class OutputWithDefaults(BaseModel):
    ok: bool = True


class BudgetParams(BaseModel):
    value: int


class BudgetOutput(BaseModel):
    value: int


class ModelWithTitleField(BaseModel):
    title: str
    count: int = 1


class ResultWithOptionalTitle(BaseModel):
    title: str | None = None


@task(name="tests.title_field", owner_email="data@example.com")
def title_field_task(params: ModelWithTitleField) -> ResultWithOptionalTitle:
    return ResultWithOptionalTitle(title=params.title)


@task(
    name="tests.publish_budget",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(),
    controls=TaskControls(
        timeout=TimeoutPolicy(response_seconds=999),
        publish_budget=PublishBudget(
            observed_merge_p99_seconds=20,
            safety_margin_seconds=10,
            lakefs_merge_timeout_seconds=45,
            conductor_completion_timeout_seconds=15,
            worker_shutdown_grace_seconds=30,
            heartbeat_interval_seconds=10,
        ),
    ),
)
def budgeted_workspace_task(workspace: Path, params: BudgetParams) -> BudgetOutput:
    del workspace
    return BudgetOutput(value=params.value)


@task(name="tests.defaults", owner_email="data@example.com")
def defaults_task(params: ParamsWithDefaults) -> OutputWithDefaults:
    del params
    return OutputWithDefaults()


def test_builds_workspace_taskdef() -> None:
    taskdef = build_taskdef(load_module_task("app.workers.features_build"))

    assert taskdef["name"] == "features.build"
    assert taskdef["ownerEmail"] == "data@example.com"
    assert taskdef["retryCount"] == 4
    assert taskdef["responseTimeoutSeconds"] == 900
    assert taskdef["concurrentExecLimit"] == 2
    assert taskdef["inputKeys"] == ["workspace", "params"]
    assert taskdef["outputKeys"] == ["workspace", "result"]
    assert "inputTemplate" not in taskdef
    assert taskdef["inputSchema"]["data"]["additionalProperties"] is False
    workspace_input = taskdef["inputSchema"]["data"]["properties"]["workspace"]
    assert workspace_input["required"] == ["repository", "branch", "ref_type", "ref"]
    workspace_output = taskdef["outputSchema"]["data"]["properties"]["workspace"]
    assert workspace_output["required"] == ["repository", "branch", "ref_type", "ref"]
    serialized = json.dumps(taskdef)
    assert "guardrail" not in serialized
    assert "require_glob" not in serialized
    assert "forbid_glob" not in serialized


def test_taskdef_derives_response_timeout_from_publish_budget() -> None:
    taskdef = build_taskdef(budgeted_workspace_task.__perago_task__)

    assert taskdef["responseTimeoutSeconds"] == 100
    assert "publish_budget" not in taskdef
    assert "lakefs_merge_timeout_seconds" not in json.dumps(taskdef)


def test_taskdef_keeps_schema_defaults_without_input_template() -> None:
    taskdef = build_taskdef(defaults_task.__perago_task__)

    params_schema = taskdef["inputSchema"]["data"]["properties"]["params"]
    result_schema = taskdef["outputSchema"]["data"]["properties"]["result"]

    assert "inputTemplate" not in taskdef
    assert params_schema["properties"]["optional_reason"]["default"] is None
    assert result_schema["properties"]["ok"]["default"] is True


def test_builds_workspace_free_taskdef() -> None:
    taskdef = build_taskdef(load_module_task("app.workers.metadata_validate"))

    assert taskdef["inputKeys"] == ["params"]
    assert taskdef["outputKeys"] == ["result"]
    assert "workspace" not in taskdef["inputSchema"]["data"]["properties"]
    assert "workspace" not in taskdef["outputSchema"]["data"]["properties"]


def test_writes_taskdef_json(tmp_path) -> None:
    output = tmp_path / "generated" / "metadata.validate.json"
    path = write_taskdef(load_module_task("app.workers.metadata_validate"), output)

    assert path == output
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "metadata.validate"


def test_write_taskdef_requires_json_file_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="output must be a JSON file path"):
        write_taskdef(load_module_task("app.workers.metadata_validate"), tmp_path / "generated")



def test_schema_preserves_fields_named_title() -> None:
    taskdef = build_taskdef(title_field_task.__perago_task__)

    params_schema = taskdef["inputSchema"]["data"]["properties"]["params"]
    result_schema = taskdef["outputSchema"]["data"]["properties"]["result"]

    assert "title" in params_schema["properties"]
    assert "title" in params_schema["required"]
    assert params_schema["additionalProperties"] is False
    assert "title" in result_schema["properties"]
    assert result_schema["additionalProperties"] is False

def test_schema_for_model_inlines_refs_and_closes_nested_objects() -> None:
    schema = schema_for_model(NestedParams)

    assert "$defs" not in schema
    assert "$ref" not in json.dumps(schema)
    assert "title" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["settings"]["additionalProperties"] is False
