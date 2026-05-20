from dataclasses import dataclass

from perago import WorkspaceSpec, logical_task_key, metadata_value, perago_metadata


@dataclass(frozen=True)
class Attempt:
    workflow_instance_id: str = "wf-7f3d"
    reference_task_name: str = "build"
    seq: int = 2
    iteration: int = 0
    task_def_name: str = "features.build"
    task_id: str = "task-9b4c"
    retry_count: int = 1
    retried_task_id: str | None = None


WORKSPACE_INPUT = {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3",
}


def test_logical_task_key_uses_workflow_stable_fields() -> None:
    assert logical_task_key(Attempt(task_id="attempt-a")) == "wf-7f3d:build:2:0:features.build"
    assert logical_task_key(Attempt(task_id="attempt-b")) == "wf-7f3d:build:2:0:features.build"


def test_metadata_value_uses_lakefs_string_values() -> None:
    assert metadata_value(None) == ""
    assert metadata_value("abc") == "abc"
    assert metadata_value({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_perago_metadata_serializes_transaction_identity() -> None:
    attempt = Attempt(retried_task_id="task-old")
    key = logical_task_key(attempt)

    metadata = perago_metadata(
        task=attempt,
        workspace=WORKSPACE_INPUT,
        workspace_spec=WorkspaceSpec(prefix="/audio/render"),
        logical_task_key=key,
        phase="try",
        extra={
            "perago.supersedes": "previous-commit",
            "perago.extra": {"b": 2, "a": 1},
        },
    )

    assert metadata == {
        "perago.phase": "try",
        "perago.logical_task_key": "wf-7f3d:build:2:0:features.build",
        "perago.workflow_instance_id": "wf-7f3d",
        "perago.task_def_name": "features.build",
        "perago.reference_task_name": "build",
        "perago.seq": "2",
        "perago.iteration": "0",
        "perago.input_ref": WORKSPACE_INPUT["ref"],
        "perago.target_branch": "main",
        "perago.prefix": "audio/render",
        "perago.task_id": "task-9b4c",
        "perago.retry_count": "1",
        "perago.retried_task_id": "task-old",
        "perago.supersedes": "previous-commit",
        "perago.extra": '{"a":1,"b":2}',
    }
