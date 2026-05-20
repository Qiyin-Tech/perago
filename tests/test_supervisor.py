from datetime import timedelta

import pytest

from perago import RuntimeConfig, RuntimeConfigError, restart_backoff_seconds, worker_child_specs
from perago.supervisor import _stop_worker_processes, run_worker_supervisor


class FakeProcess:
    def __init__(self) -> None:
        self.alive = True
        self.events: list[tuple[str, int | None]] = []

    def join(self, timeout: int) -> None:
        self.events.append(("join", timeout))

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.events.append(("terminate", None))

    def kill(self) -> None:
        self.events.append(("kill", None))
        self.alive = False


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


def test_run_worker_supervisor_rejects_unimplemented_thread_mode(tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
    )

    with pytest.raises(RuntimeConfigError, match="thread execution mode"):
        run_worker_supervisor(
            config=config,
            module_target="app.workers.features_build",
            process_count=1,
            execution_mode="thread",
        )


def test_stop_worker_processes_escalates_after_grace_periods() -> None:
    process = FakeProcess()

    _stop_worker_processes([process])  # type: ignore[list-item]

    assert process.events == [
        ("join", 10),
        ("terminate", None),
        ("join", 5),
        ("kill", None),
        ("join", 5),
    ]
