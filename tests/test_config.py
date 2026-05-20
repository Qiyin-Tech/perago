from datetime import timedelta

import pytest
from pydantic import BaseModel, ValidationError

from perago.config import (
    RuntimeConfig,
    child_environment,
    load_runtime_config,
    load_runtime_env,
    parse_log_file_max_size,
    parse_log_retention,
    read_dotenv,
    resolve_worker_id,
)
from perago.errors import RuntimeConfigError


def test_read_dotenv_and_process_env_precedence(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "# local development",
                "export PERAGO_LOG_FILE_MAX_SIZE=512KB",
                "PERAGO_WORKSPACE_ROOT='/tmp/from-dotenv'",
                'PERAGO_LOG_ROOT="/tmp/logs-from-dotenv"',
                "PERAGO_WORKER_ID_PREFIX=dotenvPrefix",
                "IGNORED_LINE",
            ]
        ),
        encoding="utf-8",
    )

    env = load_runtime_env(
        {"PERAGO_WORKER_ID_PREFIX": "processPrefix"},
        read_dotenv(dotenv),
    )

    assert env["PERAGO_WORKSPACE_ROOT"] == "/tmp/from-dotenv"
    assert env["PERAGO_LOG_ROOT"] == "/tmp/logs-from-dotenv"
    assert env["PERAGO_LOG_FILE_MAX_SIZE"] == "512KB"
    assert env["PERAGO_WORKER_ID_PREFIX"] == "processPrefix"


def test_load_runtime_config_reads_dotenv_without_probing(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PERAGO_WORKSPACE_ROOT={tmp_path / 'workspaces'}",
                f"PERAGO_LOG_ROOT={tmp_path / 'logs'}",
                "PERAGO_LOG_FILE_MAX_SIZE=1.5MB",
                "PERAGO_LOG_RETENTION=7d",
                "PERAGO_WORKER_ID_PREFIX=dotenvPrefix",
            ]
        ),
        encoding="utf-8",
    )

    config = load_runtime_config(
        "app.workers.features_build",
        cwd=tmp_path,
        process_env={},
        probe_roots=False,
    )

    assert config.workspace_root == tmp_path / "workspaces"
    assert config.log_root == tmp_path / "logs"
    assert config.log_file_max_size == 1_572_864
    assert config.log_retention == timedelta(days=7)
    assert config.worker_id_prefix == "dotenvPrefix"


def test_load_runtime_config_empty_process_env_does_not_read_os_environ(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERAGO_WORKER_ID_PREFIX", "processPrefix")
    (tmp_path / ".env").write_text(
        "PERAGO_WORKER_ID_PREFIX=dotenvPrefix",
        encoding="utf-8",
    )

    config = load_runtime_config(
        "app.workers.features_build",
        cwd=tmp_path,
        process_env={},
        probe_roots=False,
    )

    assert config.worker_id_prefix == "dotenvPrefix"


def test_runtime_config_is_frozen_pydantic_model(tmp_path) -> None:
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="worker",
    )

    assert isinstance(config, BaseModel)
    with pytest.raises(ValidationError):
        config.worker_id_prefix = "other"


def test_parse_log_file_max_size() -> None:
    assert parse_log_file_max_size(None) == 100 * 1024 * 1024
    assert parse_log_file_max_size("512KB") == 512 * 1024
    assert parse_log_file_max_size("1.5 mb") == 1_572_864

    with pytest.raises(RuntimeConfigError, match="PERAGO_LOG_FILE_MAX_SIZE"):
        parse_log_file_max_size("100")
    with pytest.raises(RuntimeConfigError, match="greater than zero"):
        parse_log_file_max_size("0MB")


def test_parse_log_retention() -> None:
    assert parse_log_retention(None) == timedelta(days=30)
    assert parse_log_retention("7D") == timedelta(days=7)

    with pytest.raises(RuntimeConfigError, match="PERAGO_LOG_RETENTION"):
        parse_log_retention("0d")


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
