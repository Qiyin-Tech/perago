import json

from pydantic import BaseModel

from perago import build_taskdef, load_module_task, write_taskdef
from perago.taskdef import schema_for_model


class NestedSettings(BaseModel):
    enabled: bool


class NestedParams(BaseModel):
    settings: NestedSettings


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


def test_builds_workspace_free_taskdef() -> None:
    taskdef = build_taskdef(load_module_task("app.workers.metadata_validate"))

    assert taskdef["inputKeys"] == ["params"]
    assert taskdef["outputKeys"] == ["result"]
    assert "workspace" not in taskdef["inputSchema"]["data"]["properties"]
    assert "workspace" not in taskdef["outputSchema"]["data"]["properties"]


def test_writes_taskdef_json(tmp_path) -> None:
    path = write_taskdef(load_module_task("app.workers.metadata_validate"), tmp_path)

    assert path == tmp_path / "taskdefs" / "metadata.validate.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "metadata.validate"


def test_schema_for_model_inlines_refs_and_closes_nested_objects() -> None:
    schema = schema_for_model(NestedParams)

    assert "$defs" not in schema
    assert "$ref" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["settings"]["additionalProperties"] is False
