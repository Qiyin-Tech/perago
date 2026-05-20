from dataclasses import dataclass

import pytest

from perago import (
    PublishFenceError,
    WorkspacePublicationPlan,
    WorkspaceSpec,
    build_workspace_publication_plan,
    choose_publish_base,
    confirm_metadata_extra,
    logical_task_key,
    metadata_value,
    perago_metadata,
    staging_branch_name,
)


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


def test_choose_publish_base_uses_input_ref_when_branch_has_not_advanced() -> None:
    assert choose_publish_base(
        workspace=WORKSPACE_INPUT,
        current_head=WORKSPACE_INPUT["ref"],
        commits=[],
        logical_task_key="wf-7f3d:build:2:0:features.build",
    ) == (WORKSPACE_INPUT["ref"], None)


def test_choose_publish_base_accepts_same_logical_task_advancement() -> None:
    assert choose_publish_base(
        workspace=WORKSPACE_INPUT,
        current_head="head-2",
        commits=[
            {"id": "head-1", "metadata": {"perago.logical_task_key": "same-key"}},
            {"id": "head-2", "metadata": {"perago.logical_task_key": "same-key"}},
        ],
        logical_task_key="same-key",
    ) == ("head-2", "head-2")


def test_choose_publish_base_rejects_unrelated_branch_advancement() -> None:
    with pytest.raises(PublishFenceError, match="main advanced"):
        choose_publish_base(
            workspace=WORKSPACE_INPUT,
            current_head="head-2",
            commits=[
                {"id": "head-2", "metadata": {"perago.logical_task_key": "other-key"}},
            ],
            logical_task_key="same-key",
        )


def test_choose_publish_base_rejects_incomplete_commit_ranges() -> None:
    with pytest.raises(PublishFenceError, match="main advanced"):
        choose_publish_base(
            workspace=WORKSPACE_INPUT,
            current_head="head-2",
            commits=[
                {"id": "head-1", "metadata": {"perago.logical_task_key": "same-key"}},
            ],
            logical_task_key="same-key",
        )


def test_staging_branch_name_is_internal_and_attempt_scoped() -> None:
    assert staging_branch_name(Attempt(task_id="task/9b4c", retry_count=3)) == (
        "perago/staging/wf-7f3d/build/seq=2/iteration=0/task_id=task_9b4c/retry=3"
    )


def test_confirm_metadata_extra_matches_publish_metadata_fields() -> None:
    assert confirm_metadata_extra(
        staging_branch="perago/staging/wf/build",
        staging_commit="staging-commit",
        expected_head=WORKSPACE_INPUT["ref"],
        superseded_commit=None,
    ) == {
        "perago.staging_branch": "perago/staging/wf/build",
        "perago.staging_commit": "staging-commit",
        "perago.expected_head": WORKSPACE_INPUT["ref"],
        "perago.supersedes": None,
    }


def test_workspace_publication_plan_combines_publish_fence_and_metadata() -> None:
    plan = build_workspace_publication_plan(
        task=Attempt(retried_task_id="task-old"),
        workspace=WORKSPACE_INPUT,
        workspace_spec=WorkspaceSpec(prefix="/audio/render"),
        current_head="head-2",
        commits=[
            {"id": "head-1", "metadata": {"perago.logical_task_key": "wf-7f3d:build:2:0:features.build"}},
            {"id": "head-2", "metadata": {"perago.logical_task_key": "wf-7f3d:build:2:0:features.build"}},
        ],
        staging_commit="staging-commit",
    )

    assert isinstance(plan, WorkspacePublicationPlan)
    assert plan.logical_task_key == "wf-7f3d:build:2:0:features.build"
    assert plan.staging_branch == "perago/staging/wf-7f3d/build/seq=2/iteration=0/task_id=task-9b4c/retry=1"
    assert plan.publish_base_head == "head-2"
    assert plan.superseded_commit == "head-2"
    assert plan.try_metadata["perago.phase"] == "try"
    assert plan.confirm_metadata["perago.phase"] == "confirm"
    assert plan.confirm_metadata["perago.staging_branch"] == plan.staging_branch
    assert plan.confirm_metadata["perago.staging_commit"] == "staging-commit"
    assert plan.confirm_metadata["perago.expected_head"] == "head-2"
    assert plan.confirm_metadata["perago.supersedes"] == "head-2"
