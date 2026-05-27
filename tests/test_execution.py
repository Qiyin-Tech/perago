from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError

from perago import (
    PostGuardrailViolation,
    PreGuardrailViolation,
    StagedWorkspace,
    TaskFailed,
    TaskInputError,
    TaskTerminalError,
    WorkspaceSpec,
    build_workspace_free_task_output,
    build_workspace_task_output,
    invoke_workspace_free_task,
    invoke_workspace_task_body,
    load_module_task,
    require_dir,
    run_workspace_free_task_attempt,
    run_workspace_task_attempt,
    task,
)
from perago.workspace import attempt_workspace_dir
from perago.workspace import active_workspace_owner_tokens


class Params(BaseModel):
    value: int = Field(ge=1)


class Output(BaseModel):
    value: int


class NestedParams(BaseModel):
    settings: Params


class NestedOutput(BaseModel):
    value: int


class StatusOutput(BaseModel):
    status: str


@dataclass(frozen=True)
class Attempt:
    status: str = "IN_PROGRESS"
    workflow_instance_id: str = "wf-7f3d"
    task_def_name: str = "features.build"
    task_id: str = "9b4c"
    retry_count: int = 2
    execution_id: str = "exec-1"


@task(
    name="tests.post_failure",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(post=[require_dir("missing")]),
)
def post_failure_task(workspace: Path, params: Params) -> Output:
    return Output(value=params.value)


@task(name="tests.nested_params", owner_email="data@example.com")
def nested_params_task(params: NestedParams) -> NestedOutput:
    return NestedOutput(value=params.settings.value)


@task(
    name="tests.read_only_workspace",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(read_only=True),
)
def read_only_workspace_task(workspace: Path, params: Params) -> Output:
    (workspace / "scratch.txt").write_text("discarded", encoding="utf-8")
    return Output(value=params.value)


@task(
    name="tests.same_content_workspace",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(),
)
def same_content_workspace_task(workspace: Path, params: Params) -> Output:
    (workspace / "raw").mkdir(exist_ok=True)
    (workspace / "raw" / "input.parquet").write_text("ok", encoding="utf-8")
    return Output(value=params.value)


@task(name="tests.workspace_free_failed", owner_email="data@example.com")
def workspace_free_failed_task(params: Params) -> Output:
    raise TaskFailed(f"retry value {params.value}")


@task(name="tests.workspace_free_terminal", owner_email="data@example.com")
def workspace_free_terminal_task(params: Params) -> Output:
    raise TaskTerminalError(f"terminal value {params.value}")


@task(name="tests.business_rejected", owner_email="data@example.com")
def business_rejected_task(params: Params) -> StatusOutput:
    del params
    return StatusOutput(status="REJECTED")


@task(name="tests.workspace_failed_after_write", owner_email="data@example.com", workspace=WorkspaceSpec())
def workspace_failed_after_write_task(workspace: Path, params: Params) -> Output:
    (workspace / "changed.txt").write_text(str(params.value), encoding="utf-8")
    raise TaskFailed("workspace retryable failure")


@task(name="tests.workspace_terminal_after_write", owner_email="data@example.com", workspace=WorkspaceSpec())
def workspace_terminal_after_write_task(workspace: Path, params: Params) -> Output:
    (workspace / "changed.txt").write_text(str(params.value), encoding="utf-8")
    raise TaskTerminalError("workspace terminal failure")


WORKSPACE_INPUT = {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3",
}


def _attempt() -> Attempt:
    return Attempt()


def test_invokes_workspace_task_body_with_guardrails(tmp_path) -> None:
    task_def = load_module_task("app.workers.features_build")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "input.parquet").write_text("ok", encoding="utf-8")

    output = invoke_workspace_task_body(
        task_def,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        tmp_path,
    )

    assert output == {"result": {"row_count": 100, "feature_count": 24}}
    assert (tmp_path / "features" / "default.parquet").is_file()


def test_workspace_task_body_classifies_pre_guardrail_failure(tmp_path) -> None:
    task_def = load_module_task("app.workers.features_build")

    with pytest.raises(PreGuardrailViolation, match="pre guardrail"):
        invoke_workspace_task_body(
            task_def,
            {
                "workspace": WORKSPACE_INPUT,
                "params": {"feature_set": "default", "min_rows": 100},
            },
            tmp_path,
        )

    assert not (tmp_path / "features").exists()


def test_workspace_task_body_classifies_post_guardrail_failure(tmp_path) -> None:
    task_def = post_failure_task.__perago_task__

    with pytest.raises(PostGuardrailViolation, match="post guardrail"):
        invoke_workspace_task_body(
            task_def,
            {
                "workspace": WORKSPACE_INPUT,
                "params": {"value": 1},
            },
            tmp_path,
        )


def test_run_workspace_task_attempt_publishes_completed_output_and_cleans(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()
    calls: list[str] = []

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        calls.append(f"download:{workspace_input.ref}:{workspace_spec.prefix}")
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    def stage_workspace(workspace_dir, workspace_input, workspace_spec, attempt) -> StagedWorkspace:
        del attempt
        calls.append(f"stage:{workspace_input.branch}:{workspace_spec.prefix}")
        assert (workspace_dir / "features" / "default.parquet").is_file()
        return StagedWorkspace(
            repository=workspace_input.repository,
            branch="perago/staging/wf/build",
            commit="staging-commit",
        )

    def publish_workspace(staged, workspace_input, workspace_spec, attempt) -> str:
        del workspace_input, workspace_spec, attempt
        calls.append(f"publish:{staged.branch}:{staged.commit}")
        return "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca"

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=stage_workspace,
        publish_workspace=publish_workspace,
        cleanup_staging=lambda staged: calls.append(f"cleanup:{staged.repository}:{staged.branch}"),
        owner_worker_id="featuresBuild0001",
    )

    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {
            "workspace": {
                "repository": "song-000123",
                "branch": "main",
                "ref_type": "commit",
                "ref": "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca",
            },
            "result": {"row_count": 100, "feature_count": 24},
        },
    }
    assert calls == [
        "download:589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3:audio/render",
        "stage:main:audio/render",
        "publish:perago/staging/wf/build:staging-commit",
        "cleanup:song-000123:perago/staging/wf/build",
    ]
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_read_only_skips_fences_and_publication(tmp_path) -> None:
    task = read_only_workspace_task.__perago_task__
    attempt = _attempt()

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"value": 7},
        },
        attempt,
        tmp_path,
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        load_current_attempt=lambda current_attempt: pytest.fail(
            "read-only workspace task must not check the attempt fence"
        ),
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: pytest.fail(
            "read-only workspace task must not stage"
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: pytest.fail(
            "read-only workspace task must not publish"
        ),
        cleanup_staging=lambda staged: None,
        complete_noop_workspace=lambda workspace_input, workspace_spec, attempt: pytest.fail(
            "read-only workspace task must not run no-op reconciliation"
        ),
        owner_worker_id="metadataInspect0001",
    )

    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {
            "workspace": WORKSPACE_INPUT,
            "result": {"value": 7},
        },
    }
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_rejects_workspace_free_task_before_preparing_workspace(tmp_path) -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(TaskInputError, match="only supports workspace tasks"):
        run_workspace_task_attempt(
            task,
            {"params": {"song_id": "song-000123", "min_duration_seconds": 30}},
            _attempt(),
            tmp_path,
            download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
            load_current_attempt=lambda current_attempt: current_attempt,
            stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
                repository="unused",
                branch="unused",
                commit="unused",
            ),
            publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
            cleanup_staging=lambda staged: None,
        )


def test_run_workspace_task_attempt_rejects_missing_workspace_spec(tmp_path) -> None:
    task = type("BrokenWorkspaceTask", (), {"has_workspace": True, "workspace": None})()

    with pytest.raises(TaskInputError, match="missing WorkspaceSpec"):
        run_workspace_task_attempt(
            task,
            {"workspace": WORKSPACE_INPUT, "params": {"value": 1}},
            _attempt(),
            tmp_path,
            download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
            load_current_attempt=lambda current_attempt: current_attempt,
            stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
                repository="unused",
                branch="unused",
                commit="unused",
            ),
            publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
            cleanup_staging=lambda staged: None,
        )


def test_run_workspace_task_attempt_completes_writable_noop_without_staging(tmp_path) -> None:
    task = same_content_workspace_task.__perago_task__
    attempt = _attempt()
    calls: list[str] = []

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"value": 9},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: calls.append("fence") or current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: pytest.fail(
            "writable no-op task must not stage"
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: pytest.fail(
            "writable no-op task must not publish"
        ),
        cleanup_staging=lambda staged: calls.append("cleanup"),
        complete_noop_workspace=lambda workspace_input, workspace_spec, attempt: calls.append("noop")
        or workspace_input.ref,
        owner_worker_id="featuresBuild0001",
    )

    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {
            "workspace": WORKSPACE_INPUT,
            "result": {"value": 9},
        },
    }
    assert calls == ["fence", "noop"]
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_checks_attempt_fence_before_writable_noop(tmp_path) -> None:
    task = same_content_workspace_task.__perago_task__
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"value": 9},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: Attempt(status="COMPLETED"),
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: pytest.fail(
            "stale writable no-op attempt must not stage"
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
        complete_noop_workspace=lambda workspace_input, workspace_spec, attempt: pytest.fail(
            "stale writable no-op attempt must not reconcile target branch"
        ),
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "9b4c"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_registers_owner_token_during_attempt(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()
    observed_tokens: list[set[str]] = []

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        observed_tokens.append(active_workspace_owner_tokens())
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            repository=workspace_input.repository,
            branch="perago/staging/wf/build",
            commit="staging-commit",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: (
            "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca"
        ),
        cleanup_staging=lambda staged: None,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "COMPLETED"
    assert len(observed_tokens) == 1
    assert len(observed_tokens[0]) == 1
    assert active_workspace_owner_tokens().isdisjoint(observed_tokens[0])


def test_run_workspace_task_attempt_classifies_pre_guardrail_failure_and_cleans(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec, workspace_dir

    def stage_workspace(workspace_dir, workspace_input, workspace_spec, attempt) -> StagedWorkspace:
        raise AssertionError("pre guardrail failure must not stage")

    def publish_workspace(staged, workspace_input, workspace_spec, attempt) -> str:
        raise AssertionError("pre guardrail failure must not publish")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=stage_workspace,
        publish_workspace=publish_workspace,
        cleanup_staging=lambda staged: None,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED_WITH_TERMINAL_ERROR"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_maps_task_failed_without_publishing(tmp_path) -> None:
    task = workspace_failed_after_write_task.__perago_task__
    calls: list[str] = []

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"value": 5},
        },
        _attempt(),
        tmp_path,
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        load_current_attempt=lambda current_attempt: calls.append("fence") or current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: pytest.fail(
            "failed task must not stage"
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: pytest.fail(
            "failed task must not publish"
        ),
        cleanup_staging=lambda staged: calls.append("cleanup"),
        owner_worker_id="featuresBuild0001",
    )

    assert result.conductor_payload() == {
        "status": "FAILED",
        "reasonForIncompletion": "workspace retryable failure",
    }
    assert calls == []
    assert not attempt_workspace_dir(tmp_path, _attempt()).exists()


def test_run_workspace_task_attempt_maps_task_terminal_error_without_publishing(tmp_path) -> None:
    task = workspace_terminal_after_write_task.__perago_task__
    calls: list[str] = []

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"value": 5},
        },
        _attempt(),
        tmp_path,
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        load_current_attempt=lambda current_attempt: calls.append("fence") or current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: pytest.fail(
            "terminal task failure must not stage"
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: pytest.fail(
            "terminal task failure must not publish"
        ),
        cleanup_staging=lambda staged: calls.append("cleanup"),
        owner_worker_id="featuresBuild0001",
    )

    assert result.conductor_payload() == {
        "status": "FAILED_WITH_TERMINAL_ERROR",
        "reasonForIncompletion": "workspace terminal failure",
    }
    assert calls == []
    assert not attempt_workspace_dir(tmp_path, _attempt()).exists()


def test_run_workspace_task_attempt_checks_attempt_fence_before_publish(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    def stage_workspace(workspace_dir, workspace_input, workspace_spec, attempt) -> StagedWorkspace:
        raise AssertionError("stale attempts must not publish")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: Attempt(status="COMPLETED"),
        stage_workspace=stage_workspace,
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "9b4c"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_cleans_staging_when_second_attempt_fence_fails(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()
    fresh_attempts = iter([attempt, Attempt(status="COMPLETED")])
    calls: list[str] = []

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: next(fresh_attempts),
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            repository=workspace_input.repository,
            branch="perago/staging/wf/build",
            commit="staging-commit",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: calls.append("publish") or "unused",
        cleanup_staging=lambda staged: calls.append(f"cleanup:{staged.repository}:{staged.branch}"),
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "9b4c"
    assert calls == ["cleanup:song-000123:perago/staging/wf/build"]
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_preserves_result_when_staging_cleanup_fails(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    def cleanup_staging(staged) -> None:
        raise RuntimeError(f"delete failed: {staged.branch}")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            repository=workspace_input.repository,
            branch="perago/staging/wf/build",
            commit="staging-commit",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: (
            "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca"
        ),
        cleanup_staging=cleanup_staging,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "COMPLETED"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_preserves_failure_when_staging_cleanup_fails(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"feature_set": "default", "min_rows": 100},
        },
        attempt,
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            repository=workspace_input.repository,
            branch="perago/staging/wf/build",
            commit="staging-commit",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: (_ for _ in ()).throw(
            RuntimeError("publish failed")
        ),
        cleanup_staging=lambda staged: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED"
    assert result.reason_for_incompletion == "publish failed"
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_task_attempt_returns_failed_result_for_bad_input(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    attempt = _attempt()

    result = run_workspace_task_attempt(
        task,
        {"params": {"feature_set": "default", "min_rows": 100}},
        attempt,
        tmp_path,
        download_workspace=lambda workspace_input, workspace_spec, workspace_dir: None,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: StagedWorkspace(
            repository="unused",
            branch="unused",
            commit="unused",
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
        owner_worker_id="featuresBuild0001",
    )

    assert result.status == "FAILED"
    assert "workspace task input" in result.reason_for_incompletion
    assert not attempt_workspace_dir(tmp_path, attempt).exists()


def test_run_workspace_free_task_attempt_returns_completed_result() -> None:
    task = load_module_task("app.workers.metadata_validate")

    result = run_workspace_free_task_attempt(
        task,
        {
            "params": {
                "song_id": "song-000123",
                "min_duration_seconds": 30,
            },
        },
    )

    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {"result": {"valid": True, "reason": None}},
    }


def test_run_workspace_free_task_attempt_maps_task_failed() -> None:
    result = run_workspace_free_task_attempt(
        workspace_free_failed_task.__perago_task__,
        {"params": {"value": 3}},
    )

    assert result.conductor_payload() == {
        "status": "FAILED",
        "reasonForIncompletion": "retry value 3",
    }


def test_run_workspace_free_task_attempt_maps_task_terminal_error() -> None:
    result = run_workspace_free_task_attempt(
        workspace_free_terminal_task.__perago_task__,
        {"params": {"value": 4}},
    )

    assert result.conductor_payload() == {
        "status": "FAILED_WITH_TERMINAL_ERROR",
        "reasonForIncompletion": "terminal value 4",
    }


def test_workspace_free_business_rejection_remains_completed() -> None:
    result = run_workspace_free_task_attempt(
        business_rejected_task.__perago_task__,
        {"params": {"value": 1}},
    )

    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {"result": {"status": "REJECTED"}},
    }


def test_run_workspace_free_task_attempt_rejects_workspace_task() -> None:
    task = load_module_task("app.workers.features_build")

    with pytest.raises(TaskInputError, match="workspace-free tasks"):
        run_workspace_free_task_attempt(task, {"params": {"feature_set": "default", "min_rows": 1}})


def test_run_workspace_free_task_attempt_returns_failed_result_for_bad_input() -> None:
    task = load_module_task("app.workers.metadata_validate")

    result = run_workspace_free_task_attempt(
        task,
        {
            "song_id": "song-000123",
            "min_duration_seconds": 30,
        },
    )

    assert result.status == "FAILED"
    assert "workspace-free task input" in result.reason_for_incompletion


def test_invokes_workspace_free_task_from_wrapped_params() -> None:
    task = load_module_task("app.workers.metadata_validate")

    output = invoke_workspace_free_task(
        task,
        {
            "params": {
                "song_id": "song-000123",
                "min_duration_seconds": 30,
            },
        },
    )

    assert output == {"result": {"valid": True, "reason": None}}


def test_builds_workspace_task_output_with_published_ref() -> None:
    task = load_module_task("app.workers.features_build")

    output = build_workspace_task_output(
        task,
        WORKSPACE_INPUT,
        "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca",
        {"row_count": 100, "feature_count": 24},
    )

    assert output == {
        "workspace": {
            "repository": "song-000123",
            "branch": "main",
            "ref_type": "commit",
            "ref": "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca",
        },
        "result": {"row_count": 100, "feature_count": 24},
    }


def test_build_workspace_task_output_rejects_workspace_free_task() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(TaskInputError, match="workspace tasks"):
        build_workspace_task_output(
            task,
            WORKSPACE_INPUT,
            "published-ref",
            {"valid": True, "reason": None},
        )


def test_builds_workspace_free_task_output() -> None:
    task = load_module_task("app.workers.metadata_validate")

    output = build_workspace_free_task_output(task, {"valid": False, "reason": "missing"})

    assert output == {"result": {"valid": False, "reason": "missing"}}


def test_build_workspace_free_task_output_rejects_workspace_task() -> None:
    task = load_module_task("app.workers.features_build")

    with pytest.raises(TaskInputError, match="workspace-free output"):
        build_workspace_free_task_output(task, {"row_count": 1, "feature_count": 1})


def test_workspace_free_invocation_rejects_expanded_top_level_params() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(TaskInputError, match="contain only params"):
        invoke_workspace_free_task(
            task,
            {
                "song_id": "song-000123",
                "min_duration_seconds": 30,
            },
        )


def test_workspace_free_invocation_validates_params_model() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(ValidationError):
        invoke_workspace_free_task(
            task,
            {
                "params": {
                    "song_id": "song-000123",
                    "min_duration_seconds": 0,
                },
            },
        )


def test_workspace_free_invocation_rejects_extra_business_params() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        invoke_workspace_free_task(
            task,
            {
                "params": {
                    "song_id": "song-000123",
                    "min_duration_seconds": 30,
                    "workspace": "not-a-workspace",
                },
            },
        )


def test_workspace_free_invocation_rejects_nested_extra_business_params() -> None:
    task = nested_params_task.__perago_task__

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        invoke_workspace_free_task(
            task,
            {
                "params": {
                    "settings": {
                        "value": 1,
                        "extra": "not-in-schema",
                    },
                },
            },
        )


def test_workspace_task_body_rejects_extra_business_params(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "input.parquet").write_text("ok", encoding="utf-8")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        invoke_workspace_task_body(
            task,
            {
                "workspace": WORKSPACE_INPUT,
                "params": {
                    "feature_set": "default",
                    "min_rows": 100,
                    "workspace": "not-a-workspace",
                },
            },
            tmp_path,
        )


def test_workspace_free_output_rejects_extra_business_result_fields() -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        build_workspace_free_task_output(task, {"valid": True, "extra": "ignored-by-default"})


def test_workspace_task_body_requires_ref_type(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")
    workspace_input = dict(WORKSPACE_INPUT)
    workspace_input.pop("ref_type")

    with pytest.raises(ValidationError):
        invoke_workspace_task_body(
            task,
            {
                "workspace": workspace_input,
                "params": {"feature_set": "default", "min_rows": 100},
            },
            tmp_path,
        )


def test_workspace_free_invocation_rejects_workspace_tasks() -> None:
    task = load_module_task("app.workers.features_build")

    with pytest.raises(TaskInputError, match="workspace-free"):
        invoke_workspace_free_task(task, {"params": {"feature_set": "default", "min_rows": 1}})


def test_workspace_task_body_rejects_workspace_free_task(tmp_path) -> None:
    task = load_module_task("app.workers.metadata_validate")

    with pytest.raises(TaskInputError, match="workspace tasks"):
        invoke_workspace_task_body(
            task,
            {"params": {"song_id": "song-000123", "min_duration_seconds": 30}},
            tmp_path,
        )


def test_workspace_task_body_rejects_invalid_wrapper_shape(tmp_path) -> None:
    task = load_module_task("app.workers.features_build")

    with pytest.raises(TaskInputError, match="workspace task input"):
        invoke_workspace_task_body(
            task,
            {
                "workspace": WORKSPACE_INPUT,
                "params": {"feature_set": "default", "min_rows": 100},
                "extra": "bad",
            },
            tmp_path,
        )


def test_workspace_task_body_rejects_missing_workspace_spec(tmp_path) -> None:
    task = type(
        "BrokenWorkspaceTask",
        (),
        {
            "has_workspace": True,
            "workspace": None,
            "params_model": Params,
        },
    )()

    with pytest.raises(TaskInputError, match="missing WorkspaceSpec"):
        invoke_workspace_task_body(
            task,
            {"workspace": WORKSPACE_INPUT, "params": {"value": 1}},
            tmp_path,
        )


def test_writable_noop_without_completion_callback_returns_failed_result(tmp_path) -> None:
    task = same_content_workspace_task.__perago_task__

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        raw = workspace_dir / "raw"
        raw.mkdir()
        (raw / "input.parquet").write_text("ok", encoding="utf-8")

    result = run_workspace_task_attempt(
        task,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"value": 9},
        },
        _attempt(),
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: pytest.fail(
            "no-op task must not stage"
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
    )

    assert result.status == "FAILED"
    assert "complete_noop_workspace callback is required" in result.reason_for_incompletion


def test_workspace_snapshot_tracks_symlink_targets_for_noop_detection(tmp_path) -> None:
    @task(name="tests.same_symlink_workspace", owner_email="data@example.com", workspace=WorkspaceSpec())
    def same_symlink_workspace_task(workspace: Path, params: Params) -> Output:
        assert (workspace / "latest").is_symlink()
        return Output(value=params.value)

    def download_workspace(workspace_input, workspace_spec, workspace_dir) -> None:
        del workspace_input, workspace_spec
        (workspace_dir / "target.txt").write_text("ok", encoding="utf-8")
        (workspace_dir / "latest").symlink_to("target.txt")

    result = run_workspace_task_attempt(
        same_symlink_workspace_task.__perago_task__,
        {
            "workspace": WORKSPACE_INPUT,
            "params": {"value": 3},
        },
        _attempt(),
        tmp_path,
        download_workspace=download_workspace,
        load_current_attempt=lambda current_attempt: current_attempt,
        stage_workspace=lambda workspace_dir, workspace_input, workspace_spec, attempt: pytest.fail(
            "unchanged symlink workspace must not stage"
        ),
        publish_workspace=lambda staged, workspace_input, workspace_spec, attempt: "unused",
        cleanup_staging=lambda staged: None,
        complete_noop_workspace=lambda workspace_input, workspace_spec, attempt: workspace_input.ref,
    )

    assert result.status == "COMPLETED"
