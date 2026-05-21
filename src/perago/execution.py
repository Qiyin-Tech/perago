from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel

from perago.attempt import assert_current_attempt_snapshot
from perago.errors import (
    GuardrailViolation,
    PostGuardrailViolation,
    PreGuardrailViolation,
    TaskInputError,
)
from perago.guards import check_guardrails
from perago.models import WorkspaceInput, WorkspaceSpec
from perago.result import RuntimeTaskResult, completed_result, result_for_exception
from perago.task import TaskDefinition
from perago.workspace import (
    cleanup_attempt_workspace_safely,
    new_workspace_owner,
    prepare_attempt_workspace,
    register_active_workspace_owner,
    unregister_active_workspace_owner,
)


DownloadWorkspace = Callable[[WorkspaceInput, WorkspaceSpec, Path], None]
LoadCurrentAttempt = Callable[[object], object]
StageWorkspace = Callable[[Path, WorkspaceInput, WorkspaceSpec, object], "StagedWorkspace"]
PublishWorkspace = Callable[["StagedWorkspace", WorkspaceInput, WorkspaceSpec, object], str]
CleanupStaging = Callable[["StagedWorkspace"], None]


@dataclass(frozen=True)
class StagedWorkspace:
    """
    Workspace staging reference returned before publication.

    ``StagedWorkspace`` is the complete LakeFS staging reference passed from a
    workspace staging callback to publication and cleanup callbacks. It carries
    repository, staging branch, and staging commit so cleanup can be driven by
    the staged reference itself instead of worker-local mutable state.

    Parameters
    ----------
    repository : str
        LakeFS repository that owns the staging branch.
    branch : str
        LakeFS staging branch that contains the attempted workspace changes.
    commit : str
        Commit reference produced by staging the local attempt workspace.

    Attributes
    ----------
    repository : str
        LakeFS repository that owns the staging branch.
    branch : str
        LakeFS staging branch that contains the attempted workspace changes.
    commit : str
        Commit reference produced by staging the local attempt workspace.

    See Also
    --------
    run_workspace_task_attempt : Runtime flow that consumes staged workspace
        references.

    Notes
    -----
    The dataclass is frozen. Cleanup receives the same object even if publish
    fails or a later attempt fence rejects the attempt.

    Examples
    --------
    >>> StagedWorkspace(repository="songs", branch="perago/staging/wf/task", commit="abc123").branch
    'perago/staging/wf/task'
    """

    repository: str
    branch: str
    commit: str


@dataclass(frozen=True)
class TaskExecutionContext:
    attempt: object
    execution_id: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.attempt, name)


def run_workspace_task_attempt(
    task: TaskDefinition,
    input_data: Mapping[str, Any],
    attempt: object,
    workspace_root: Path,
    *,
    download_workspace: DownloadWorkspace,
    load_current_attempt: LoadCurrentAttempt,
    stage_workspace: StageWorkspace,
    publish_workspace: PublishWorkspace,
    cleanup_staging: CleanupStaging,
    owner_worker_id: str | None = None,
    execution_id: str | None = None,
) -> RuntimeTaskResult:
    """
    Run one workspace task attempt.

    This function is the testable execution core used by the Conductor worker
    runtime. It validates the Conductor input shape, prepares an attempt-local
    workspace, downloads the declared workspace input, invokes the task body,
    checks the attempt fence before and after staging, publishes the staged
    workspace, and cleans local and staging resources.

    Parameters
    ----------
    task : TaskDefinition
        Loaded workspace task definition. Workspace-free task definitions are
        rejected.
    input_data : mapping of str to Any
        Conductor task input. Workspace attempts must contain exactly
        ``"workspace"`` and ``"params"``.
    attempt : object
        Conductor task attempt object. It must expose the attributes consumed
        by :func:`perago.assert_current_attempt_snapshot` and workspace
        directory helpers.
    workspace_root : pathlib.Path
        Root directory under which the attempt-local workspace is prepared.
    download_workspace : callable
        Callback that materializes ``WorkspaceInput`` into the local workspace
        directory.
    load_current_attempt : callable
        Callback that reloads the latest Conductor attempt state for attempt
        fence checks.
    stage_workspace : callable
        Callback that stages local workspace changes and returns a
        :class:`StagedWorkspace`.
    publish_workspace : callable
        Callback that publishes a staged workspace and returns the published
        workspace reference.
    cleanup_staging : callable
        Callback that removes or abandons the staging branch after the attempt
        completes or fails after staging.
    owner_worker_id : str or None, default=None
        Worker id written into the local workspace owner marker for active
        owner tracking and supervisor GC.
    execution_id : str or None, default=None
        Execution-scoped id used to isolate local attempt workspace and LakeFS
        staging branch names. A new id is generated when omitted.

    Returns
    -------
    RuntimeTaskResult
        ``COMPLETED`` result containing ``workspace`` and ``result`` output
        when every phase succeeds; otherwise a failed result produced by
        :func:`perago.result_for_exception`.

    Raises
    ------
    TaskInputError
        If ``task`` is not a workspace task. Exceptions raised after execution
        enters the attempt ``try`` block are converted to ``RuntimeTaskResult``
        instead of being raised.

    See Also
    --------
    invoke_workspace_task_body : Validate and invoke only the task body phase.
    build_workspace_task_output : Build the completed output payload.
    result_for_exception : Convert execution exceptions to runtime results.

    Notes
    -----
    Cleanup is best effort. A staging cleanup failure is logged and does not
    replace the result of the completed or failed attempt.

    Examples
    --------
    >>> from pathlib import Path
    >>> task_def = load_module_task("app.workers.features_build")
    >>> result = run_workspace_task_attempt(  # doctest: +SKIP
    ...     task_def,
    ...     {"workspace": {...}, "params": {...}},
    ...     attempt,
    ...     Path("/tmp/perago/workspaces"),
    ...     download_workspace=lakefs.download_workspace,
    ...     load_current_attempt=conductor.load_current_attempt,
    ...     stage_workspace=lakefs.stage_workspace,
    ...     publish_workspace=lakefs.publish_workspace,
    ...     cleanup_staging=lakefs.cleanup_staging,
    ... )
    >>> result.status  # doctest: +SKIP
    'COMPLETED'
    """
    if not task.has_workspace:
        raise TaskInputError("run_workspace_task_attempt only supports workspace tasks")
    workspace = task.workspace
    if workspace is None:
        raise TaskInputError("workspace task definition is missing WorkspaceSpec")

    workspace_dir: Path | None = None
    staged: StagedWorkspace | None = None
    execution = TaskExecutionContext(
        attempt=attempt,
        execution_id=execution_id or getattr(attempt, "execution_id", uuid4().hex),
    )
    owner = new_workspace_owner(owner_worker_id or os.environ.get("PERAGO_WORKER_ID", f"pid-{os.getpid()}"))
    register_active_workspace_owner(owner)
    try:
        if set(input_data) != {"workspace", "params"}:
            raise TaskInputError("workspace task input must contain only workspace and params")
        workspace_input = WorkspaceInput.model_validate(input_data["workspace"])
        workspace_dir = prepare_attempt_workspace(workspace_root, execution, owner)
        download_workspace(workspace_input, workspace, workspace_dir)
        body_output = invoke_workspace_task_body(task, input_data, workspace_dir)
        assert_current_attempt_snapshot(attempt, load_current_attempt(attempt))
        staged = stage_workspace(workspace_dir, workspace_input, workspace, execution)
        assert_current_attempt_snapshot(attempt, load_current_attempt(attempt))
        published_ref = publish_workspace(staged, workspace_input, workspace, execution)
        output_workspace = workspace_input.published_output(published_ref)
        return completed_result(
            {
                "workspace": output_workspace.model_dump(mode="json"),
                **body_output,
            }
        )
    except Exception as exc:
        return result_for_exception(exc)
    finally:
        if staged is not None:
            _cleanup_staging_safely(staged, cleanup_staging)
        if workspace_dir is not None:
            cleanup_attempt_workspace_safely(workspace_dir, attempt)
        unregister_active_workspace_owner(owner)


def run_workspace_free_task_attempt(
    task: TaskDefinition,
    input_data: Mapping[str, Any],
) -> RuntimeTaskResult:
    """
    Run one workspace-free task attempt.

    Workspace-free attempts only validate the ``params`` wrapper, invoke the
    task callable, validate the output model, and convert failures to the
    runtime result contract.

    Parameters
    ----------
    task : TaskDefinition
        Loaded workspace-free task definition. Workspace task definitions are
        rejected.
    input_data : mapping of str to Any
        Conductor task input. Workspace-free attempts must contain exactly
        ``"params"``.

    Returns
    -------
    RuntimeTaskResult
        ``COMPLETED`` result containing the validated ``result`` payload, or a
        failed result produced from the raised exception.

    Raises
    ------
    TaskInputError
        If ``task`` is a workspace task. Input and output validation failures
        after execution enters the attempt ``try`` block are converted to
        ``RuntimeTaskResult``.

    See Also
    --------
    invoke_workspace_free_task : Invoke and validate a workspace-free task.
    build_workspace_free_task_output : Build the completed output payload.
    result_for_exception : Convert execution exceptions to runtime results.

    Examples
    --------
    >>> task_def = load_module_task("app.workers.metadata_validate")
    >>> result = run_workspace_free_task_attempt(
    ...     task_def,
    ...     {"params": {"song_id": "song-000123", "min_duration_seconds": 30}},
    ... )
    >>> result.status
    'COMPLETED'
    """
    if task.has_workspace:
        raise TaskInputError("run_workspace_free_task_attempt only supports workspace-free tasks")

    try:
        return completed_result(invoke_workspace_free_task(task, input_data))
    except Exception as exc:
        return result_for_exception(exc)


def invoke_workspace_task_body(
    task: TaskDefinition,
    input_data: Mapping[str, Any],
    workspace_dir: Path,
) -> dict[str, Any]:
    """
    Invoke a workspace task body against a prepared local workspace.

    The helper performs the task-body portion of a workspace attempt: it checks
    the Conductor input wrapper, validates ``WorkspaceInput`` and params,
    applies pre guardrails, calls the task function, validates the declared
    output model, and applies post guardrails.

    Parameters
    ----------
    task : TaskDefinition
        Loaded workspace task definition.
    input_data : mapping of str to Any
        Conductor task input containing exactly ``"workspace"`` and
        ``"params"``.
    workspace_dir : pathlib.Path
        Attempt-local workspace directory already populated from the workspace
        input.

    Returns
    -------
    dict of str to Any
        Body output wrapper containing only the validated ``"result"`` field.

    Raises
    ------
    TaskInputError
        If ``task`` is not a workspace task or if the input wrapper shape is
        invalid.
    PreGuardrailViolation
        If a pre-execution workspace guardrail fails.
    PostGuardrailViolation
        If a post-execution workspace guardrail fails.
    pydantic.ValidationError
        If workspace input, params, or task result validation fails.

    See Also
    --------
    run_workspace_task_attempt : Full workspace attempt execution flow.
    build_workspace_task_output : Add a published workspace ref to a validated
        result.
    check_guardrails : Evaluate workspace guardrail declarations.

    Examples
    --------
    >>> from pathlib import Path
    >>> task_def = load_module_task("app.workers.features_build")
    >>> output = invoke_workspace_task_body(  # doctest: +SKIP
    ...     task_def,
    ...     {"workspace": {...}, "params": {"feature_set": "default", "min_rows": 100}},
    ...     Path("/tmp/perago/workspaces/attempt"),
    ... )
    >>> sorted(output)
    ['result']
    """
    if not task.has_workspace:
        raise TaskInputError("invoke_workspace_task_body only supports workspace tasks")
    if set(input_data) != {"workspace", "params"}:
        raise TaskInputError("workspace task input must contain only workspace and params")

    WorkspaceInput.model_validate(input_data["workspace"])
    params = task.params_model.model_validate(input_data["params"], extra="forbid")
    workspace = task.workspace
    if workspace is None:
        raise TaskInputError("workspace task definition is missing WorkspaceSpec")

    _check_phase_guardrails(workspace_dir, workspace.pre, "pre", PreGuardrailViolation)
    raw_result = task.fn(workspace_dir, params)
    result = _validate_result(task, raw_result)
    _check_phase_guardrails(workspace_dir, workspace.post, "post", PostGuardrailViolation)
    return {"result": result.model_dump(mode="json")}


def invoke_workspace_free_task(task: TaskDefinition, input_data: Mapping[str, Any]) -> dict[str, Any]:
    """
    Invoke a workspace-free task and validate its output wrapper.

    The helper is the body-level execution path for tasks that do not declare a
    ``WorkspaceSpec``. It accepts only the Conductor ``params`` wrapper, calls
    the task function with the validated params model, and returns the validated
    output payload.

    Parameters
    ----------
    task : TaskDefinition
        Loaded workspace-free task definition.
    input_data : mapping of str to Any
        Conductor task input containing exactly ``"params"``.

    Returns
    -------
    dict of str to Any
        Completed workspace-free output wrapper containing only ``"result"``.

    Raises
    ------
    TaskInputError
        If ``task`` is a workspace task or if the input wrapper shape is
        invalid.
    pydantic.ValidationError
        If params or task result validation fails.

    See Also
    --------
    run_workspace_free_task_attempt : Attempt wrapper that converts exceptions
        to runtime results.
    build_workspace_free_task_output : Validate and wrap a raw task result.

    Examples
    --------
    >>> task_def = load_module_task("app.workers.metadata_validate")
    >>> invoke_workspace_free_task(
    ...     task_def,
    ...     {"params": {"song_id": "song-000123", "min_duration_seconds": 30}},
    ... )
    {'result': {'valid': True, 'reason': None}}
    """
    if task.has_workspace:
        raise TaskInputError("invoke_workspace_free_task only supports workspace-free tasks")
    if set(input_data) != {"params"}:
        raise TaskInputError("workspace-free task input must contain only params")

    params = task.params_model.model_validate(input_data["params"], extra="forbid")
    raw_result = task.fn(params)
    return build_workspace_free_task_output(task, raw_result)


def build_workspace_free_task_output(task: TaskDefinition, raw_result: object) -> dict[str, Any]:
    """
    Validate and wrap a workspace-free task result.

    The helper applies the task output model outside the invocation path so
    tests and runtime integrations can reuse the exact same output contract.

    Parameters
    ----------
    task : TaskDefinition
        Loaded workspace-free task definition whose output model validates the
        result.
    raw_result : object
        Value returned by the task callable. Pydantic model instances are
        dumped before being revalidated with ``extra="forbid"``; mappings and
        other supported values are validated directly by the output model.

    Returns
    -------
    dict of str to Any
        Output wrapper containing only ``"result"``.

    Raises
    ------
    TaskInputError
        If ``task`` is a workspace task.
    pydantic.ValidationError
        If ``raw_result`` does not satisfy the task output model or contains
        extra fields.

    See Also
    --------
    invoke_workspace_free_task : Invoke a workspace-free task and build this
        output wrapper.
    build_workspace_task_output : Build the corresponding workspace task
        output wrapper.

    Examples
    --------
    >>> task_def = load_module_task("app.workers.metadata_validate")
    >>> build_workspace_free_task_output(task_def, {"valid": False, "reason": "missing"})
    {'result': {'valid': False, 'reason': 'missing'}}
    """
    if task.has_workspace:
        raise TaskInputError("workspace-free output can only be built for workspace-free tasks")
    result = _validate_result(task, raw_result)
    return {"result": result.model_dump(mode="json")}


def build_workspace_task_output(
    task: TaskDefinition,
    input_workspace: WorkspaceInput | Mapping[str, Any],
    published_ref: str,
    raw_result: object,
) -> dict[str, Any]:
    """
    Validate and wrap a completed workspace task output.

    ``build_workspace_task_output`` combines a published workspace reference
    with a task body result. It is useful for tests and runtime integrations
    that already performed download, body execution, staging, and publication.

    Parameters
    ----------
    task : TaskDefinition
        Loaded workspace task definition whose output model validates
        ``raw_result``.
    input_workspace : WorkspaceInput or mapping of str to Any
        Original workspace input used for the attempt. The repository, branch,
        and ref type are preserved in the output.
    published_ref : str
        Reference returned by workspace publication.
    raw_result : object
        Value returned by the task callable. It is validated by the task output
        model with extra fields forbidden.

    Returns
    -------
    dict of str to Any
        Output wrapper containing ``"workspace"`` with the published ref and
        ``"result"`` with the validated task output.

    Raises
    ------
    TaskInputError
        If ``task`` is not a workspace task.
    pydantic.ValidationError
        If ``input_workspace`` or ``raw_result`` is invalid.

    See Also
    --------
    run_workspace_task_attempt : Full workspace attempt execution flow.
    WorkspaceInput.published_output : Derive the published workspace output
        model.
    build_workspace_free_task_output : Build the workspace-free output wrapper.

    Examples
    --------
    >>> task_def = load_module_task("app.workers.features_build")
    >>> output = build_workspace_task_output(  # doctest: +SKIP
    ...     task_def,
    ...     {"repository": "song-000123", "branch": "main", "ref_type": "commit", "ref": "..."},
    ...     "published-ref",
    ...     {"row_count": 100, "feature_count": 24},
    ... )
    >>> sorted(output)
    ['result', 'workspace']
    """
    if not task.has_workspace:
        raise TaskInputError("workspace output can only be built for workspace tasks")
    workspace_input = WorkspaceInput.model_validate(input_workspace)
    workspace_output = workspace_input.published_output(published_ref)
    result = _validate_result(task, raw_result)
    return {
        "workspace": workspace_output.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
    }


def _check_phase_guardrails(
    workspace_dir: Path,
    guardrails: list[Any],
    phase: str,
    error: type[GuardrailViolation],
) -> None:
    try:
        check_guardrails(workspace_dir, guardrails, phase)
    except GuardrailViolation as exc:
        raise error(str(exc)) from exc


def _cleanup_staging_safely(staged: StagedWorkspace, cleanup_staging: CleanupStaging) -> None:
    try:
        cleanup_staging(staged)
    except Exception as exc:  # noqa: BLE001
        logger.bind(
            staging_branch=staged.branch,
            staging_commit=staged.commit,
        ).opt(exception=exc).error("failed to clean staging workspace")


def _validate_result(task: TaskDefinition, raw_result: object) -> BaseModel:
    if isinstance(raw_result, BaseModel):
        return task.output_model.model_validate(raw_result.model_dump(), extra="forbid")
    return task.output_model.model_validate(raw_result, extra="forbid")
