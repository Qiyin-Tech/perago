# ADR-0002: Classify workspace guardrail failures by phase

**Date**: 2026-05-20
**Status**: accepted
**Deciders**: Perago maintainers

## Context

Workspace guardrails check local file-shape expectations before the business function runs and after it returns successfully. A missing required pre input usually means an upstream task or workflow wiring violated the current task's input workspace contract. A missing post output means the current task attempt failed to produce the file shape it promised.

## Decision

Perago classifies workspace guardrail failures by phase.

Pre guardrail violations are terminal. The worker attempts local cleanup, reports `FAILED_WITH_TERMINAL_ERROR`, does not call the business function, does not apply the task retry policy, and does not publish a workspace output.

Post guardrail violations are retryable task failures. The worker attempts local cleanup, reports `FAILED`, applies the task retry policy, and does not upload or publish that workspace. A retry reruns the business function from the same deterministic workspace input ref.

Conductor owns retry scheduling. A retry may be picked up by the same Worker Process, a sibling Worker Process, or a Worker Process on another machine. Perago must not rely on local retry state or reuse a failed attempt-local workspace as retry input.

Transient runtime failures remain retryable. LakeFS, Conductor, network, lease, and uncertain publication errors continue to use the normal runtime failure path unless they are explicitly classified as terminal for another reason.

## Consequences

Guardrails must stay limited to stable local workspace expectations.

Task authors should use pre guardrails only for input files that must already exist before the task starts. Task authors may use post guardrails for output files that should be produced by rerunning the same Logical Task under Conductor retry.

Perago must report guardrail path declaration errors separately from runtime guardrail violations. Invalid guardrail paths fail task definition validation during module import and `perago check`; they are not Conductor task failures.

If an attempt-local workspace was created, Perago must attempt to clean it before reporting `COMPLETED`, `FAILED`, or `FAILED_WITH_TERMINAL_ERROR` to Conductor. Cleanup failure is a `loguru` error event and must not change the Conductor result. Startup cleanup may sweep abandoned attempt workspaces before the process polls.

Attempt-local workspace directories live under the local `PERAGO_WORKSPACE_ROOT` path. The root may be configured in `.env`, is parsed as a host file-system path, and is not part of Conductor task input, Conductor task output, TaskDef JSON, or LakeFS workspace identity.
