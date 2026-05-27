from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from conductor.client.http.models.task_result import TaskResult
from conductor.client.http.models.task_result_status import TaskResultStatus

from perago.result import RuntimeTaskResult

from .models import ConductorTaskAttempt


def conductor_task_to_attempt(task: object) -> ConductorTaskAttempt:
    return ConductorTaskAttempt(
        workflow_instance_id=str(_required_task_attr(task, "workflow_instance_id")),
        task_id=str(_required_task_attr(task, "task_id")),
        retry_count=int(_required_task_attr(task, "retry_count")),
        task_def_name=str(_required_task_attr(task, "task_def_name")),
        reference_task_name=str(_required_task_attr(task, "reference_task_name")),
        seq=int(_required_task_attr(task, "seq")),
        iteration=int(_task_attr(task, "iteration", 0) or 0),
        status=str(_required_task_attr(task, "status")),
        input_data=_mapping_attr(task, "input_data"),
        retried_task_id=_optional_str(_task_attr(task, "retried_task_id", None)),
        response_timeout_seconds=_optional_int(_task_attr(task, "response_timeout_seconds", None)),
    )


def runtime_result_to_sdk_task_result(
    attempt: ConductorTaskAttempt,
    result: RuntimeTaskResult,
    *,
    worker_id: str,
) -> TaskResult:
    task_result = TaskResult(
        workflow_instance_id=attempt.workflow_instance_id,
        task_id=attempt.task_id,
        worker_id=worker_id,
        status=TaskResultStatus(result.status),
    )
    if result.status == "COMPLETED":
        task_result.output_data = result.output
    else:
        task_result.reason_for_incompletion = result.reason_for_incompletion
    return task_result


def _required_task_attr(task: object, name: str) -> Any:
    value = _task_attr(task, name, None)
    if value is None:
        raise AttributeError(f"Conductor task is missing required field {name}")
    return value


def _task_attr(task: object, name: str, default: Any) -> Any:
    if isinstance(task, Mapping):
        return task.get(name, default)
    return getattr(task, name, default)


def _mapping_attr(task: object, name: str) -> Mapping[str, Any]:
    value = _required_task_attr(task, name)
    if not isinstance(value, Mapping):
        raise TypeError(f"Conductor task field {name} must be a mapping")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
