import json

from perago import build_taskdef, load_module_task, write_taskdef


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
