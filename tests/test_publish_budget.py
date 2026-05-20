import pytest
from pydantic import ValidationError

from perago import PublishBudget, TaskControls, TimeoutPolicy


def test_publish_budget_derives_response_timeout_from_operational_bounds() -> None:
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=45,
        conductor_completion_timeout_seconds=15,
        worker_shutdown_grace_seconds=30,
        heartbeat_interval_seconds=10,
    )

    assert budget.response_timeout_seconds == 100


def test_task_controls_response_timeout_prefers_publish_budget() -> None:
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=45,
        conductor_completion_timeout_seconds=15,
        worker_shutdown_grace_seconds=30,
        heartbeat_interval_seconds=10,
    )

    assert TaskControls(timeout=TimeoutPolicy(response_seconds=999)).response_timeout_seconds == 999
    assert TaskControls(
        timeout=TimeoutPolicy(response_seconds=999),
        publish_budget=budget,
    ).response_timeout_seconds == 100


def test_publish_budget_rejects_unbounded_or_under_sized_values() -> None:
    with pytest.raises(ValidationError):
        PublishBudget(
            observed_merge_p99_seconds=20,
            safety_margin_seconds=10,
            lakefs_merge_timeout_seconds=0,
            conductor_completion_timeout_seconds=15,
            worker_shutdown_grace_seconds=30,
            heartbeat_interval_seconds=10,
        )

    with pytest.raises(ValidationError, match="observed_merge_p99_seconds"):
        PublishBudget(
            observed_merge_p99_seconds=20,
            safety_margin_seconds=10,
            lakefs_merge_timeout_seconds=29,
            conductor_completion_timeout_seconds=15,
            worker_shutdown_grace_seconds=30,
            heartbeat_interval_seconds=10,
        )


def test_publish_budget_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PublishBudget(
            observed_merge_p99_seconds=20,
            safety_margin_seconds=10,
            lakefs_merge_timeout_seconds=45,
            conductor_completion_timeout_seconds=15,
            worker_shutdown_grace_seconds=30,
            heartbeat_interval_seconds=10,
            exact_once=True,
        )
