from pathlib import Path, PureWindowsPath

import pytest
from pydantic import ValidationError

import perago
from perago import (
    GuardrailViolation,
    TaskDefinitionError,
    WorkspaceSpec,
    check_guardrails,
    load_module_task,
    require_file,
    require_glob,
)


def test_loads_workspace_task_definition() -> None:
    task = load_module_task("app.workers.features_build")

    assert task.name == "features.build"
    assert task.workspace is not None
    assert task.workspace.prefix == "audio/render"
    assert task.params_model.__name__ == "BuildFeaturesParams"
    assert task.output_model.__name__ == "BuildFeaturesOutput"


def test_workspace_guardrail_model_is_not_public_api() -> None:
    assert "WorkspaceGuardrail" not in perago.__all__
    assert not hasattr(perago, "WorkspaceGuardrail")


def test_loads_workspace_free_task_definition() -> None:
    task = load_module_task("app.workers.metadata_validate")

    assert task.name == "metadata.validate"
    assert task.workspace is None


def test_rejects_bad_signature() -> None:
    with pytest.raises(TaskDefinitionError, match="workspace task parameters"):
        load_module_task("app.workers.bad_signature")


def test_rejects_multi_task_module() -> None:
    with pytest.raises(TaskDefinitionError, match="more than one"):
        load_module_task("app.workers.multi_task")


def test_guardrail_path_canonicalization() -> None:
    assert require_file(Path("raw") / "manifest.json").path == "raw/manifest.json"
    assert require_file(PureWindowsPath("raw") / "manifest.json").path == "raw/manifest.json"

    with pytest.raises(TaskDefinitionError):
        require_file("/raw/manifest.json")
    with pytest.raises(TaskDefinitionError):
        require_file("../raw/manifest.json")
    with pytest.raises(TaskDefinitionError):
        require_file(r"raw\manifest.json")


def test_workspace_prefix_validation() -> None:
    assert WorkspaceSpec(prefix="/audio/render").prefix == "audio/render"
    with pytest.raises(ValidationError, match="stay inside"):
        WorkspaceSpec(prefix="../raw")


def test_guardrail_runtime_checks(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.parquet").write_text("ok", encoding="utf-8")

    check_guardrails(tmp_path, [require_glob("raw/**/*.parquet", min_count=1)], "pre")

    with pytest.raises(GuardrailViolation, match="min_count=2"):
        check_guardrails(tmp_path, [require_glob("raw/**/*.parquet", min_count=2)], "pre")
