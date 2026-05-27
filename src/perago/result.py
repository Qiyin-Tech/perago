from __future__ import annotations

from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, model_validator

from perago.config import DEFAULT_FAILURE_REASON_MAX_LENGTH
from perago.errors import PreGuardrailViolation, TaskFailed, TaskTerminalError


TaskResultStatus = Literal["COMPLETED", "FAILED", "FAILED_WITH_TERMINAL_ERROR"]


class RuntimeTaskResult(BaseModel):
    """
    Validated result payload returned by a worker attempt.

    ``RuntimeTaskResult`` is Perago's internal representation of the status
    and payload that will be written back to Conductor. It enforces the
    mutually exclusive success and failure payload shapes before the SDK
    payload is assembled.

    Parameters
    ----------
    status : {"COMPLETED", "FAILED", "FAILED_WITH_TERMINAL_ERROR"}
        Conductor task result status selected by the worker runtime.
    output : dict of str to Any or None, default=None
        Completed task output. ``COMPLETED`` results require this field, while
        failed results reject it.
    reason_for_incompletion : str or None, default=None
        Failure reason. ``FAILED`` and ``FAILED_WITH_TERMINAL_ERROR`` results
        require this field, while ``COMPLETED`` results reject it.

    Raises
    ------
    pydantic.ValidationError
        If the payload shape is inconsistent with ``status`` or if unknown
        fields are provided.

    See Also
    --------
    completed_result : Build a completed result.
    failed_result : Build a retryable failed result.
    terminal_failed_result : Build a terminal failed result.
    result_for_exception : Classify an exception into a runtime result.

    Notes
    -----
    The model is frozen and rejects unknown fields. ``worker_id`` is not part
    of this payload; it is attached separately when the Conductor SDK task
    result is updated.

    Examples
    --------
    >>> RuntimeTaskResult(status="COMPLETED", output={"result": {"ok": True}})
    RuntimeTaskResult(...)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: TaskResultStatus
    output: dict[str, Any] | None = None
    reason_for_incompletion: str | None = None

    @model_validator(mode="after")
    def validate_payload_shape(self) -> RuntimeTaskResult:
        """
        Validate the mutually exclusive result payload shape.

        Returns
        -------
        RuntimeTaskResult
            The validated model instance.

        Raises
        ------
        ValueError
            If ``status`` and payload fields do not match the Perago result
            contract.
        """
        if self.status == "COMPLETED":
            if self.output is None:
                raise ValueError("COMPLETED task results require output")
            if self.reason_for_incompletion is not None:
                raise ValueError("COMPLETED task results must not include reason_for_incompletion")
            return self

        if self.reason_for_incompletion is None:
            raise ValueError(f"{self.status} task results require reason_for_incompletion")
        if self.output is not None:
            raise ValueError(f"{self.status} task results must not include output")
        return self

    def conductor_payload(self) -> dict[str, Any]:
        """
        Convert the result to the Conductor update payload shape.

        Returns
        -------
        dict of str to Any
            Mapping containing ``status`` and either ``output`` or
            ``reasonForIncompletion``.

        Examples
        --------
        >>> completed_result({"result": {"ok": True}}).conductor_payload()
        {'status': 'COMPLETED', 'output': {'result': {'ok': True}}}
        """
        payload: dict[str, Any] = {"status": self.status}
        if self.output is not None:
            payload["output"] = self.output
        if self.reason_for_incompletion is not None:
            payload["reasonForIncompletion"] = self.reason_for_incompletion
        return payload


def completed_result(output: dict[str, Any]) -> RuntimeTaskResult:
    """
    Build a completed worker attempt result.

    The helper keeps success result construction consistent across workspace
    and workspace-free attempts after their output payload has already been
    validated by the execution layer.

    Parameters
    ----------
    output : dict of str to Any
        Validated task output payload. Workspace tasks include both
        ``workspace`` and ``result`` keys; workspace-free tasks include only
        ``result``.

    Returns
    -------
    RuntimeTaskResult
        Result with status ``"COMPLETED"`` and the supplied ``output``.

    See Also
    --------
    RuntimeTaskResult : Validated result model.
    build_workspace_task_output : Build a completed workspace task output.
    build_workspace_free_task_output : Build a completed workspace-free task
        output.

    Examples
    --------
    >>> completed_result({"result": {"valid": True}}).status
    'COMPLETED'
    """
    return RuntimeTaskResult(status="COMPLETED", output=output)


def failed_result(
    reason: object,
    *,
    max_length: int = DEFAULT_FAILURE_REASON_MAX_LENGTH,
) -> RuntimeTaskResult:
    """
    Build a failed worker attempt result.

    The helper converts any runtime failure reason to Conductor's string
    ``reasonForIncompletion`` field and leaves ``output`` unset.

    Parameters
    ----------
    reason : object
        Failure reason converted with :class:`str` for Conductor's
        ``reasonForIncompletion`` field.
    max_length : int, default=DEFAULT_FAILURE_REASON_MAX_LENGTH
        Maximum number of characters written to ``reasonForIncompletion``.

    Returns
    -------
    RuntimeTaskResult
        Result with status ``"FAILED"`` and no ``output`` payload.

    See Also
    --------
    terminal_failed_result : Build a terminal failed result.
    result_for_exception : Classify exceptions into result statuses.

    Notes
    -----
    Perago uses ``FAILED`` for most runtime errors, including post guardrail,
    stale attempt, publish fence, and task body failures.

    Examples
    --------
    >>> failed_result("post guardrail failed").conductor_payload()
    {'status': 'FAILED', 'reasonForIncompletion': 'post guardrail failed'}
    """
    return RuntimeTaskResult(
        status="FAILED",
        reason_for_incompletion=_failure_reason(reason, max_length=max_length),
    )


def terminal_failed_result(
    reason: object,
    *,
    max_length: int = DEFAULT_FAILURE_REASON_MAX_LENGTH,
) -> RuntimeTaskResult:
    """
    Build a terminal failed worker attempt result.

    The helper mirrors :func:`failed_result` but selects the Conductor status
    used for input-workspace contract failures detected before the task body.

    Parameters
    ----------
    reason : object
        Failure reason converted with :class:`str` for Conductor's
        ``reasonForIncompletion`` field.
    max_length : int, default=DEFAULT_FAILURE_REASON_MAX_LENGTH
        Maximum number of characters written to ``reasonForIncompletion``.

    Returns
    -------
    RuntimeTaskResult
        Result with status ``"FAILED_WITH_TERMINAL_ERROR"`` and no ``output``
        payload.

    See Also
    --------
    failed_result : Build a regular failed result.
    result_for_exception : Classify exceptions into result statuses.

    Notes
    -----
    In the current runtime classifier this status is reserved for
    :class:`perago.PreGuardrailViolation`. It is not a general non-retryable
    bucket for all validation failures.

    Examples
    --------
    >>> terminal_failed_result("missing input").status
    'FAILED_WITH_TERMINAL_ERROR'
    """
    return RuntimeTaskResult(
        status="FAILED_WITH_TERMINAL_ERROR",
        reason_for_incompletion=_failure_reason(reason, max_length=max_length),
    )


def result_for_exception(
    exc: Exception,
    *,
    max_length: int = DEFAULT_FAILURE_REASON_MAX_LENGTH,
) -> RuntimeTaskResult:
    """
    Classify an exception into a worker attempt result.

    The runtime uses this classifier around task invocation and publication so
    that exceptions are reported through a single Conductor result contract.

    Parameters
    ----------
    exc : Exception
        Exception raised while validating input, running the task body,
        checking guardrails, staging workspace changes, publishing, or cleaning
        up an attempt.
    max_length : int, default=DEFAULT_FAILURE_REASON_MAX_LENGTH
        Maximum number of characters written to ``reasonForIncompletion``.

    Returns
    -------
    RuntimeTaskResult
        ``FAILED_WITH_TERMINAL_ERROR`` for pre guardrail failures and
        ``FAILED`` for all other exceptions.

    See Also
    --------
    RuntimeTaskResult : Validated result model returned by this function.
    PreGuardrailViolation : Exception mapped to terminal failure.
    failed_result : Builder used for ordinary failed results.
    terminal_failed_result : Builder used for terminal failed results.

    Notes
    -----
    The classifier is intentionally fail-closed. Publish fence errors, stale
    attempts, post guardrail failures, and user task exceptions are not
    converted into successful results.

    Examples
    --------
    >>> result_for_exception(PreGuardrailViolation("missing raw file")).status
    'FAILED_WITH_TERMINAL_ERROR'
    >>> result_for_exception(RuntimeError("body failed")).status
    'FAILED'
    """
    if isinstance(exc, TaskTerminalError):
        return terminal_failed_result(exc.reason, max_length=max_length)
    if isinstance(exc, PreGuardrailViolation):
        return terminal_failed_result(exc, max_length=max_length)
    if isinstance(exc, TaskFailed):
        return failed_result(exc.reason, max_length=max_length)
    return failed_result(exc, max_length=max_length)


def _failure_reason(reason: object, *, max_length: int) -> str:
    if max_length < 1:
        raise ValueError("max_length must be a positive integer")
    text = str(reason)
    original_length = len(text)
    if original_length <= max_length:
        return text
    logger.bind(original_length=original_length, max_length=max_length).warning(
        "truncated task failure reason"
    )
    return text[:max_length]
