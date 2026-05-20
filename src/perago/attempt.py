from __future__ import annotations

from typing import Any

from perago.errors import StaleAttemptError


def assert_current_attempt_snapshot(task: object, fresh: object) -> None:
    """
    Assert that a fresh Conductor task still represents the same attempt.

    This is Perago's attempt fence. Workspace publication calls it before and
    after staging so a worker fails closed when Conductor no longer reports the
    same in-progress workflow, task id, and retry count.

    Parameters
    ----------
    task : object
        Original attempt-like object captured before task execution. It must
        expose ``status``, ``workflow_instance_id``, ``task_id``, and
        ``retry_count`` attributes.
    fresh : object
        Fresh attempt-like object loaded from Conductor for comparison. It must
        expose the same attributes as ``task``.

    Raises
    ------
    StaleAttemptError
        If ``fresh`` is not ``IN_PROGRESS`` or no longer matches the original
        workflow id, task id, or retry count.
    AttributeError
        If either object is missing a required attempt identity attribute.

    See Also
    --------
    StaleAttemptError : Exception raised when the attempt fence rejects.
    run_workspace_task_attempt : Runtime flow that checks the fence around
        workspace staging and publication.

    Examples
    --------
    >>> from dataclasses import dataclass
    >>> @dataclass(frozen=True)
    ... class Attempt:
    ...     status: str
    ...     workflow_instance_id: str
    ...     task_id: str
    ...     retry_count: int
    >>> attempt = Attempt("IN_PROGRESS", "wf-1", "task-1", 0)
    >>> assert_current_attempt_snapshot(attempt, attempt)
    """
    if (
        _task_attr(fresh, "status") != "IN_PROGRESS"
        or _task_attr(fresh, "workflow_instance_id") != _task_attr(task, "workflow_instance_id")
        or _task_attr(fresh, "task_id") != _task_attr(task, "task_id")
        or _task_attr(fresh, "retry_count") != _task_attr(task, "retry_count")
    ):
        raise StaleAttemptError(_task_attr(task, "task_id"))


def _task_attr(task: object, name: str) -> Any:
    try:
        return getattr(task, name)
    except AttributeError as exc:
        raise AttributeError(f"task is missing required attribute {name}") from exc
