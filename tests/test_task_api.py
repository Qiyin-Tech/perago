from pathlib import Path, PurePath, PureWindowsPath

import pytest
from pydantic import BaseModel, ValidationError

import perago
from perago import (
    GuardrailViolation,
    PublishBudget,
    TaskDefinitionError,
    TaskControls,
    WorkspaceSpec,
    check_guardrails,
    forbid_glob,
    load_module_task,
    require_dir,
    require_file,
    require_glob,
    task,
)
from perago.guards import _WorkspaceGuardrail


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


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


def test_rejects_async_task_functions() -> None:
    with pytest.raises(TaskDefinitionError, match="synchronous function"):
        load_module_task("app.workers.bad_async_task")


def test_rejects_task_parameter_defaults() -> None:
    with pytest.raises(TaskDefinitionError, match="must not declare defaults"):
        load_module_task("app.workers.bad_default_param")


def test_rejects_variadic_or_keyword_only_task_signatures() -> None:
    with pytest.raises(TaskDefinitionError, match="must not use"):
        load_module_task("app.workers.bad_variadic_signature")
    with pytest.raises(TaskDefinitionError, match="must not use"):
        load_module_task("app.workers.bad_keyword_only_signature")


def test_rejects_missing_task_contract_annotations() -> None:
    with pytest.raises(TaskDefinitionError, match="params must be annotated"):
        load_module_task("app.workers.bad_missing_params_annotation")
    with pytest.raises(TaskDefinitionError, match="return value must be annotated"):
        load_module_task("app.workers.bad_missing_return_annotation")


def test_rejects_multi_task_module() -> None:
    with pytest.raises(TaskDefinitionError, match="more than one"):
        load_module_task("app.workers.multi_task")


def test_rejects_module_without_task() -> None:
    with pytest.raises(TaskDefinitionError, match="does not declare"):
        load_module_task("app.workers.no_task")


def test_rejects_non_module_targets() -> None:
    for target in [
        "",
        "app..workers.features_build",
        "app/workers/features_build.py",
        "app.workers.features-build",
        "app.workers.features_build:build_features",
        "app\\workers\\features_build.py",
    ]:
        with pytest.raises(TaskDefinitionError, match="Python import path"):
            load_module_task(target)


def test_rejects_missing_required_task_metadata() -> None:
    with pytest.raises(TaskDefinitionError, match="task name is required"):

        @task(name=" ", owner_email="data@example.com")
        def missing_name(params: Params) -> Output:
            return Output(value=params.value)

    with pytest.raises(TaskDefinitionError, match="path separators"):
        load_module_task("app.workers.bad_task_name_path")

    with pytest.raises(TaskDefinitionError, match="owner_email is required"):

        @task(name="metadata.validate", owner_email="")
        def missing_owner(params: Params) -> Output:
            return Output(value=params.value)


def test_rejects_duplicate_contract_metadata() -> None:
    with pytest.raises(TaskDefinitionError, match="unsupported task decorator fields: output, params"):

        @task(name="metadata.validate", owner_email="data@example.com", params=Params, output=Output)
        def duplicate_contract(params: Params) -> Output:
            return Output(value=params.value)


def test_rejects_invalid_task_decorator_option_types() -> None:
    with pytest.raises(TaskDefinitionError, match="controls must be a TaskControls"):
        load_module_task("app.workers.bad_decorator_types")

    with pytest.raises(TaskDefinitionError, match="workspace must be a WorkspaceSpec"):

        @task(name="bad.workspace.type", owner_email="data@example.com", workspace={})
        def bad_workspace_type(params: Params) -> Output:
            return Output(value=params.value)


def test_rejects_publish_budget_on_workspace_free_tasks() -> None:
    budget = PublishBudget(
        observed_merge_p99_seconds=1,
        safety_margin_seconds=1,
        lakefs_merge_timeout_seconds=2,
        conductor_completion_timeout_seconds=1,
        worker_shutdown_grace_seconds=1,
        heartbeat_interval_seconds=1,
    )

    with pytest.raises(TaskDefinitionError, match="publish_budget requires workspace"):

        @task(
            name="bad.publish_budget",
            owner_email="data@example.com",
            controls=TaskControls(publish_budget=budget),
        )
        def bad_publish_budget(params: Params) -> Output:
            return Output(value=params.value)


def test_guardrail_path_canonicalization() -> None:
    assert require_file(Path("raw") / "manifest.json").path == "raw/manifest.json"
    assert require_file(PureWindowsPath("raw") / "manifest.json").path == "raw/manifest.json"

    with pytest.raises(TaskDefinitionError):
        require_file("/raw/manifest.json")
    with pytest.raises(TaskDefinitionError):
        require_file("../raw/manifest.json")
    with pytest.raises(TaskDefinitionError):
        require_file(r"raw\manifest.json")
    with pytest.raises(TaskDefinitionError, match="must not be empty"):
        require_file(Path(""))
    with pytest.raises(TaskDefinitionError, match="drive-qualified"):
        require_file(PureWindowsPath("C:/raw/manifest.json"))
    with pytest.raises(TaskDefinitionError, match="absolute"):
        require_file(PurePath("/raw/manifest.json"))


def test_guardrail_count_bound_validation() -> None:
    with pytest.raises(ValidationError, match="require_file does not accept count bounds"):
        _WorkspaceGuardrail(kind="require_file", path="raw/manifest.json", min_count=1)
    with pytest.raises(ValidationError, match="min_count must be <= max_count"):
        require_glob("raw/*.parquet", min_count=3, max_count=2)

    guardrail = _WorkspaceGuardrail(kind="require_glob", path="raw/*.parquet", min_count=None)

    assert guardrail.min_count == 1


def test_workspace_prefix_validation() -> None:
    assert WorkspaceSpec(prefix="/audio/render").prefix == "audio/render"
    assert WorkspaceSpec(prefix="/audio/render").read_only is False
    assert WorkspaceSpec(prefix="/audio/render", read_only=True).read_only is True
    with pytest.raises(ValidationError, match="stay inside"):
        WorkspaceSpec(prefix="../raw")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkspaceSpec(prefx="/audio/render")


def test_guardrail_runtime_checks(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.parquet").write_text("ok", encoding="utf-8")

    check_guardrails(tmp_path, [require_glob("raw/**/*.parquet", min_count=1)], "pre")
    check_guardrails(tmp_path, [require_file("raw/a.parquet"), require_dir("raw")], "pre")
    check_guardrails(tmp_path, [forbid_glob("raw/*.csv")], "post")

    with pytest.raises(GuardrailViolation, match="min_count=2"):
        check_guardrails(tmp_path, [require_glob("raw/**/*.parquet", min_count=2)], "pre")
    with pytest.raises(GuardrailViolation, match="did not find a file"):
        check_guardrails(tmp_path, [require_file("raw/missing.parquet")], "pre")
    with pytest.raises(GuardrailViolation, match="did not find a directory"):
        check_guardrails(tmp_path, [require_dir("raw/missing")], "pre")
    with pytest.raises(GuardrailViolation, match="matched 1 files"):
        check_guardrails(tmp_path, [forbid_glob("raw/*.parquet")], "post")
    with pytest.raises(GuardrailViolation, match="max_count=0"):
        check_guardrails(tmp_path, [require_glob("raw/*.parquet", min_count=0, max_count=0)], "post")
