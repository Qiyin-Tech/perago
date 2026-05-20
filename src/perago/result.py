from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from perago.errors import PreGuardrailViolation


TaskResultStatus = Literal["COMPLETED", "FAILED", "FAILED_WITH_TERMINAL_ERROR"]


class RuntimeTaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: TaskResultStatus
    output: dict[str, Any] | None = None
    reason_for_incompletion: str | None = None

    def conductor_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status}
        if self.output is not None:
            payload["output"] = self.output
        if self.reason_for_incompletion is not None:
            payload["reasonForIncompletion"] = self.reason_for_incompletion
        return payload


def completed_result(output: dict[str, Any]) -> RuntimeTaskResult:
    return RuntimeTaskResult(status="COMPLETED", output=output)


def failed_result(reason: object) -> RuntimeTaskResult:
    return RuntimeTaskResult(status="FAILED", reason_for_incompletion=str(reason))


def terminal_failed_result(reason: object) -> RuntimeTaskResult:
    return RuntimeTaskResult(
        status="FAILED_WITH_TERMINAL_ERROR",
        reason_for_incompletion=str(reason),
    )


def result_for_exception(exc: Exception) -> RuntimeTaskResult:
    if isinstance(exc, PreGuardrailViolation):
        return terminal_failed_result(exc)
    return failed_result(exc)
