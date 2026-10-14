from perago.config import DEFAULT_FAILURE_REASON_MAX_LENGTH
from perago.errors import (
    PostGuardrailViolation,
    PreGuardrailViolation,
    PublishFenceError,
    TaskFailed,
    TaskTerminalError,
)
from perago.result import (
    RuntimeTaskResult,
    completed_result,
    failed_result,
    result_for_exception,
    terminal_failed_result,
)
import pytest
from pydantic import ValidationError


def test_completed_result_payload_contains_output() -> None:
    result = completed_result({"result": {"valid": True}})

    assert result.status == "COMPLETED"
    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {"result": {"valid": True}},
    }


def test_failed_result_payload_contains_reason() -> None:
    result = failed_result("post failed", max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH)

    assert result.conductor_payload() == {
        "status": "FAILED",
        "reasonForIncompletion": "post failed",
    }


def test_terminal_failed_result_payload_contains_reason() -> None:
    result = terminal_failed_result("pre failed", max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH)

    assert result.conductor_payload() == {
        "status": "FAILED_WITH_TERMINAL_ERROR",
        "reasonForIncompletion": "pre failed",
    }


def test_result_for_exception_classifies_guardrail_failures_by_phase() -> None:
    assert (
        result_for_exception(
            PreGuardrailViolation("missing input"),
            max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
        ).status
        == "FAILED_WITH_TERMINAL_ERROR"
    )
    assert (
        result_for_exception(
            PostGuardrailViolation("missing output"),
            max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
        ).status
        == "FAILED"
    )
    assert (
        result_for_exception(RuntimeError("transient"), max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH).status
        == "FAILED"
    )


def test_result_for_exception_classifies_task_execution_errors() -> None:
    retryable = result_for_exception(TaskFailed("retry later"), max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH)
    terminal = result_for_exception(TaskTerminalError("invalid input"), max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH)

    assert retryable.conductor_payload() == {
        "status": "FAILED",
        "reasonForIncompletion": "retry later",
    }
    assert terminal.conductor_payload() == {
        "status": "FAILED_WITH_TERMINAL_ERROR",
        "reasonForIncompletion": "invalid input",
    }


def test_task_execution_errors_require_string_reasons() -> None:
    with pytest.raises(TypeError, match="reason must be str"):
        TaskFailed({"code": "x"})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="reason must be str"):
        TaskTerminalError({"code": "x"})  # type: ignore[arg-type]


def test_failure_reason_is_truncated_without_output() -> None:
    result = result_for_exception(TaskFailed("abcdef"), max_length=3)

    assert result.conductor_payload() == {
        "status": "FAILED",
        "reasonForIncompletion": "abc",
    }


def test_result_for_exception_fails_closed_on_publish_fence_errors() -> None:
    result = result_for_exception(
        PublishFenceError("main advanced from old to new"),
        max_length=DEFAULT_FAILURE_REASON_MAX_LENGTH,
    )

    assert result.conductor_payload() == {
        "status": "FAILED",
        "reasonForIncompletion": "main advanced from old to new",
    }


def test_runtime_task_result_rejects_inconsistent_payload_shapes() -> None:
    with pytest.raises(ValidationError, match="require output"):
        RuntimeTaskResult(status="COMPLETED")
    with pytest.raises(ValidationError, match="must not include reason"):
        RuntimeTaskResult(status="COMPLETED", output={}, reason_for_incompletion="done")
    with pytest.raises(ValidationError, match="require reason"):
        RuntimeTaskResult(status="FAILED")
    # Failure metadata is optional, but never removes the required failure reason.
    assert RuntimeTaskResult(status="FAILED", output={}, reason_for_incompletion="failed").output == {}


@pytest.mark.parametrize("exception,status", [(TaskFailed, "FAILED"), (TaskTerminalError, "FAILED_WITH_TERMINAL_ERROR")])
def test_explicit_failure_preserves_json_output_and_reason_budget(exception, status) -> None:
    """Metadata must survive independently of the operator-facing reason truncation."""

    payload = {"error": {"code": "PROVIDER_BUSY", "retryable": False, "details": [None, True, 2, 2.5, "原因" * 500]}}
    result = result_for_exception(exception("long reason", output_data=payload), max_length=4)
    assert result.conductor_payload() == {"status": status, "output": payload, "reasonForIncompletion": "long"}


def test_failure_output_is_snapshotted_before_caller_mutation() -> None:
    """A mutable provider response cannot alter failure metadata after it is raised."""

    payload = {"error": {"codes": ["original"]}}
    failure = TaskTerminalError("failed", output_data=payload)
    payload["error"]["codes"].append("changed")
    assert result_for_exception(failure, max_length=500).output == {"error": {"codes": ["original"]}}


@pytest.mark.parametrize("payload", [[], "raw", {1: "value"}, {"nested": {1: "value"}},
                                      {"value": object()}, {"value": (1, 2)},
                                      {"value": float("nan")}, {"value": float("inf")}])
@pytest.mark.parametrize("exception", [TaskFailed, TaskTerminalError])
def test_failure_output_rejects_non_json_metadata(exception, payload) -> None:
    """Unsupported values are rejected at authoring time without including private values in diagnostics."""

    with pytest.raises(TypeError, match="output_data"):
        exception("failed", output_data=payload)


def test_failure_output_rejects_cycles() -> None:
    """Recursive Python containers cannot enter process IPC or SDK serialization."""

    payload = {}
    payload["self"] = payload
    with pytest.raises(TypeError, match="cycles"):
        TaskFailed("failed", output_data=payload)


def test_unknown_exception_attributes_do_not_publish_failure_output() -> None:
    """Only explicit task failure APIs can opt into structured failure publication."""

    failure = RuntimeError("unexpected")
    failure.output_data = {"private": "must-not-publish"}
    assert result_for_exception(failure, max_length=500).output is None


def test_runtime_task_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RuntimeTaskResult(status="COMPLETED", output={}, worker_id="worker-1")
