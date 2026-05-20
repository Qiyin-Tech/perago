import pathlib

from typer.testing import CliRunner

from perago.cli import app


def test_check_cli_reports_task(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.metadata_validate"])

    assert result.exit_code == 0
    assert "ok: metadata.validate" in result.output
    assert "worker_id_prefix: appworkersmetadatavalidate" in result.output
    assert "conductor: not configured" in result.output
    assert "lakefs: not configured" in result.output


def test_check_cli_reports_connection_config_status_without_secrets(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CONDUCTOR_SERVER_URL=http://conductor.local/api",
                "LAKECTL_SERVER_ENDPOINT_URL=http://lakefs.local",
                "LAKECTL_CREDENTIALS_ACCESS_KEY_ID=lakefs-key",
                "LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY=lakefs-secret",
            ]
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.metadata_validate"])

    assert result.exit_code == 0
    assert "conductor: configured" in result.output
    assert "lakefs: configured" in result.output
    assert "lakefs-secret" not in result.output


def test_check_cli_rejects_invalid_worker_prefix(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERAGO_WORKER_ID_PREFIX", "bad-prefix")
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.metadata_validate"])

    assert result.exit_code == 1
    assert "PERAGO_WORKER_ID_PREFIX" in result.output


def test_check_cli_reports_pydantic_task_definition_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_workspace_prefix"])

    assert result.exit_code == 1
    assert "WorkspaceSpec.prefix must stay inside the repository" in result.output


def test_extract_cli_rejects_path_like_task_names(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["extract", "app.workers.bad_task_name_path", "--output", str(tmp_path / "generated" / "bad.name.json")],
    )

    assert result.exit_code == 1
    assert "task name must not contain path separators" in result.output
    assert not (tmp_path / "generated" / "bad.name.json").exists()


def test_check_cli_reads_runtime_config_before_importing_task(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERAGO_WORKER_ID_PREFIX", "bad-prefix")
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_workspace_prefix"])

    assert result.exit_code == 1
    assert "PERAGO_WORKER_ID_PREFIX" in result.output
    assert "WorkspaceSpec.prefix" not in result.output


def test_check_cli_reports_absolute_guardrail_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_guardrail_absolute"])

    assert result.exit_code == 1
    assert "guardrail paths must be relative to WorkspaceSpec" in result.output


def test_check_cli_reports_task_controls_validation_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_controls"])

    assert result.exit_code == 1
    assert "rate_limit_frequency_in_seconds and rate_limit_per_frequency" in result.output


def test_check_cli_rejects_unknown_task_control_fields(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_control_extra"])

    assert result.exit_code == 1
    assert "retry_count" in result.output
    assert "Extra inputs are not permitted" in result.output


def test_check_cli_reports_schema_generation_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_schema"])

    assert result.exit_code == 1
    assert "Cannot generate a JsonSchema" in result.output


def test_check_cli_reports_async_task_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_async_task"])

    assert result.exit_code == 1
    assert "task function must be a synchronous function" in result.output


def test_check_cli_reports_task_parameter_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_default_param"])

    assert result.exit_code == 1
    assert "task function parameters must not declare defaults" in result.output


def test_check_cli_reports_unsupported_task_signature_kinds(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_variadic_signature"])

    assert result.exit_code == 1
    assert "task function must not use" in result.output

    result = runner.invoke(app, ["check", "app.workers.bad_keyword_only_signature"])

    assert result.exit_code == 1
    assert "task function must not use" in result.output


def test_check_cli_reports_missing_task_contract_annotations(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_missing_params_annotation"])

    assert result.exit_code == 1
    assert "params must be annotated" in result.output

    result = runner.invoke(app, ["check", "app.workers.bad_missing_return_annotation"])

    assert result.exit_code == 1
    assert "return value must be annotated" in result.output


def test_check_cli_reports_bad_decorator_option_types(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app.workers.bad_decorator_types"])

    assert result.exit_code == 1
    assert "controls must be a TaskControls" in result.output


def test_check_cli_rejects_non_module_target(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["check", "app/workers/features_build.py"])

    assert result.exit_code == 1
    assert "module target must be a Python import path" in result.output


def test_extract_cli_writes_taskdef(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["extract", "app.workers.metadata_validate", "--output", str(tmp_path / "generated" / "metadata.validate.json")],
    )

    assert result.exit_code == 0
    assert (tmp_path / "generated" / "metadata.validate.json").exists()
    generated_files = sorted(path.relative_to(tmp_path / "generated") for path in (tmp_path / "generated").rglob("*") if path.is_file())
    assert generated_files == [pathlib.Path("metadata.validate.json")]


def test_extract_cli_accepts_short_output_option(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["extract", "app.workers.metadata_validate", "-o", str(tmp_path / "generated" / "taskdef.json")],
    )

    assert result.exit_code == 0
    assert (tmp_path / "generated" / "taskdef.json").exists()


def test_extract_cli_rejects_directory_like_output(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["extract", "app.workers.metadata_validate", "-o", str(tmp_path / "generated")],
    )

    assert result.exit_code == 1
    assert "output must be a JSON file path" in result.output


def test_start_cli_starts_supervisor_after_taskdef_check(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERAGO_WORKER_ID_PREFIX", "prodAFeaturesBuild")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CONDUCTOR_SERVER_URL=http://conductor.local/api",
                "LAKECTL_SERVER_ENDPOINT_URL=http://lakefs.local",
                "LAKECTL_CREDENTIALS_ACCESS_KEY_ID=lakefs-key",
                "LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY=lakefs-secret",
            ]
        ),
        encoding="utf-8",
    )
    started: dict[str, object] = {}

    class FakeConductor:
        def taskdef_exists(self, task_name: str) -> bool:
            return task_name == "features.build"

    monkeypatch.setattr("perago.cli.OrkesConductorRuntimeClient.from_config", lambda config: FakeConductor())
    monkeypatch.setattr(
        "perago.cli.run_worker_supervisor",
        lambda **kwargs: started.update(kwargs),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["start", "app.workers.features_build", "-j", "2"])

    assert result.exit_code == 0
    assert started["module_target"] == "app.workers.features_build"
    assert started["process_count"] == 2
    assert started["execution_mode"] == "process"


def test_start_cli_rejects_unimplemented_thread_mode_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "PERAGO_EXECUTION_MODE=thread",
                "CONDUCTOR_SERVER_URL=http://conductor.local/api",
                "LAKECTL_SERVER_ENDPOINT_URL=http://lakefs.local",
                "LAKECTL_CREDENTIALS_ACCESS_KEY_ID=lakefs-key",
                "LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY=lakefs-secret",
            ]
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(app, ["start", "app.workers.features_build"])

    assert result.exit_code == 1
    assert "thread execution mode is not implemented yet" in result.output


def test_start_cli_option_overrides_execution_mode_env(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "PERAGO_EXECUTION_MODE=thread",
                "CONDUCTOR_SERVER_URL=http://conductor.local/api",
                "LAKECTL_SERVER_ENDPOINT_URL=http://lakefs.local",
                "LAKECTL_CREDENTIALS_ACCESS_KEY_ID=lakefs-key",
                "LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY=lakefs-secret",
            ]
        ),
        encoding="utf-8",
    )
    started: dict[str, object] = {}

    class FakeConductor:
        def taskdef_exists(self, task_name: str) -> bool:
            return task_name == "features.build"

    monkeypatch.setattr("perago.cli.OrkesConductorRuntimeClient.from_config", lambda config: FakeConductor())
    monkeypatch.setattr("perago.cli.run_worker_supervisor", lambda **kwargs: started.update(kwargs))
    runner = CliRunner()

    result = runner.invoke(app, ["start", "app.workers.features_build", "--execution-mode", "process"])

    assert result.exit_code == 0
    assert started["execution_mode"] == "process"


def test_start_cli_fails_when_taskdef_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CONDUCTOR_SERVER_URL=http://conductor.local/api",
                "LAKECTL_SERVER_ENDPOINT_URL=http://lakefs.local",
                "LAKECTL_CREDENTIALS_ACCESS_KEY_ID=lakefs-key",
                "LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY=lakefs-secret",
            ]
        ),
        encoding="utf-8",
    )

    class FakeConductor:
        def taskdef_exists(self, task_name: str) -> bool:
            del task_name
            return False

    monkeypatch.setattr("perago.cli.OrkesConductorRuntimeClient.from_config", lambda config: FakeConductor())
    runner = CliRunner()

    result = runner.invoke(app, ["start", "app.workers.features_build"])

    assert result.exit_code == 1
    assert "is not registered" in result.output
    assert "perago extract" in result.output


def test_start_cli_requires_runtime_service_config_before_importing_task(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERAGO_WORKER_ID_PREFIX", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["start", "app.workers.bad_schema"])

    assert result.exit_code == 1
    assert "CONDUCTOR_SERVER_URL is required" in result.output
    assert "Cannot generate a JsonSchema" not in result.output
