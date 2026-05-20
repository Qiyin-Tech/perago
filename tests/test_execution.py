from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError

from perago import (
    PostGuardrailViolation,
    PreGuardrailViolation,
    TaskInputError,
    WorkspaceSpec,
    build_workspace_free_task_output,
    build_workspace_task_output,
    invoke_workspace_free_task,
    invoke_workspace_task_body,
    load_module_task,
    require_dir,
    run_workspace_task_attempt,
    task,
)
from perago.workspace import attempt_workspace_dir


class Params(BaseModel):
    value: int = Field(ge=1)


class Output(BaseModel):
    value: int


@dataclass(frozen=True)
class Attempt:
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

    def publish_workspace(workspace_dir, workspace_input, workspace_spec) -> str:
        calls.append(f"publish:{workspace_input.branch}:{workspace_spec.prefix}")
        assert (workspace_dir / "features" / "default.parquet").is_file()
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
        publish_workspace=publish_workspace,
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
        "publish:main:audio/render",
    ]
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_classifies_pre_guardrail_failure_and_cleans(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec, workspace_dir

    def publish_workspace(workspace_dir, workspace_input, workspace_spec) -> str:
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
        publish_workspace=publish_workspace,
    )

    assert result.status == "FAILED_WITH_TERMINAL_ERROR"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


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
