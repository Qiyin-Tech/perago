from dataclasses import dataclass

import pytest

from perago import StaleAttemptError, assert_current_attempt_snapshot


@dataclass(frozen=True)
class AttemptSnapshot:
    status: str
    workflow_instance_id: str
    task_id: str
    retry_count: int


CURRENT_ATTEMPT = AttemptSnapshot(
    status="IN_PROGRESS",
    workflow_instance_id="wf-7f3d",
    task_id="task-9b4c",
    retry_count=2,
)


def test_current_attempt_snapshot_passes_for_matching_in_progress_attempt() -> None:
    assert_current_attempt_snapshot(CURRENT_ATTEMPT, CURRENT_ATTEMPT)


@pytest.mark.parametrize(
    "fresh",
    [
        AttemptSnapshot(status="COMPLETED", workflow_instance_id="wf-7f3d", task_id="task-9b4c", retry_count=2),
        AttemptSnapshot(status="IN_PROGRESS", workflow_instance_id="wf-other", task_id="task-9b4c", retry_count=2),
        AttemptSnapshot(status="IN_PROGRESS", workflow_instance_id="wf-7f3d", task_id="task-other", retry_count=2),
        AttemptSnapshot(status="IN_PROGRESS", workflow_instance_id="wf-7f3d", task_id="task-9b4c", retry_count=3),
    ],
)
def test_current_attempt_snapshot_rejects_stale_attempts(fresh) -> None:
    with pytest.raises(StaleAttemptError, match="task-9b4c"):
        assert_current_attempt_snapshot(CURRENT_ATTEMPT, fresh)


def test_current_attempt_snapshot_reports_missing_required_attribute() -> None:
    incomplete = object()

    with pytest.raises(AttributeError, match="task is missing required attribute status"):
        assert_current_attempt_snapshot(CURRENT_ATTEMPT, incomplete)
