import pytest
from pydantic import ValidationError

from perago import TaskInputError, invoke_workspace_free_task, load_module_task


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
