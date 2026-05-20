from __future__ import annotations

from typing import Any

from perago.errors import StaleAttemptError


def assert_current_attempt_snapshot(task: object, fresh: object) -> None:
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
