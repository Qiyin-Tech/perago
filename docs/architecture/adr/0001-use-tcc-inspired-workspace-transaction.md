# ADR-0001: Use TCC-inspired workspace transaction for LakeFS publication

**Date**: 2026-05-19
**Status**: superseded by [ADR-0003](0003-use-soft-fenced-lakefs-publication-protocol.md)
**Deciders**: Perago maintainers

> 历史记录。当前 LakeFS 发布规则以 ADR-0003 和 [LakeFS 发布协议](../../lakefs-publication-protocol.md) 为准。

## Context

Perago workspace workers read an immutable LakeFS commit and publish a new commit to a workflow-carried target branch. The MVP targets LakeFS Community/OSS and must not rely on Enterprise-only behavior such as async merge. Workflow definitions are serial for workspace writes: parallel branches that write the same LakeFS branch are forbidden. Perago also assumes only one active workflow instance writes a given LakeFS branch at a time. Conductor retries are expected, and a worker process can die after publishing to LakeFS but before local cleanup and Conductor completion. Multiple worker processes may also poll independently, so runtime publication cannot rely on an in-process task pool or shared memory.

The business function should stay simple: it receives a local `Path` and typed params, returns a typed result, and does not implement transaction callbacks.

## Decision

Perago uses a TCC-inspired workspace transaction around workspace publication. The runtime performs Try by writing to a short-lived staging branch, Confirm by passing Conductor and LakeFS publication fences before squash merging into the target branch, and Cancel by deleting the staging branch when the attempt fails, becomes stale, or finishes. Target workspace branches should be protected LakeFS branches so workspace updates can reach them only through merge.

The Conductor attempt fence is implemented by re-checking the active task attempt before publishing. For the same logical task under normal Conductor retry semantics, two current attempts should not both pass that fence. The LakeFS publish fence is still client-side in the MVP and therefore soft for the attempt crash window: the Python SDK does not expose an atomic expected-destination-head compare-and-swap on merge.

LakeFS Community pre-merge hooks/actions may be used as fast gates to abort invalid merges. They must not be used as long-running lock waits. Treat hook-based expected-head validation as an optional hard-fence candidate until it is proven by integration test against the deployed LakeFS Community version.

The MVP accepts the soft-fence model. Perago defines an operational maximum LakeFS merge time as part of a publish budget and uses that budget to size the LakeFS merge request timeout, Conductor completion reserve, heartbeat slack, and shutdown grace period. The task-level `responseTimeoutSeconds` remains a separate lease timeout and must cover the full attempt lifecycle; TaskDef generation warns when it is shorter than the derived publish budget. The publish budget is a timing boundary, not a changed-object or changed-byte quota. If timeout or metadata checks cannot classify a publish outcome, Perago fails closed instead of trying to prove exactly-once recovery. The MVP does not take over the SDK `TaskRunner` completion update path.

## Considered Options

### Direct writes to the target branch

- **Pros**: Smallest implementation.
- **Cons**: Concurrent or stale attempts can leave uncommitted or committed changes directly on the target branch.
- **Why not**: It does not isolate Try from Confirm and makes retries unsafe.

### XA or AT-style distributed transaction

- **Pros**: Stronger transactional framing where supported.
- **Cons**: Conductor task completion and LakeFS branch publication do not participate in a shared XA resource manager or AT undo-log proxy.
- **Why not**: The required resource-manager capabilities are not available at the Perago boundary.

### Saga-style business compensation

- **Pros**: Fits long-running distributed workflows.
- **Cons**: Requires business-defined compensation or recovery logic.
- **Why not**: Perago's API goal is that task authors write a normal typed Python function, not transaction participants.

### TCC-inspired runtime transaction

- **Pros**: Isolates unconfirmed workspace writes, keeps user code simple, and maps cleanly to LakeFS branches and commits.
- **Cons**: Without a server-side expected-head check or external ledger, the LakeFS publish fence remains a soft client-side check.
- **Why chosen**: It gives the MVP the right idempotency shape without exposing transaction methods to business code.

## Consequences

Workspace workers never write directly to the workflow target branch. They write to a staging branch first and publish through a squash merge, producing a linear target-branch history for successful publications.

Perago must attach transaction metadata to LakeFS commits so retries can distinguish a previous lost attempt for the same logical task from unrelated branch advancement.

The MVP accepts that if a process dies after LakeFS merge but before local cleanup and Conductor completion, Conductor may retry and Perago may recompute the result. The later retry publishes a new linear commit that records the previous commit as superseded.

If the soft-fence assumptions are violated and Perago cannot classify the LakeFS state from metadata, the workflow is allowed to fail. Recovery is to start a new workflow from the current protected branch head. If strict exactly-once publication and result recovery become requirements, Perago needs either a proven LakeFS Community server-side hard publish fence or a Perago-owned external transaction ledger with durable result records and reconciliation from LakeFS commit metadata.
