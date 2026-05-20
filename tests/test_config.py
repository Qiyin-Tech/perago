import pytest

from perago.config import child_environment, resolve_worker_id
from perago.errors import RuntimeConfigError


def test_child_environment_derives_worker_id_from_module_target() -> None:
    base_env = {"OTHER": "kept"}

    env = child_environment(base_env, "app.workers.features_build", 3)

    assert env["OTHER"] == "kept"
    assert env["PERAGO_WORKER_ID_PREFIX"] == "appworkersfeaturesbuild"
    assert env["PERAGO_WORKER_ID"] == "appworkersfeaturesbuild0003"
    assert "PERAGO_WORKER_ID" not in base_env


def test_child_environment_uses_configured_prefix() -> None:
    env = child_environment(
        {"PERAGO_WORKER_ID_PREFIX": "prodAFeaturesBuild"},
        "app.workers.features_build",
        2,
    )

    assert env["PERAGO_WORKER_ID_PREFIX"] == "prodAFeaturesBuild"
    assert env["PERAGO_WORKER_ID"] == "prodAFeaturesBuild0002"


def test_child_environment_rejects_invalid_configured_prefix() -> None:
    with pytest.raises(RuntimeConfigError, match="PERAGO_WORKER_ID_PREFIX"):
        child_environment({"PERAGO_WORKER_ID_PREFIX": "bad-prefix"}, "app.workers.features_build", 1)


def test_resolve_worker_id_prefers_configured_value() -> None:
    worker_id = resolve_worker_id(
        "app.workers.features_build",
        {"PERAGO_WORKER_ID": "manual-worker"},
    )

    assert worker_id == "manual-worker"


def test_resolve_worker_id_falls_back_to_module_target_and_pid(monkeypatch) -> None:
    monkeypatch.setattr("perago.config.os.getpid", lambda: 42118)

    worker_id = resolve_worker_id("app.workers.features_build", {})

    assert worker_id == "appworkersfeaturesbuild-pid-42118"
