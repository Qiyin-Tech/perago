# ADR-0005: Use exceptions for task execution failures

**Date**: 2026-05-27
**Status**: accepted
**Deciders**: Perago maintainers

## Context

Perago task functions already use their return value as the business `Result Output` that is written under Conductor `outputData.result` only when the task completes successfully. A user reported that returning a business payload such as `{"status": "FAIL"}` still allowed the next workflow node to run. That behavior matches the current contract, but it exposed an API gap: task authors need a clear way to declare execution failures without overloading business result fields.

Conductor distinguishes retryable `FAILED` task results from non-retryable `FAILED_WITH_TERMINAL_ERROR` task results. It also supports `outputData`, but failed Perago task results intentionally do not expose business output to downstream nodes.

## Decision

Perago uses task return values for successful business results and exceptions for task execution failures.

Task authors should raise `TaskFailed("...")` for execution failures where retrying the same input may succeed. Perago reports those attempts as Conductor `FAILED`.

Task authors should raise `TaskTerminalError("...")` for detectable execution failures where retrying the same input has no value. Perago reports those attempts as Conductor `FAILED_WITH_TERMINAL_ERROR`.

Business-recoverable outcomes that should be handled by workflow logic, such as prompt policy rejection or missing user-provided information, remain successful task results. The task returns a structured `Result Output`, and WorkflowDef branching handles the business state.

Failure reasons are strings. Perago caps the text written to Conductor `reasonForIncompletion` with a configurable `PERAGO_FAILURE_REASON_MAX_LENGTH` limit and records truncation details in worker JSONL logs rather than putting structured JSON into failed task output.

## Alternatives Considered

### Alternative 1: Return a failure object from the task function

- **Pros**: Lets task authors express success and failure without Python exceptions.
- **Cons**: Makes the return annotation represent both success data and runtime control flow, complicates Pydantic output models and TaskDef output schemas, and conflicts with the existing `Result Output` glossary.
- **Why not**: Perago keeps success data and runtime failure control separate. `return Output(...)` means the task completed.

### Alternative 2: Treat business status fields such as `status="FAIL"` as Conductor failures

- **Pros**: Matches some business payload conventions.
- **Cons**: Requires Perago to understand arbitrary business schemas, makes common fields like `status` reserved or ambiguous, and breaks typed task contracts.
- **Why not**: Business schemas belong to the task author and workflow. Perago should not infer Conductor lifecycle state from business result fields.

### Alternative 3: Put structured JSON in failed `outputData`

- **Pros**: Could carry machine-readable failure metadata.
- **Cons**: Failed tasks normally do not feed downstream business nodes, Conductor `reasonForIncompletion` is the primary operator-facing field, and Perago would need another schema contract for failed outputs.
- **Why not**: The MVP only needs a reason string for Conductor failure state. Structured business recovery data belongs in successful `Result Output` and workflow branches.

## Consequences

### Positive

- Task authors have explicit APIs for retryable and terminal execution failures.
- `Result Output` remains a successful business result instead of a union of success data and runtime control signals.
- WorkflowDef branching remains responsible for business-recoverable outcomes.
- Workspace publication stays fail-closed: failed task attempts do not stage or publish local workspace changes.

### Negative

- Task authors must choose between business branch output and execution failure exceptions.
- Terminal errors become a public API concept and documentation must explain when automatic retry is inappropriate.
- The runtime must preserve ordinary unhandled exceptions as retryable `FAILED` results while mapping explicit terminal errors separately.

### Risks

- **Risk**: Authors may overuse `TaskTerminalError` for cases that product workflow should recover from.
  **Mitigation**: Documentation uses the failure quadrant and examples to separate business branches from execution failures.
- **Risk**: Authors may expect structured failed outputs.
  **Mitigation**: Perago keeps failed results to `status` plus `reasonForIncompletion`; structured state should be returned only for successful business branch outputs.
