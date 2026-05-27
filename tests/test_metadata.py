from dataclasses import dataclass

import pytest

from perago import staging_branch_name


@dataclass(frozen=True)
class Attempt:
    workflow_instance_id: str = "wf-7f3d"
    reference_task_name: str = "build"
    seq: int = 2
    iteration: int = 0
    task_def_name: str = "features.build"
    task_id: str = "task-9b4c"
    retry_count: int = 1
    execution_id: str = "exec-1"
    retried_task_id: str | None = None


def test_staging_branch_name_is_internal_and_attempt_scoped() -> None:
    assert staging_branch_name(Attempt(task_id="task/9b4c", retry_count=3)) == (
        "perago-staging-wf-7f3d-build-seq-2-iteration-0-task-id-task-9b4c-retry-3-exec-exec-1"
    )


def test_staging_branch_name_isolated_by_execution_id() -> None:
    first = staging_branch_name(Attempt(execution_id="exec-a"))
    second = staging_branch_name(Attempt(execution_id="exec-b"))

    assert first != second
    assert first.endswith("-exec-exec-a")
    assert second.endswith("-exec-exec-b")


def test_staging_branch_name_uses_lakefs_branch_id_safe_characters() -> None:
    branch = staging_branch_name(
        Attempt(
            workflow_instance_id="4677a373-4878-4e6f-bfa4-876036537a33",
            reference_task_name="hello/workspace",
            seq=1,
            iteration=0,
            task_def_name="perago.smoke.workspace",
            task_id="42cfee5b-bca4-4b78-9bf2-86b47b3df2b6",
            retry_count=0,
            execution_id="exec/42",
        )
    )

    assert branch == (
        "perago-staging-4677a373-4878-4e6f-bfa4-876036537a33-hello-workspace-"
        "seq-1-iteration-0-task-id-42cfee5b-bca4-4b78-9bf2-86b47b3df2b6-retry-0-exec-exec-42"
    )
    assert not branch.startswith("-")
    assert all(char.isalnum() or char in {"_", "-"} for char in branch)


def test_staging_branch_name_reports_missing_required_identity() -> None:
    with pytest.raises(AttributeError, match="workflow_instance_id"):
        staging_branch_name(object())


def test_staging_branch_name_uses_unknown_for_blank_safe_segments() -> None:
    branch = staging_branch_name(Attempt(task_id="///", execution_id="___"))

    assert "-task-id-unknown-" in branch
    assert branch.endswith("-exec-unknown")
