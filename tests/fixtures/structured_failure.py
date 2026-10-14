"""A no-network task exercising native failure output through both execution modes."""

from perago import TaskFailed, TaskTerminalError, task
from pydantic import BaseModel


class Params(BaseModel):
    """Select a failure state without changing the declared success output contract."""

    terminal: bool = True


class Output(BaseModel):
    """Successful result shape intentionally differs from failure metadata."""

    value: int


@task(name="tests.structuredFailure", owner_email="test@example.invalid")
def structured_failure(params: Params) -> Output:
    """Produce only in-memory metadata; no provider or Conductor call is made here."""

    exception = TaskTerminalError if params.terminal else TaskFailed
    raise exception("provider unavailable", output_data={"error": {"code": "SERVICE_FAILURE", "retryable": not params.terminal,
                                                                    "details": {"providerTaskId": "original-upstream-task"}}})
