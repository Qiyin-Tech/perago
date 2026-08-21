import pytest
from pydantic import ValidationError

from perago import PublishBudget, TaskControls, TimeoutPolicy


def test_publish_budget_derives_response_timeout_from_operational_bounds() -> None:
    lakefs_merge_timeout_seconds = 45
    conductor_completion_timeout_seconds = 15
    worker_shutdown_grace_seconds = 30
    heartbeat_interval_seconds = 10
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=lakefs_merge_timeout_seconds,
        conductor_completion_timeout_seconds=conductor_completion_timeout_seconds,
        worker_shutdown_grace_seconds=worker_shutdown_grace_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )

    assert budget.response_timeout_seconds == (
        lakefs_merge_timeout_seconds
        + conductor_completion_timeout_seconds
        + worker_shutdown_grace_seconds
        + heartbeat_interval_seconds
    )


def test_task_controls_response_timeout_uses_timeout_policy() -> None:
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
    ).response_timeout_seconds == 999


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


def test_publish_budget_accepts_exact_merge_latency_boundary() -> None:
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=30,
        conductor_completion_timeout_seconds=15,
        worker_shutdown_grace_seconds=30,
        heartbeat_interval_seconds=10,
    )

    assert budget.lakefs_merge_timeout_seconds == 30


def test_publish_budget_response_timeout_matches_runtime_window() -> None:
    merge_timeout = 45
    completion_timeout = 15
    shutdown_grace = 30
    heartbeat_interval = 10
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=merge_timeout,
        conductor_completion_timeout_seconds=completion_timeout,
        worker_shutdown_grace_seconds=shutdown_grace,
        heartbeat_interval_seconds=heartbeat_interval,
    )

    assert budget.response_timeout_seconds == (
        merge_timeout + completion_timeout + shutdown_grace + heartbeat_interval
    )


def test_publish_budget_sums_short_runtime_window() -> None:
    budget = PublishBudget(
        observed_merge_p99_seconds=10,
        safety_margin_seconds=5,
        lakefs_merge_timeout_seconds=20,
        conductor_completion_timeout_seconds=5,
        worker_shutdown_grace_seconds=6,
        heartbeat_interval_seconds=2,
    )

    assert budget.response_timeout_seconds == 33


def test_publish_budget_sums_medium_runtime_window() -> None:
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=5,
        lakefs_merge_timeout_seconds=30,
        conductor_completion_timeout_seconds=8,
        worker_shutdown_grace_seconds=12,
        heartbeat_interval_seconds=4,
    )

    assert budget.response_timeout_seconds == 54


def test_publish_budget_sums_long_runtime_window() -> None:
    budget = PublishBudget(
        observed_merge_p99_seconds=40,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=60,
        conductor_completion_timeout_seconds=20,
        worker_shutdown_grace_seconds=30,
        heartbeat_interval_seconds=10,
    )

    assert budget.response_timeout_seconds == 120


def test_publish_budget_response_timeout_is_stable() -> None:
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=45,
        conductor_completion_timeout_seconds=15,
        worker_shutdown_grace_seconds=30,
        heartbeat_interval_seconds=10,
    )

    assert budget.response_timeout_seconds == 100
    assert budget.response_timeout_seconds == 100
