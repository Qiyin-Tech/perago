import pytest

from perago import RuntimeConfigError, restart_backoff_seconds, worker_child_specs


def test_restart_backoff_sequence_caps_at_maximum() -> None:
    assert [restart_backoff_seconds(index) for index in range(7)] == [1, 2, 4, 8, 16, 30, 30]


def test_restart_backoff_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="restart_count"):
        restart_backoff_seconds(-1)


def test_worker_child_specs_assign_stable_slot_worker_ids() -> None:
    specs = worker_child_specs(
        base_env={},
        module_target="app.workers.features_build",
        process_count=4,
    )

    assert [spec.slot for spec in specs] == [1, 2, 3, 4]
    assert [spec.worker_id for spec in specs] == [
        "appworkersfeaturesbuild0001",
        "appworkersfeaturesbuild0002",
        "appworkersfeaturesbuild0003",
        "appworkersfeaturesbuild0004",
    ]


def test_worker_child_specs_reuse_configured_prefix() -> None:
    specs = worker_child_specs(
        base_env={"PERAGO_WORKER_ID_PREFIX": "prodAFeaturesBuild"},
        module_target="app.workers.features_build",
        process_count=2,
    )

    assert [spec.worker_id for spec in specs] == [
        "prodAFeaturesBuild0001",
        "prodAFeaturesBuild0002",
    ]


def test_worker_child_specs_reject_invalid_process_count() -> None:
    with pytest.raises(RuntimeConfigError, match="at least 1"):
        worker_child_specs(base_env={}, module_target="app.workers.features_build", process_count=0)
