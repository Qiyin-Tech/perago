from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError

from perago import (
    PostGuardrailViolation,
    PreGuardrailViolation,
    TaskInputError,
    WorkspaceSpec,
    invoke_workspace_free_task,
    invoke_workspace_task_body,
    load_module_task,
    require_dir,
    task,
)


class Params(BaseModel):
    value: int = Field(ge=1)


class Output(BaseModel):
    value: int


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


def test_workspace_free_invocation_rejects_workspace_tasks() -> None:
    task = load_module_task("app.workers.features_build")

    with pytest.raises(TaskInputError, match="workspace-free"):
        invoke_workspace_free_task(task, {"params": {"feature_set": "default", "min_rows": 1}})
