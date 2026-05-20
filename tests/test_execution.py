from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError

from perago import (
    PostGuardrailViolation,
    PreGuardrailViolation,
    StagedWorkspace,
    TaskInputError,
    WorkspaceSpec,
    build_workspace_free_task_output,
    build_workspace_task_output,
    invoke_workspace_free_task,
    invoke_workspace_task_body,
    load_module_task,
    require_dir,
    run_workspace_free_task_attempt,
    run_workspace_task_attempt,
    task,
)
from perago.workspace import attempt_workspace_dir
from perago.workspace import active_workspace_owner_tokens


class Params(BaseModel):
    value: int = Field(ge=1)


class Output(BaseModel):
    value: int


class NestedParams(BaseModel):
    settings: Params


class NestedOutput(BaseModel):
    value: int


@dataclass(frozen=True)
class Attempt:
    status: str = "IN_PROGRESS"
    workflow_instance_id: str = "wf-7f3d"
    task_def_name: str = "features.build"
    task_id: str = "9b4c"
    retry_count: int = 2


@task(
    name="tests.post_failure",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(post=[require_dir("missing")]),
)
def post_failure_task(workspace: Path, params: Params) -> Output:
    return Output(value=params.value)


@task(name="tests.nested_params", owner_email="data@example.com")
def nested_params_task(params: NestedParams) -> NestedOutput:
    return NestedOutput(value=params.settings.value)


WORKSPACE_INPUT = {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3",
}


def _attempt() -> Attempt:
    return Attempt()


def test_invokes_workspace_task_body_with_guardrails(tmp_path) -> None:
    task_def = load_module_task("app.workers.features_build")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "input.parquet").write_text("ok", encoding="utf-8")

    output = invoke_workspace_task_body(
        task_def,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        tmp_path,
    )

    assert output == {"result": {"row_count": 100, "feature_count": 24}}
    assert (tmp_path / "features" / "default.parquet").is_file()


def test_workspace_task_body_classifies_pre_guardrail_failure(tmp_path) -> None:
    task_def = load_module_task("app.workers.features_build")

    with pytest.raises(PreGuardrailViolation, match="pre guardrail"):
        invoke_workspace_task_body(
            task_def,
            {
                "workspace": WORKSPACE_INPUT,
                "params": {"feature_set": "default", "min_rows": 100},
            },
            tmp_path,
        )

    assert not (tmp_path / "features").exists()


def test_workspace_task_body_classifies_post_guardrail_failure(tmp_path) -> None:
    task_def = post_failure_task.__perago_task__

    with pytest.raises(PostGuardrailViolation, match="post guardrail"):
        invoke_workspace_task_body(
            task_def,
            {
                "workspace": WORKSPACE_INPUT,
                "params": {"value": 1},
            },
            tmp_path,
        )


def test_run_workspace_task_attempt_publishes_completed_output_and_cleans(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()
    calls: list[str] = []

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        calls.append(f"download:{workspace_input.ref}:{workspace_spec.prefix}")
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    def stage_workspace(workspace_dir, workspace_input, workspace_spec, attempt) -> StagedWorkspace:
        del attempt
        calls.append(f"stage:{workspace_input.branch}:{workspace_spec.prefix}")
        assert (workspace_dir / "features" / "default.parquet").is_file()
        return StagedWorkspace(branch="perago/staging/wf/build", commit="staging-commit")

    def publish_workspace(staged, workspace_input, workspace_spec, attempt) -> str:
        del workspace_input, workspace_spec, attempt
        calls.append(f"publish:{staged.branch}:{staged.commit}")
        return "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca"

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=stage_workspace,
        publish_workspace=publish_workspace,
        cleanup_staging=lambda staged: calls.append(f"cleanup:{staged.branch}"),
        owner_worker_id="featuresBuild0001",
    )

    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {
            "workspace": {
                "repository": "song-000123",
                "branch": "main",
                "ref_type": "commit",
                "ref": "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca",
            },
            "result": {"row_count": 100, "feature_count": 24},
        },
    }
    assert calls == [
        "download:589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3:audio/render",
        "stage:main:audio/render",
        "publish:perago/staging/wf/build:staging-commit",
        "cleanup:perago/staging/wf/build",
    ]
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_registers_owner_token_during_attempt(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()
    observed_tokens: list[set[str]] = []

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        observed_tokens.append(active_workspace_owner_tokens())
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            branch="perago/staging/wf/build",
            commit="staging-commit",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: (
            "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca"
        ),
        cleanup_staging=lambda staged: None,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "COMPLETED"
    assert len(observed_tokens) == 1
    assert len(observed_tokens[0]) == 1
    assert active_workspace_owner_tokens().isdisjoint(observed_tokens[0])


def test_run_workspace_task_attempt_classifies_pre_guardrail_failure_and_cleans(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec, workspace_dir

    def stage_workspace(workspace_dir, workspace_input, workspace_spec, attempt) -> StagedWorkspace:
        raise AssertionError("pre guardrail failure must not stage")

    def publish_workspace(staged, workspace_input, workspace_spec, attempt) -> str:
        raise AssertionError("pre guardrail failure must not publish")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=stage_workspace,
        publish_workspace=publish_workspace,
        cleanup_staging=lambda staged: None,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED_WITH_TERMINAL_ERROR"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_checks_attempt_fence_before_publish(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    def stage_workspace(workspace_dir, workspace_input, workspace_spec, attempt) -> StagedWorkspace:
        raise AssertionError("stale attempts must not publish")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: Attempt(status="COMPLETED"),
        stage_workspace=stage_workspace,
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "9b4c"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_cleans_staging_when_second_attempt_fence_fails(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()
    fresh_attempts = iter([attempt, Attempt(status="COMPLETED")])
    calls: list[str] = []

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: next(fresh_attempts),
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            branch="perago/staging/wf/build",
            commit="staging-commit",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: calls.append("publish") or "unused",
        cleanup_staging=lambda staged: calls.append(f"cleanup:{staged.branch}"),
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "9b4c"
    assert calls == ["cleanup:perago/staging/wf/build"]
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_preserves_result_when_staging_cleanup_fails(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    def cleanup_staging(staged) -> None:
        raise RuntimeError(f"delete failed: {staged.branch}")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            branch="perago/staging/wf/build",
            commit="staging-commit",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: (
            "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca"
        ),
        cleanup_staging=cleanup_staging,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "COMPLETED"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_returns_failed_result_for_bad_input(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    result = run_workspace_task_attempt(
        task,
        {"params": {"feature_set": "default", "min_rows": 100}},
        attempt,
        tmp_path,
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            branch="unused",
            commit="unused",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED"
    assert "workspace task input" in result.reason_for_incompletion
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_free_task_attempt_returns_completed_result() -> None:
    task = load_module_task("app.workers.metadata_validate")

    result = run_workspace_free_task_attempt(
        task,
        {
            "params": {
                "song_id": "song-000123",
                "min_duration_seconds": 30,
            },
        },
    )

    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {"result": {"valid": True, "reason": None}},
    }


def test_run_workspace_free_task_attempt_returns_failed_result_for_bad_input() -> None:
    task = load_module_task("app.workers.metadata_validate")

    result = run_workspace_free_task_attempt(
        task,
        {
            "song_id": "song-000123",
            "min_duration_seconds": 30,
        },
    )

    assert result.status == "FAILED"
    assert "workspace-free task input" in result.reason_for_incompletion


def test_invokes_workspace_free_task_from_wrapped_params() -> None:
    task = load_module_task("app.workers.metadata_validate")

    output = invoke_workspace_free_task(
        task,
        {
            "params": {
                "song_id": "song-000123",
                "min_duration_seconds": 30,
            },
        },
    )

    assert output == {"result": {"valid": True, "reason": None}}


def test_builds_workspace_task_output_with_published_ref() -> None:
    task = load_module_task("app.workers.features_build")

    output = build_workspace_task_output(
        task,
        WORKSPACE_INPUT,
        "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca",
        {"row_count": 100, "feature_count": 24},
    )

    assert output == {
        "workspace": {
            "repository": "song-000123",
            "branch": "main",
            "ref_type": "commit",
            "ref": "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca",
        },
        "result": {"row_count": 100, "feature_count": 24},
    }


def test_builds_workspace_free_task_output() -> None:
    task = load_module_task("app.workers.metadata_validate")

    output = build_workspace_free_task_output(task, {"valid": False, "reason": "missing"})

    assert output == {"result": {"valid": False, "reason": "missing"}}


def test_workspace_free_invocation_rejects_expanded_top_level_params() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(TaskInputError, match="contain only params"):
        invoke_workspace_free_task(
            task,
            {
                "song_id": "song-000123",
                "min_duration_seconds": 30,
            },
        )


def test_workspace_free_invocation_validates_params_model() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(ValidationError):
        invoke_workspace_free_task(
            task,
            {
                "params": {
                    "song_id": "song-000123",
                    "min_duration_seconds": 0,
                },
            },
        )


def test_workspace_free_invocation_rejects_extra_business_params() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        invoke_workspace_free_task(
            task,
            {
                "params": {
                    "song_id": "song-000123",
                    "min_duration_seconds": 30,
                    "workspace": "not-a-workspace",
                },
            },
        )


def test_workspace_free_invocation_rejects_nested_extra_business_params() -> None:
    task = nested_params_task.__perago_task__

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        invoke_workspace_free_task(
            task,
            {
                "params": {
                    "settings": {
                        "value": 1,
                        "extra": "not-in-schema",
                    },
                },
            },
        )


def test_workspace_task_body_rejects_extra_business_params(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "input.parquet").write_text("ok", encoding="utf-8")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        invoke_workspace_task_body(
            task,
            {
                "workspace": WORKSPACE_INPUT,
                "params": {
                    "feature_set": "default",
                    "min_rows": 100,
                    "workspace": "not-a-workspace",
                },
            },
            tmp_path,
        )


def test_workspace_free_output_rejects_extra_business_result_fields() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        build_workspace_free_task_output(task, {"valid": True, "extra": "ignored-by-default"})


def test_workspace_task_body_requires_ref_type(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    workspace_input = dict(WORKSPACE_INPUT)
    workspace_input.pop("ref_type")

    with pytest.raises(ValidationError):
        invoke_workspace_task_body(
            task,
            {
                "workspace": workspace_input,
                "params": {"feature_set": "default", "min_rows": 100},
            },
            tmp_path,
        )


def test_workspace_free_invocation_rejects_workspace_tasks() -> None:
    task = load_module_task("app.workers.features_build")

    with pytest.raises(TaskInputError, match="workspace-free"):
        invoke_workspace_free_task(task, {"params": {"feature_set": "default", "min_rows": 1}})
