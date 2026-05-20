from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from perago.errors import TaskDefinitionError
from perago.guards import _WorkspaceGuardrail


class RetryPolicy(BaseModel):
    """Retry controls copied into the generated Conductor TaskDef.

    ``RetryPolicy`` configures the retry-related TaskDef fields exposed through
    :class:`TaskControls`. The model rejects unknown fields and validates all
    timing values as non-negative integers.

    Parameters
    ----------
    count : int, default=3
        Number of retries written as ``retryCount``. Must be between ``0`` and
        ``10``.
    logic : {"FIXED", "EXPONENTIAL_BACKOFF", "LINEAR_BACKOFF"}, default="FIXED"
        Retry algorithm written as ``retryLogic``.
    delay_seconds : int, default=60
        Initial retry delay written as ``retryDelaySeconds``.
    max_delay_seconds : int, default=0
        Maximum retry delay written as ``maxRetryDelaySeconds``.
    jitter_ms : int, default=0
        Backoff jitter written as ``backoffJitterMs``.

    Notes
    -----
    This model only describes Conductor retry fields. It does not decide
    whether a specific Perago failure is retryable.

    Examples
    --------
    >>> RetryPolicy(count=4, logic="FIXED", delay_seconds=30)
    RetryPolicy(...)
    """

    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=3, ge=0, le=10)
    logic: Literal["FIXED", "EXPONENTIAL_BACKOFF", "LINEAR_BACKOFF"] = "FIXED"
    delay_seconds: int = Field(default=60, ge=0)
    max_delay_seconds: int = Field(default=0, ge=0)
    jitter_ms: int = Field(default=0, ge=0)


class TimeoutPolicy(BaseModel):
    """Timeout controls copied into the generated Conductor TaskDef.

    ``TimeoutPolicy`` holds the general Conductor timeout fields used when a
    task does not declare a :class:`PublishBudget`. Workspace tasks with a
    publish budget derive ``responseTimeoutSeconds`` from that budget instead
    of ``response_seconds``.

    Parameters
    ----------
    policy : {"RETRY", "TIME_OUT_WF", "ALERT_ONLY"}, default="TIME_OUT_WF"
        Timeout behavior written as ``timeoutPolicy``.
    seconds : int, default=0
        Task timeout written as ``timeoutSeconds``.
    response_seconds : int, default=600
        Response timeout written as ``responseTimeoutSeconds`` when no publish
        budget is configured.
    poll_seconds : int, default=0
        Poll timeout written as ``pollTimeoutSeconds``.
    total_seconds : int, default=0
        Total timeout written as ``totalTimeoutSeconds``.

    Notes
    -----
    All values are non-negative integers and unknown fields are rejected.

    Examples
    --------
    >>> TimeoutPolicy(response_seconds=900)
    TimeoutPolicy(...)
    """

    model_config = ConfigDict(extra="forbid")

    policy: Literal["RETRY", "TIME_OUT_WF", "ALERT_ONLY"] = "TIME_OUT_WF"
    seconds: int = Field(default=0, ge=0)
    response_seconds: int = Field(default=600, ge=0)
    poll_seconds: int = Field(default=0, ge=0)
    total_seconds: int = Field(default=0, ge=0)


class ExecutionLimits(BaseModel):
    """Optional execution and rate-limit controls for a TaskDef.

    ``ExecutionLimits`` maps to Conductor concurrency and rate limit fields.
    ``None`` values are omitted from the generated TaskDef.

    Parameters
    ----------
    concurrent_exec_limit : int or None, default=None
        Optional value written as ``concurrentExecLimit``.
    rate_limit_frequency_in_seconds : int or None, default=None
        Optional rate-limit window written as
        ``rateLimitFrequencyInSeconds``. Must be configured together with
        ``rate_limit_per_frequency``.
    rate_limit_per_frequency : int or None, default=None
        Optional rate-limit count written as ``rateLimitPerFrequency``. Must be
        configured together with ``rate_limit_frequency_in_seconds``.

    Raises
    ------
    TaskDefinitionError
        If only one side of the rate-limit pair is configured.

    Examples
    --------
    >>> ExecutionLimits(concurrent_exec_limit=2)
    ExecutionLimits(...)
    """

    model_config = ConfigDict(extra="forbid")

    concurrent_exec_limit: int | None = Field(default=None, ge=0)
    rate_limit_frequency_in_seconds: int | None = Field(default=None, ge=0)
    rate_limit_per_frequency: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_rate_limit_pair(self) -> ExecutionLimits:
        """Validate that Conductor rate-limit fields are configured together.

        Returns
        -------
        ExecutionLimits
            The validated model.

        Raises
        ------
        TaskDefinitionError
            If exactly one rate-limit field is configured.
        """
        has_frequency = self.rate_limit_frequency_in_seconds is not None
        has_limit = self.rate_limit_per_frequency is not None
        if has_frequency != has_limit:
            raise TaskDefinitionError(
                "rate_limit_frequency_in_seconds and rate_limit_per_frequency must be configured together"
            )
        return self


class PublishBudget(BaseModel):
    """Operational time budget for workspace publication.

    ``PublishBudget`` derives the Conductor ``responseTimeoutSeconds`` used by
    generated TaskDefs for workspace tasks and provides runtime timeouts for
    the LakeFS merge and Conductor completion calls. It is an operational time
    budget, not an exactly-once publication proof.

    Parameters
    ----------
    observed_merge_p99_seconds : int
        Observed high-percentile LakeFS merge latency under expected workload.
    safety_margin_seconds : int
        Additional safety margin added to the observed merge latency.
    lakefs_merge_timeout_seconds : int
        Request timeout for the LakeFS merge operation. Must cover
        ``observed_merge_p99_seconds + safety_margin_seconds``.
    conductor_completion_timeout_seconds : int
        Request timeout for reporting the final task result to Conductor.
    worker_shutdown_grace_seconds : int
        Grace period reserved for worker shutdown after publication.
    heartbeat_interval_seconds : int
        Heartbeat interval included in the derived response timeout.

    Attributes
    ----------
    response_timeout_seconds : int
        Derived Conductor ``responseTimeoutSeconds`` value.

    Raises
    ------
    TaskDefinitionError
        If the LakeFS merge timeout is smaller than the observed latency plus
        safety margin.

    Notes
    -----
    Publish budgets are only valid for workspace tasks. Workspace-free tasks
    reject ``TaskControls(publish_budget=...)`` during task definition
    validation.

    Examples
    --------
    >>> budget = PublishBudget(
    ...     observed_merge_p99_seconds=20,
    ...     safety_margin_seconds=10,
    ...     lakefs_merge_timeout_seconds=45,
    ...     conductor_completion_timeout_seconds=15,
    ...     worker_shutdown_grace_seconds=30,
    ...     heartbeat_interval_seconds=10,
    ... )
    >>> budget.response_timeout_seconds
    100
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_merge_p99_seconds: int = Field(ge=0)
    safety_margin_seconds: int = Field(ge=0)
    lakefs_merge_timeout_seconds: int = Field(ge=1)
    conductor_completion_timeout_seconds: int = Field(ge=1)
    worker_shutdown_grace_seconds: int = Field(ge=1)
    heartbeat_interval_seconds: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_merge_timeout_budget(self) -> PublishBudget:
        """Validate that the LakeFS merge timeout covers the observed budget.

        Returns
        -------
        PublishBudget
            The validated model.

        Raises
        ------
        TaskDefinitionError
            If the merge timeout is smaller than the observed latency plus
            safety margin.
        """
        required_merge_timeout = self.observed_merge_p99_seconds + self.safety_margin_seconds
        if self.lakefs_merge_timeout_seconds < required_merge_timeout:
            raise TaskDefinitionError(
                "lakefs_merge_timeout_seconds must cover observed_merge_p99_seconds plus safety_margin_seconds"
            )
        return self

    @property
    def response_timeout_seconds(self) -> int:
        """Derived Conductor response timeout for workspace publication."""
        return (
            self.lakefs_merge_timeout_seconds
            + self.conductor_completion_timeout_seconds
            + self.worker_shutdown_grace_seconds
            + self.heartbeat_interval_seconds
        )


class TaskControls(BaseModel):
    """Task-level controls consumed by TaskDef generation and runtime code.

    ``TaskControls`` is the only public entry point for retry, timeout,
    execution limit, and publish budget controls. Task authors pass it to
    :func:`perago.task`; Perago expands the supported fields into the generated
    Conductor TaskDef.

    Parameters
    ----------
    retry : RetryPolicy, optional
        Retry controls. Defaults to ``RetryPolicy()``.
    timeout : TimeoutPolicy, optional
        General timeout controls. Defaults to ``TimeoutPolicy()``.
    limits : ExecutionLimits, optional
        Optional concurrency and rate-limit controls. Defaults to
        ``ExecutionLimits()``.
    publish_budget : PublishBudget or None, default=None
        Workspace publication budget. Only workspace tasks may configure it.

    Attributes
    ----------
    response_timeout_seconds : int
        Effective response timeout used for TaskDef generation. This comes from
        ``publish_budget`` when present, otherwise from ``timeout``.

    Examples
    --------
    >>> TaskControls(
    ...     retry=RetryPolicy(count=4),
    ...     timeout=TimeoutPolicy(response_seconds=900),
    ...     limits=ExecutionLimits(concurrent_exec_limit=2),
    ... )
    TaskControls(...)
    """

    model_config = ConfigDict(extra="forbid")

    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    publish_budget: PublishBudget | None = None

    @property
    def response_timeout_seconds(self) -> int:
        """Effective response timeout for the generated Conductor TaskDef."""
        if self.publish_budget is not None:
            return self.publish_budget.response_timeout_seconds
        return self.timeout.response_seconds


class WorkspaceSpec(BaseModel):
    """Workspace declaration for a workspace task.

    ``WorkspaceSpec`` tells Perago which LakeFS object prefix should be
    projected into the local attempt workspace and which guardrails should run
    before and after the task body.

    Parameters
    ----------
    prefix : str, default="/"
        Workspace object prefix. ``"/"`` maps the whole repository root; other
        values are normalized to relative POSIX-style prefixes without a
        leading slash.
    pre : list of workspace guardrails, optional
        Guardrails evaluated after workspace download and before the task body.
    post : list of workspace guardrails, optional
        Guardrails evaluated after the task body and before publication.

    Raises
    ------
    TaskDefinitionError
        If ``prefix`` is empty, escapes the repository, or uses backslash
        separators.

    Notes
    -----
    ``WorkspaceSpec`` is frozen and rejects unknown fields. Guardrails are
    runtime metadata; they are not serialized into generated Conductor
    TaskDefs.

    Examples
    --------
    >>> WorkspaceSpec(prefix="/audio/render", pre=[require_file("raw/manifest.json")])
    WorkspaceSpec(...)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prefix: str = "/"
    pre: list[_WorkspaceGuardrail] = Field(default_factory=list)
    post: list[_WorkspaceGuardrail] = Field(default_factory=list)

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        """Normalize and validate a workspace object prefix.

        Parameters
        ----------
        value : str
            Prefix value supplied to ``WorkspaceSpec``.

        Returns
        -------
        str
            Normalized prefix, with ``"/"`` preserved as the repository root
            marker and other values stripped of leading slashes.

        Raises
        ------
        TaskDefinitionError
            If the prefix is empty, escapes the repository, or uses backslash
            separators.
        """
        if "\\" in value:
            raise TaskDefinitionError("WorkspaceSpec.prefix must use '/' separators")
        stripped = value.strip()
        if stripped == "/":
            return "/"
        normalized = stripped.lstrip("/")
        if not normalized:
            raise TaskDefinitionError("WorkspaceSpec.prefix must not be empty")
        parts = normalized.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise TaskDefinitionError("WorkspaceSpec.prefix must stay inside the repository")
        return "/".join(parts)


class WorkspaceRef(BaseModel):
    """Base model for Conductor workspace references."""

    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    ref_type: Literal["commit"]
    ref: str = Field(min_length=1)

    @field_validator("repository", "branch", "ref")
    @classmethod
    def validate_non_blank_ref_fields(cls, value: str) -> str:
        """Validate non-blank workspace reference strings.

        Parameters
        ----------
        value : str
            Repository, branch, or commit ref field value.

        Returns
        -------
        str
            The original non-blank value.

        Raises
        ------
        TaskDefinitionError
            If the value is blank.
        """
        if not value.strip():
            raise TaskDefinitionError("workspace repository, branch, and ref must not be blank")
        return value


class WorkspaceInput(WorkspaceRef):
    """Workspace reference supplied in Conductor task input.

    ``WorkspaceInput`` identifies the LakeFS repository, target branch, and
    immutable commit ref that a workspace task should download before running
    the task body.

    Parameters
    ----------
    repository : str
        LakeFS repository name. Blank strings are rejected.
    branch : str
        Target branch that successful publication should advance. Blank strings
        are rejected.
    ref_type : {"commit"}
        Type of input reference. Perago currently accepts immutable commit
        references only.
    ref : str
        Input commit ref to download. Blank strings are rejected.

    See Also
    --------
    WorkspaceOutput : Workspace reference returned after publication.

    Examples
    --------
    >>> input_ref = WorkspaceInput(
    ...     repository="song-000123",
    ...     branch="main",
    ...     ref_type="commit",
    ...     ref="589f8770",
    ... )
    >>> input_ref.published_output("9c6f8770").ref
    '9c6f8770'
    """

    def published_output(self, ref: str) -> WorkspaceOutput:
        """Return a workspace output that preserves repository and branch.

        Parameters
        ----------
        ref : str
            Published LakeFS commit ref.

        Returns
        -------
        WorkspaceOutput
            Output reference with the same repository and branch and with
            ``ref_type`` set to ``"commit"``.
        """
        return WorkspaceOutput.model_validate(
            {
                **self.model_dump(mode="json"),
                "ref_type": "commit",
                "ref": ref,
            }
        )


class WorkspaceOutput(WorkspaceRef):
    """Workspace reference returned after successful publication.

    ``WorkspaceOutput`` is generated by the runtime for completed workspace
    tasks. It preserves the input repository and branch, and points ``ref`` to
    the LakeFS commit produced by publication.

    Parameters
    ----------
    repository : str
        LakeFS repository name.
    branch : str
        Branch that was successfully advanced.
    ref_type : {"commit"}
        Type of output reference. Perago currently emits commit references.
    ref : str
        Published commit ref.
    """


def validate_worker_id_prefix(value: str) -> str:
    if not value:
        raise TaskDefinitionError("PERAGO_WORKER_ID_PREFIX must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise TaskDefinitionError("PERAGO_WORKER_ID_PREFIX must contain only ASCII letters and digits")
    return value
