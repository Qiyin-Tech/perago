"""Verify failure metadata at native execution, real process IPC and SDK serialization boundaries."""

from __future__ import annotations

import json
import multiprocessing
from dataclasses import replace
from pathlib import Path

import pytest
from conductor.client.configuration.configuration import Configuration
from conductor.client.http.api_client import ApiClient
from perago import TaskTerminalError, WorkspaceSpec
from perago.conductor_runtime import (
    ConductorTaskAttempt, ProcessTaskAssignment, ProcessTaskCompletion, StopProcessExecutor,
    run_process_executor_loop, runtime_result_to_sdk_task_result,
)
from perago.execution import run_workspace_free_task_attempt, run_workspace_task_attempt
from perago.task import load_module_task


def _attempt(terminal: bool) -> ConductorTaskAttempt:
    """Build one native attempt identity as a Conductor poll would supply it."""

    return ConductorTaskAttempt(workflow_instance_id="workflow-fixture", task_id="task-fixture", retry_count=0,
                                task_def_name="tests.structuredFailure", reference_task_name="fixture", seq=1,
                                iteration=0, status="IN_PROGRESS", input_data={"params": {"terminal": terminal}})


def _executor(connection) -> None:
    """Run the original Perago executor in a spawned child without custom supervision."""

    try:
        run_process_executor_loop(task=load_module_task("structured_failure"), worker_id="fixture0001",
                                  workspace_root=Path("unused"), connection=connection,
                                  failure_reason_max_length=500, load_current_attempt=lambda attempt: attempt)
    finally:
        connection.close()


@pytest.mark.parametrize("terminal", [False, True])
def test_failure_output_crosses_native_process_ipc_and_sdk_json(terminal) -> None:
    """Assert the actual child result and the SDK's HTTP serialization agree on failure state and metadata."""

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_executor, args=(child,))
    process.start()
    child.close()
    try:
        attempt = _attempt(terminal)
        parent.send(ProcessTaskAssignment(attempt=attempt, execution_id="execution-fixture"))
        assert parent.poll(15), "native executor did not report its failure"
        completion = parent.recv()
        assert isinstance(completion, ProcessTaskCompletion)
        sdk_result = runtime_result_to_sdk_task_result(attempt, completion.result, worker_id="fixture0001")
        payload = ApiClient(Configuration()).sanitize_for_serialization(sdk_result)
        assert payload["status"] == ("FAILED_WITH_TERMINAL_ERROR" if terminal else "FAILED")
        assert payload["reasonForIncompletion"] == "provider unavailable"
        assert payload["outputData"] == completion.result.output
        assert payload["outputData"]["error"]["details"]["providerTaskId"] == "original-upstream-task"
        assert "result" not in payload["outputData"]
        json.dumps(payload, allow_nan=False)
        parent.send(StopProcessExecutor())
        process.join(10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join()
        parent.close()
        process.close()


def test_failure_output_survives_workspace_free_execution() -> None:
    """Failure metadata bypasses the successful return model but remains a terminal task failure."""

    result = run_workspace_free_task_attempt(load_module_task("structured_failure"), {"params": {"terminal": True}},
                                             failure_reason_max_length=500)
    assert result.status == "FAILED_WITH_TERMINAL_ERROR", result.reason_for_incompletion
    assert result.output["error"]["code"] == "SERVICE_FAILURE"


def test_workspace_failure_with_metadata_does_not_stage_or_publish(tmp_path) -> None:
    """A failed workspace attempt still cleans up before returning metadata, without publishing data."""

    original = load_module_task("structured_failure")
    calls = []

    def fail(workspace, params):
        """Write local work, then explicitly fail with diagnostics."""

        (workspace / "partial.txt").write_text("partial")
        raise TaskTerminalError("failed", output_data={"error": {"code": "UPSTREAM_REJECTED"}})

    def download(input_workspace, specification, directory):
        """Record download without making a LakeFS request."""

        calls.append("download")
        directory.mkdir(parents=True, exist_ok=True)

    def unexpected(*args, **kwargs):
        """Fail immediately if the failed body enters any publication path."""

        pytest.fail("failed task attempted workspace publication")

    task = replace(original, fn=fail, workspace=WorkspaceSpec(prefix="/"))
    attempt = _attempt(True)
    inputs = {**attempt.input_data, "workspace": {"repository": "fixture", "branch": "main", "refType": "commit", "ref": "a" * 64}}
    result = run_workspace_task_attempt(task, inputs, attempt, tmp_path, download_workspace=download,
                                        load_current_attempt=lambda current: current, stage_workspace=unexpected,
                                        publish_workspace=unexpected, cleanup_staging=unexpected,
                                        failure_reason_max_length=500)
    assert result.status == "FAILED_WITH_TERMINAL_ERROR", result.reason_for_incompletion
    assert result.output == {"error": {"code": "UPSTREAM_REJECTED"}}
    assert calls == ["download"]
    assert not list(tmp_path.rglob("partial.txt"))
