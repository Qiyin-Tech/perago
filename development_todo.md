# Project Development TODO

## Publish fence commit-range classification

- [ ] Implement the intended publish-fence commit range check, or deliberately narrow the implementation contract.

Current state: `LakeFSWorkspaceRuntime.publish_workspace()` passes only the target branch head commit into `build_workspace_publication_plan()`. The intended project behavior is to classify target-branch advancement by checking the commit range from the input workspace ref to the current head, and accepting only commits attributable to the same `perago.logical_task_key`.

Acceptance criteria:

- Fetch the relevant LakeFS commit range between `WorkspaceInput.ref` and the observed target branch head.
- Pass that range to `build_workspace_publication_plan()` instead of a single head commit.
- Keep unrelated or metadata-incomplete branch advancement fail-closed.
- Cover the behavior with a runtime-level test, not only metadata helper tests.
