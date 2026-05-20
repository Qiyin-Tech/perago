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
        ["extract", "app.workers.metadata_validate", "--out", str(tmp_path / "generated")],
    )

    assert result.exit_code == 0
    assert (tmp_path / "generated" / "taskdefs" / "metadata.validate.json").exists()


def test_start_cli_reports_planned_worker_ids_without_starting_services(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERAGO_WORKER_ID_PREFIX", "prodAFeaturesBuild")
    runner = CliRunner()

    result = runner.invoke(app, ["start", "app.workers.features_build", "-j", "2"])

    assert result.exit_code == 1
    assert "reserved for the Conductor/LakeFS worker integration phase" in result.output
    assert "worker_processes=2" in result.output
    assert "worker_ids=prodAFeaturesBuild0001,prodAFeaturesBuild0002" in result.output
