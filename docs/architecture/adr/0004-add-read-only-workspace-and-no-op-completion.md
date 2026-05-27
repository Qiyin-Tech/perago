# ADR-0004: Add read-only workspace tasks and no-op completion

**Date**: 2026-05-27
**Status**: accepted
**Deciders**: Perago maintainers

## Context

Perago workspace tasks currently model every successful workspace attempt as a LakeFS publication. That creates a bad edge case: a task may read workspace files without producing workspace changes, or it may be allowed to write but happen to produce no diff for a particular input. Creating LakeFS empty commits for those attempts records executor activity in workspace history, not workspace content change, and can also trigger LakeFS errors. We need a contract that supports read-only workspace nodes and writable-but-no-op nodes without weakening the existing soft-fenced publication model.

## Decision

`WorkspaceSpec` gets an explicit `read_only: bool = False` parameter.

`read_only=True` declares a workspace task that consumes versioned workspace input but never publishes workspace changes. It downloads the workspace, runs the task body and guardrails, returns a `WorkspaceOutput` whose `ref` equals the input `workspace.ref`, and skips diff checks, target HEAD checks, staging branches, LakeFS commits, and publication. It is not an OS-level readonly mount; writes to the attempt-local workspace are discarded during cleanup.

```python
@task(
    name="metadata.inspect",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/audio/render", read_only=True),
)
def inspect_metadata(workspace: Path, params: InspectParams) -> InspectOutput:
    manifest = workspace / "manifest.json"
    return InspectOutput(found=manifest.exists())
```

`read_only=False` remains the default. Writable workspace tasks check whether the local workspace projection changed after the task body and post guardrails:

| State | Runtime behavior | Output ref |
| --- | --- | --- |
| diff is non-empty and `HEAD == input_ref` | stage and merge. | published ref |
| diff is non-empty and `parent(HEAD) == input_ref` | stage and replacement publish to the staged commit. | staged commit |
| diff is empty and `HEAD == input_ref` | complete without staging or committing. | input ref |
| diff is empty and `parent(HEAD) == input_ref` | treat `HEAD` as abandoned publication, relocate target branch back to input ref, then complete. | input ref |
| any other writable HEAD state | fail closed. | none |

Perago does not create LakeFS empty commits. The fact that a node ran belongs to Conductor result state and worker logs, not to LakeFS workspace history.

`TaskControls(publish_budget=...)` remains invalid for workspace-free tasks. If a read-only workspace task configures `publish_budget`, `perago check`, `perago extract`, and `perago start` should emit one warning during validation/startup and ignore the budget; task execution must not warn for every attempt.

```text
WorkspaceSpec(read_only=True) disables workspace publication; TaskControls.publish_budget is ignored.
```

## Alternatives Considered

### Allow LakeFS empty commits

- **Pros**: Minimal runtime branching; every workspace task still returns a new ref.
- **Cons**: Pollutes workspace history with executor activity instead of content changes; does not help read-only nodes; can fail in LakeFS when there are no changes.
- **Why not**: Workspace history should represent workspace content, while node execution belongs to Conductor.

### Infer read-only behavior from an empty diff only

- **Pros**: No new task declaration field.
- **Cons**: Cannot distinguish a node that is intentionally read-only from a writable node that happened to produce no diff; forces read-only nodes into writable HEAD checks.
- **Why not**: Read-only is part of the task's workspace access contract and should be explicit.

### Reject publish budget on read-only workspace tasks

- **Pros**: Keeps configuration strict.
- **Cons**: Turns a harmless stale parameter into a hard failure and makes migrations noisier.
- **Why not**: A startup/check warning is enough because the budget is simply ineffective when publication is disabled.

### Enforce OS-level read-only workspaces

- **Pros**: Catches accidental writes by task code.
- **Cons**: Adds platform-specific filesystem behavior and complicates local cleanup and tests.
- **Why not**: The contract is "no publication", not "immutable local filesystem"; accidental local writes are discarded.

## Consequences

Workspace task no longer means "always writes a new commit." It means "receives a versioned workspace and returns a workspace output." A workspace output may carry the same ref as the input.

Runtime implementation must add a changed-workspace detection point before staging writable tasks, and it must add a no-op branch handling path for writable tasks with empty diffs. Read-only tasks bypass LakeFS publication checks entirely.

Documentation and generated references must consistently distinguish:

- workspace-free tasks: no workspace input/output;
- read-only workspace tasks: workspace input/output, no publication;
- writable no-op completion: workspace input/output, no empty commit, HEAD-state check;
- writable publication: workspace input/output with a new published ref.

This ADR refines ADR-0003. The soft-fenced publication protocol still applies whenever a writable task publishes changes or performs no-op branch reconciliation.
