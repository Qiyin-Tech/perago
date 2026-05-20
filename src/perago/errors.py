class TaskDefinitionError(ValueError):
    """
    Raised when a task module violates the Perago task contract.

    Task definition errors are detected while loading, checking, or extracting
    a single-task module. They describe authoring-time contract problems such
    as unsupported function signatures, invalid workspace checks, or controls
    that cannot be represented in a Conductor TaskDef.

    See Also
    --------
    task : Declare a task module contract.
    load_module_task : Load and validate the task declared by a module.
    build_taskdef : Convert a valid task definition to a Conductor TaskDef.

    Examples
    --------
    >>> TaskDefinitionError("task module must define exactly one task")
    TaskDefinitionError('task module must define exactly one task')
    """


class RuntimeConfigError(ValueError):
    """
    Raised when local runtime configuration is invalid.

    Runtime config errors are produced from process environment variables,
    ``.env`` files, writable root probes, and supervisor settings. They are
    local worker setup failures, not task input validation failures.

    See Also
    --------
    load_runtime_config : Load local runtime configuration.
    RuntimeConfig : Validated runtime configuration model.
    worker_child_specs : Validate supervisor process count and worker ids.

    Examples
    --------
    >>> RuntimeConfigError("CONDUCTOR_SERVER_URL is required for perago start")
    RuntimeConfigError('CONDUCTOR_SERVER_URL is required for perago start')
    """


class TaskInputError(ValueError):
    """
    Raised when Conductor task input does not match the Perago contract.

    Task input errors are attempt-local validation failures raised before a task
    body is invoked. They cover malformed workspace input, missing ``params``,
    extra fields, and outputs that cannot be validated against the declared
    Pydantic model.

    See Also
    --------
    run_workspace_task_attempt : Validate workspace attempt input and output.
    run_workspace_free_task_attempt : Validate workspace-free attempt input and
        output.
    WorkspaceInput : Validated workspace locator model.

    Examples
    --------
    >>> TaskInputError("workspace task input requires workspace and params")
    TaskInputError('workspace task input requires workspace and params')
    """


class GuardrailViolation(RuntimeError):
    """
    Raised when a workspace guardrail check fails.

    This is the common base class for pre- and post-task workspace check
    failures. The runtime maps subclasses by phase so pre-check failures become
    terminal Conductor failures while post-check failures remain ordinary
    failed attempts.

    See Also
    --------
    PreGuardrailViolation : Raised for failed pre-task workspace checks.
    PostGuardrailViolation : Raised for failed post-task workspace checks.
    check_guardrails : Evaluate workspace checks against a local workspace.

    Examples
    --------
    >>> GuardrailViolation("required file is missing")
    GuardrailViolation('required file is missing')
    """


class PreGuardrailViolation(GuardrailViolation):
    """
    Raised when pre-task workspace checks fail.

    A pre-check failure means the downloaded workspace does not satisfy the
    task's required input shape. ``result_for_exception`` maps this subclass to
    ``FAILED_WITH_TERMINAL_ERROR`` because retrying the same invalid input
    should not re-run the task body.

    See Also
    --------
    GuardrailViolation : Base class for workspace check failures.
    check_guardrails : Evaluate configured workspace checks.
    result_for_exception : Convert this exception to a terminal failed result.

    Examples
    --------
    >>> PreGuardrailViolation("raw/input.parquet is missing")
    PreGuardrailViolation('raw/input.parquet is missing')
    """


class PostGuardrailViolation(GuardrailViolation):
    """
    Raised when post-task workspace checks fail.

    A post-check failure means the task body completed but did not leave the
    workspace in the declared output shape. The runtime reports it as ordinary
    ``FAILED`` and does not publish the attempted workspace.

    See Also
    --------
    GuardrailViolation : Base class for workspace check failures.
    check_guardrails : Evaluate configured workspace checks.
    result_for_exception : Convert runtime exceptions to Conductor results.

    Examples
    --------
    >>> PostGuardrailViolation("features/output.parquet is missing")
    PostGuardrailViolation('features/output.parquet is missing')
    """


class PublishFenceError(RuntimeError):
    """
    Raised when a workspace branch cannot be safely advanced.

    Publish fence errors are fail-closed publication failures. They indicate
    that the target branch advanced in a way Perago cannot attribute to the
    same logical task key, so the current attempt must not merge its staging
    branch.

    See Also
    --------
    choose_publish_base : Decide whether the target branch can be advanced.
    build_workspace_publication_plan : Assemble a publish plan and fence
        decision.
    failed_result : Build the ordinary failed Conductor result used for this
        exception.

    Examples
    --------
    >>> PublishFenceError("main advanced from old to new")
    PublishFenceError('main advanced from old to new')
    """


class StaleAttemptError(RuntimeError):
    """
    Raised when a Conductor task snapshot no longer matches the attempt.

    Stale attempt errors come from the attempt fence. They prevent a worker
    from staging or publishing workspace changes after Conductor has moved the
    task out of the same in-progress workflow, task id, or retry count.

    See Also
    --------
    assert_current_attempt_snapshot : Check that a fresh snapshot still matches.
    run_workspace_task_attempt : Calls the attempt fence around publication.
    failed_result : Build the ordinary failed Conductor result used for this
        exception.

    Examples
    --------
    >>> StaleAttemptError("task-9b4c")
    StaleAttemptError('task-9b4c')
    """
