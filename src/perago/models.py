from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from perago.errors import TaskDefinitionError
from perago.guards import _WorkspaceGuardrail


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=3, ge=0, le=10)
    logic: Literal["FIXED", "EXPONENTIAL_BACKOFF", "LINEAR_BACKOFF"] = "FIXED"
    delay_seconds: int = Field(default=60, ge=0)
    max_delay_seconds: int = Field(default=0, ge=0)
    jitter_ms: int = Field(default=0, ge=0)


class TimeoutPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: Literal["RETRY", "TIME_OUT_WF", "ALERT_ONLY"] = "TIME_OUT_WF"
    seconds: int = Field(default=0, ge=0)
    response_seconds: int = Field(default=600, ge=0)
    poll_seconds: int = Field(default=0, ge=0)
    total_seconds: int = Field(default=0, ge=0)


class ExecutionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concurrent_exec_limit: int | None = Field(default=None, ge=0)
    rate_limit_frequency_in_seconds: int | None = Field(default=None, ge=0)
    rate_limit_per_frequency: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_rate_limit_pair(self) -> ExecutionLimits:
        has_frequency = self.rate_limit_frequency_in_seconds is not None
        has_limit = self.rate_limit_per_frequency is not None
        if has_frequency != has_limit:
            raise TaskDefinitionError(
                "rate_limit_frequency_in_seconds and rate_limit_per_frequency must be configured together"
            )
        return self


class TaskControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)


class PublishBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_changed_objects: int = Field(ge=1)
    max_changed_bytes: int = Field(ge=1)
    observed_merge_p99_seconds: int = Field(ge=0)
    safety_margin_seconds: int = Field(ge=0)
    lakefs_merge_timeout_seconds: int = Field(ge=1)
    conductor_completion_timeout_seconds: int = Field(ge=1)
    worker_shutdown_grace_seconds: int = Field(ge=1)
    heartbeat_interval_seconds: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_merge_timeout_budget(self) -> PublishBudget:
        required_merge_timeout = self.observed_merge_p99_seconds + self.safety_margin_seconds
        if self.lakefs_merge_timeout_seconds < required_merge_timeout:
            raise TaskDefinitionError(
                "lakefs_merge_timeout_seconds must cover observed_merge_p99_seconds plus safety_margin_seconds"
            )
        return self

    @property
    def response_timeout_seconds(self) -> int:
        return (
            self.lakefs_merge_timeout_seconds
            + self.conductor_completion_timeout_seconds
            + self.worker_shutdown_grace_seconds
            + self.heartbeat_interval_seconds
        )


class WorkspaceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prefix: str = "/"
    pre: list[_WorkspaceGuardrail] = Field(default_factory=list)
    post: list[_WorkspaceGuardrail] = Field(default_factory=list)

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
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


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    ref_type: Literal["commit"]
    ref: str = Field(min_length=1)


def validate_worker_id_prefix(value: str) -> str:
    if not value:
        raise TaskDefinitionError("PERAGO_WORKER_ID_PREFIX must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise TaskDefinitionError("PERAGO_WORKER_ID_PREFIX must contain only ASCII letters and digits")
    return value
