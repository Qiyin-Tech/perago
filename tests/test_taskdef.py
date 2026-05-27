import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from perago import (
    PublishBudget,
    RetryPolicy,
    TaskControls,
    TaskDefinitionError,
    TimeoutPolicy,
    WorkspaceSpec,
    build_taskdef,
    load_module_task,
    task,
    write_taskdef,
)
from perago.models import (
    DEFAULT_RETRY_COUNT,
    DEFAULT_RETRY_DELAY_SECONDS,
    DEFAULT_TIMEOUT_RESPONSE_SECONDS,
    MAX_RETRY_COUNT,
)
from perago.taskdef import TASKDEF_SCHEMA_TYPE, TASKDEF_SCHEMA_VERSION, schema_for_model


def _add_examples(schema: dict[str, object]) -> None:
    schema["examples"] = [{"enabled": True}]


def _add_description(schema: dict[str, object]) -> None:
    schema["description"] = "Explicit callable schema description."


class NestedSettings(BaseModel):
    enabled: bool


class NestedParams(BaseModel):
    settings: NestedSettings


class NestedSettingsWithDoc(BaseModel):
    """Nested model docstring must not be serialized into the TaskDef schema."""

    enabled: bool = Field(description="Whether this nested setting is enabled.")


class NestedParamsWithDescriptions(BaseModel):
    """Root model docstring must not be serialized into the TaskDef schema."""

    settings: NestedSettingsWithDoc = Field(description="Settings object supplied by the task author.")
    label: str = Field(description="Human-readable label supplied by the task author.")


class ParamsWithDescriptionField(BaseModel):
    description: str


class NestedSettingsWithSchemaDescription(BaseModel):
    """Nested model docstring must still be treated as Python API documentation."""

    model_config = ConfigDict(json_schema_extra={"description": "Explicit nested schema description."})

    enabled: bool


class ParamsWithSchemaDescription(BaseModel):
    """Root model docstring must still be treated as Python API documentation."""

    model_config = ConfigDict(json_schema_extra={"description": "Explicit params schema description."})

    settings: NestedSettingsWithSchemaDescription
    description: str


class NestedSettingsWithCallableSchemaExtra(BaseModel):
    """Callable nested model docstring must not be serialized into the TaskDef schema."""

    model_config = ConfigDict(json_schema_extra=_add_examples)

    enabled: bool


class ParamsWithCallableSchemaExtra(BaseModel):
    """Callable root model docstring must not be serialized into the TaskDef schema."""

    model_config = ConfigDict(json_schema_extra=_add_examples)

    settings: NestedSettingsWithCallableSchemaExtra


class ParamsWithCallableSchemaDescription(BaseModel):
    """Callable schema description should replace this Python docstring."""

    model_config = ConfigDict(json_schema_extra=_add_description)

    enabled: bool


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


@task(
    name="tests.read_only_publish_budget",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(read_only=True),
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
def read_only_budgeted_workspace_task(workspace: Path, params: BudgetParams) -> BudgetOutput:
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
    assert list(taskdef)[:3] == ["name", "ownerEmail", "description"]
    assert taskdef["description"] == "Build feature parquet files."
    assert taskdef["retryCount"] == 4
    assert taskdef["responseTimeoutSeconds"] == 900
    assert taskdef["concurrentExecLimit"] == 2
    assert taskdef["inputKeys"] == ["workspace", "params"]
    assert taskdef["outputKeys"] == ["workspace", "result"]
    assert taskdef["inputSchema"]["version"] == TASKDEF_SCHEMA_VERSION
    assert taskdef["inputSchema"]["type"] == TASKDEF_SCHEMA_TYPE
    assert taskdef["outputSchema"]["version"] == TASKDEF_SCHEMA_VERSION
    assert taskdef["outputSchema"]["type"] == TASKDEF_SCHEMA_TYPE
    assert "inputTemplate" not in taskdef
    assert taskdef["inputSchema"]["data"]["additionalProperties"] is False
    workspace_input = taskdef["inputSchema"]["data"]["properties"]["workspace"]
    assert workspace_input["required"] == ["repository", "branch", "ref_type", "ref"]
    assert "description" not in workspace_input
    workspace_output = taskdef["outputSchema"]["data"]["properties"]["workspace"]
    assert workspace_output["required"] == ["repository", "branch", "ref_type", "ref"]
    assert "description" not in workspace_output
    serialized = json.dumps(taskdef)
    assert "guardrail" not in serialized
    assert "require_glob" not in serialized
    assert "forbid_glob" not in serialized


def test_task_control_defaults_and_limits_are_named_contract_values() -> None:
    retry = RetryPolicy()
    timeout = TimeoutPolicy()

    assert retry.count == DEFAULT_RETRY_COUNT
    assert retry.delay_seconds == DEFAULT_RETRY_DELAY_SECONDS
    assert timeout.response_seconds == DEFAULT_TIMEOUT_RESPONSE_SECONDS

    with pytest.raises(ValueError):
        RetryPolicy(count=MAX_RETRY_COUNT + 1)


def test_taskdef_uses_timeout_response_seconds_with_publish_budget() -> None:
    taskdef = build_taskdef(budgeted_workspace_task.__perago_task__)

    assert taskdef["responseTimeoutSeconds"] == 999
    assert "publish_budget" not in taskdef
    assert "lakefs_merge_timeout_seconds" not in json.dumps(taskdef)


def test_taskdef_warns_when_publish_budget_exceeds_response_timeout() -> None:
    @task(
        name="tests.short_response_timeout_publish_budget",
        owner_email="data@example.com",
        workspace=WorkspaceSpec(),
        controls=TaskControls(
            timeout=TimeoutPolicy(response_seconds=60),
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
    def short_timeout_task(workspace: Path, params: BudgetParams) -> BudgetOutput:
        del workspace
        return BudgetOutput(value=params.value)

    with pytest.warns(UserWarning, match="timeout.response_seconds is shorter"):
        taskdef = build_taskdef(short_timeout_task.__perago_task__)

    assert taskdef["responseTimeoutSeconds"] == 60
    assert "publish_budget" not in taskdef
    assert "lakefs_merge_timeout_seconds" not in json.dumps(taskdef)


def test_taskdef_ignores_publish_budget_for_read_only_workspace_task() -> None:
    taskdef = build_taskdef(read_only_budgeted_workspace_task.__perago_task__)

    assert taskdef["responseTimeoutSeconds"] == 999
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


def test_taskdef_rejects_root_model_task_contracts() -> None:
    with pytest.raises(TaskDefinitionError, match="RootModel"):
        build_taskdef(load_module_task("app.workers.root_model_task"))


def test_writes_taskdef_json(tmp_path) -> None:
    output = tmp_path / "generated" / "metadata.validate.json"
    path = write_taskdef(load_module_task("app.workers.metadata_validate"), output)

    assert path == output
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "metadata.validate"


def test_write_taskdef_requires_json_file_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="output must be a JSON file path"):
        write_taskdef(load_module_task("app.workers.metadata_validate"), tmp_path / "generated")

    directory_with_json_suffix = tmp_path / "generated.json"
    directory_with_json_suffix.mkdir()

    with pytest.raises(ValueError, match="not a directory"):
        write_taskdef(load_module_task("app.workers.metadata_validate"), directory_with_json_suffix)


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
    assert "description" not in schema
    assert schema["additionalProperties"] is False
    assert schema["properties"]["settings"]["additionalProperties"] is False


def test_schema_for_model_preserves_field_descriptions_while_stripping_model_docstrings() -> None:
    schema = schema_for_model(NestedParamsWithDescriptions)

    serialized = json.dumps(schema)
    assert "Root model docstring" not in serialized
    assert "Nested model docstring" not in serialized
    assert schema["properties"]["settings"]["description"] == "Settings object supplied by the task author."
    assert (
        schema["properties"]["settings"]["properties"]["enabled"]["description"]
        == "Whether this nested setting is enabled."
    )
    assert schema["properties"]["label"]["description"] == "Human-readable label supplied by the task author."


def test_schema_for_model_preserves_fields_named_description() -> None:
    schema = schema_for_model(ParamsWithDescriptionField)

    assert "description" in schema["properties"]
    assert schema["properties"]["description"]["type"] == "string"


def test_schema_for_model_strips_model_level_schema_metadata() -> None:
    schema = schema_for_model(ParamsWithSchemaDescription)

    serialized = json.dumps(schema)
    assert "Root model docstring" not in serialized
    assert "Nested model docstring" not in serialized
    assert "Explicit params schema description." not in serialized
    assert "Explicit nested schema description." not in serialized
    assert "description" not in schema
    assert "description" not in schema["properties"]["settings"]
    assert "description" in schema["properties"]
    assert schema["properties"]["description"]["type"] == "string"


def test_schema_for_model_strips_callable_model_level_schema_extra() -> None:
    schema = schema_for_model(ParamsWithCallableSchemaExtra)

    serialized = json.dumps(schema)
    assert "Callable root model docstring" not in serialized
    assert "Callable nested model docstring" not in serialized
    assert "examples" not in schema
    assert "examples" not in schema["properties"]["settings"]


def test_schema_for_model_strips_callable_model_level_schema_description() -> None:
    schema = schema_for_model(ParamsWithCallableSchemaDescription)

    serialized = json.dumps(schema)
    assert "Callable schema description should replace this Python docstring." not in serialized
    assert "Explicit callable schema description." not in serialized
    assert "description" not in schema
