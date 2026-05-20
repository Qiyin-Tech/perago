from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from conductor.client.configuration.configuration import Configuration
from conductor.client.http.models.task_result import TaskResult
from conductor.client.http.models.task_result_status import TaskResultStatus
from conductor.client.orkes.orkes_metadata_client import OrkesMetadataClient
from conductor.client.orkes.orkes_task_client import OrkesTaskClient
from loguru import logger

from perago.config import ConductorConfig
from perago.execution import (
    CleanupStaging,
    DownloadWorkspace,
    LoadCurrentAttempt,
    PublishWorkspace,
    StageWorkspace,
    run_workspace_free_task_attempt,
    run_workspace_task_attempt,
)
from perago.result import RuntimeTaskResult
from perago.task import TaskDefinition


POLL_EMPTY_SLEEP_SECONDS = 1.0
POLL_ERROR_BACKOFF_SECONDS = 5.0


@dataclass(frozen=True)
class ConductorTaskAttempt:
    workflow_instance_id: str
    task_id: str
    retry_count: int
    task_def_name: str
    reference_task_name: str
    seq: int
    iteration: int
    status: str
    input_data: Mapping[str, Any]
    retried_task_id: str | None = None
    response_timeout_seconds: int | None = None


class ConductorRuntimeClient(Protocol):
    def taskdef_exists(self, task_name: str) -> bool: ...

    def poll_task(self, task_name: str, *, worker_id: str) -> ConductorTaskAttempt | None: ...

    def get_task(self, task_id: str) -> ConductorTaskAttempt: ...

    def update_task(self, attempt: ConductorTaskAttempt, result: RuntimeTaskResult, *, worker_id: str) -> None: ...


class OrkesConductorRuntimeClient:
    def __init__(
        self,
        *,
        task_client: OrkesTaskClient,
        metadata_client: OrkesMetadataClient,
        task_update_timeout_seconds: int | None = None,
    ) -> None:
        self._task_client = task_client
        self._metadata_client = metadata_client
        self._task_update_timeout_seconds = task_update_timeout_seconds

    @classmethod
    def from_config(
        cls,
        config: ConductorConfig,
        *,
        task_update_timeout_seconds: int | None = None,
    ) -> OrkesConductorRuntimeClient:
        sdk_config = Configuration(server_api_url=config.server_url)
        return cls(
            task_client=OrkesTaskClient(sdk_config),
            metadata_client=OrkesMetadataClient(sdk_config),
            task_update_timeout_seconds=task_update_timeout_seconds,
        )

    def taskdef_exists(self, task_name: str) -> bool:
        try:
            self._metadata_client.get_task_def(task_name)
        except Exception as exc:  # noqa: BLE001
            if _looks_like_not_found(exc):
                return False
            raise
        return True

    def poll_task(self, task_name: str, *, worker_id: str) -> ConductorTaskAttempt | None:
        task = self._task_client.poll_task(task_name, worker_id=worker_id)
        if task is None or getattr(task, "task_id", None) in {None, ""}:
            return None
        return conductor_task_to_attempt(task)

    def get_task(self, task_id: str) -> ConductorTaskAttempt:
        return conductor_task_to_attempt(self._task_client.get_task(task_id))

    def update_task(self, attempt: ConductorTaskAttempt, result: RuntimeTaskResult, *, worker_id: str) -> None:
        task_result = runtime_result_to_sdk_task_result(attempt, result, worker_id=worker_id)
        if self._task_update_timeout_seconds is None:
            self._task_client.update_task(task_result)
            return
        self._task_client.taskResourceApi.update_task(
            task_result,
            _request_timeout=self._task_update_timeout_seconds,
        )


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


def run_worker_poll_loop(
    *,
    task: TaskDefinition,
    client: ConductorRuntimeClient,
    worker_id: str,
    workspace_root: Any,
    should_stop: Any,
    download_workspace: DownloadWorkspace,
    stage_workspace: StageWorkspace,
    publish_workspace: PublishWorkspace,
    cleanup_staging: CleanupStaging,
    poll_empty_sleep_seconds: float = POLL_EMPTY_SLEEP_SECONDS,
    poll_error_backoff_seconds: float = POLL_ERROR_BACKOFF_SECONDS,
) -> None:
    while not should_stop():
        try:
            attempt = client.poll_task(task.name, worker_id=worker_id)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error("failed to poll Conductor task")
            _sleep_until_stop(poll_error_backoff_seconds, should_stop)
            continue

        if attempt is None:
            _sleep_until_stop(poll_empty_sleep_seconds, should_stop)
            continue

        result = execute_polled_task(
            task=task,
            attempt=attempt,
            workspace_root=workspace_root,
            download_workspace=download_workspace,
            load_current_attempt=lambda current_attempt: client.get_task(current_attempt.task_id),
            stage_workspace=stage_workspace,
            publish_workspace=publish_workspace,
            cleanup_staging=cleanup_staging,
        )
        try:
            client.update_task(attempt, result, worker_id=worker_id)
        except Exception as exc:  # noqa: BLE001
            logger.bind(task_id=attempt.task_id, workflow_instance_id=attempt.workflow_instance_id).opt(
                exception=exc
            ).error("failed to update Conductor task result")
            _sleep_until_stop(poll_error_backoff_seconds, should_stop)


def execute_polled_task(
    *,
    task: TaskDefinition,
    attempt: ConductorTaskAttempt,
    workspace_root: Any,
    download_workspace: DownloadWorkspace,
    load_current_attempt: LoadCurrentAttempt,
    stage_workspace: StageWorkspace,
    publish_workspace: PublishWorkspace,
    cleanup_staging: CleanupStaging,
) -> RuntimeTaskResult:
    if task.has_workspace:
        return run_workspace_task_attempt(
            task,
            attempt.input_data,
            attempt,
            workspace_root,
            download_workspace=download_workspace,
            load_current_attempt=load_current_attempt,
            stage_workspace=stage_workspace,
            publish_workspace=publish_workspace,
            cleanup_staging=cleanup_staging,
        )
    return run_workspace_free_task_attempt(task, attempt.input_data)


def _sleep_until_stop(seconds: float, should_stop: Any) -> None:
    deadline = time.monotonic() + seconds
    while not should_stop() and time.monotonic() < deadline:
        time.sleep(min(0.1, deadline - time.monotonic()))


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


def _looks_like_not_found(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status == 404:
        return True
    return "404" in str(exc) and "not" in str(exc).lower()
