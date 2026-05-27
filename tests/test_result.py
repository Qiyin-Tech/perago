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
    with pytest.raises(ValidationError, match="must not include output"):
        RuntimeTaskResult(status="FAILED", output={}, reason_for_incompletion="failed")


def test_runtime_task_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RuntimeTaskResult(status="COMPLETED", output={}, worker_id="worker-1")
