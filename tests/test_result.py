from perago import (
    PostGuardrailViolation,
    PreGuardrailViolation,
    PublishFenceError,
    completed_result,
    failed_result,
    result_for_exception,
    terminal_failed_result,
)


def test_completed_result_payload_contains_output() -> None:
    result = completed_result({"result": {"valid": True}})

    assert result.status == "COMPLETED"
    assert result.conductor_payload() == {
        "status": "COMPLETED",
        "output": {"result": {"valid": True}},
    }


def test_failed_result_payload_contains_reason() -> None:
    result = failed_result("post failed")

    assert result.conductor_payload() == {
        "status": "FAILED",
        "reasonForIncompletion": "post failed",
    }


def test_terminal_failed_result_payload_contains_reason() -> None:
    result = terminal_failed_result("pre failed")

    assert result.conductor_payload() == {
        "status": "FAILED_WITH_TERMINAL_ERROR",
        "reasonForIncompletion": "pre failed",
    }


def test_result_for_exception_classifies_guardrail_failures_by_phase() -> None:
    assert result_for_exception(PreGuardrailViolation("missing input")).status == "FAILED_WITH_TERMINAL_ERROR"
    assert result_for_exception(PostGuardrailViolation("missing output")).status == "FAILED"
    assert result_for_exception(RuntimeError("transient")).status == "FAILED"


def test_result_for_exception_fails_closed_on_publish_fence_errors() -> None:
    result = result_for_exception(PublishFenceError("main advanced from old to new"))

    assert result.conductor_payload() == {
        "status": "FAILED",
        "reasonForIncompletion": "main advanced from old to new",
    }
