# Real Conductor/LakeFS Smoke Issues

This file records every issue hit while building and running `scripts/real_conductor_lakefs_smoke.py`.
Use it as the source list for local mock regression tests.

## 1. Smoke runner was hidden under `.cache`

- Symptom: the first real smoke script lived at `.cache/real_conductor_lakefs_smoke.py`, outside normal git tracking.
- Root cause: treated the E2E as a temporary probe instead of a reusable integration smoke.
- Fix: moved the runner to `scripts/real_conductor_lakefs_smoke.py`.
- Regression target: keep the smoke runner in a tracked path and runnable as a normal Python script.

## 2. Worker module was dynamically generated

- Symptom: the runner wrote a Python task module into a temporary directory before starting `perago start`.
- Root cause: the initial smoke collapsed fixture creation and orchestration into one dynamic script.
- Fix: added the tracked worker module `scripts/perago_smoke_worker.py`.
- Regression target: smoke tests should import a stable task module, not a generated file.

## 3. TaskDef registration bypassed `perago extract`

- Symptom: the runner called `build_taskdef()` directly and registered the resulting object.
- Root cause: tested the lower-level function instead of the public CLI path that users rely on.
- Fix: `extract_task_def()` now invokes `perago extract scripts.perago_smoke_worker --out <tmp>` and registers the generated JSON.
- Regression target: local mock should assert the runner uses the CLI extract artifact as the TaskDef source.

## 4. WorkflowDef had no typed schema

- Symptom: the generated WorkflowDef only had `inputParameters` and `outputParameters`.
- Root cause: reused Conductor's minimal workflow fields and did not attach the same contract enforced by the TaskDef.
- Fix: `workflow_def()` now attaches `input_schema`, `output_schema`, and `enforce_schema=True`, reusing the schema from the extracted TaskDef.
- Regression target: mock WorkflowDef registration should assert schema presence and `enforce_schema=True`.

## 5. Direct script execution could not import `scripts`

- Symptom: `uv run python scripts/real_conductor_lakefs_smoke.py` failed with `ModuleNotFoundError: No module named 'scripts'`.
- Root cause: direct script execution sets `sys.path[0]` to `scripts/`, not the repository root.
- Fix: the runner inserts the repository root into `sys.path` and passes it as `PYTHONPATH` to the worker subprocess.
- Regression target: execute the runner entrypoint from the repository root without `python -m`.

## 6. LakeFS rejected staging branch names

- Symptom: real Conductor task attempts failed with LakeFS `400 Bad Request`: branch id must consist of letters, digits, underscores and dashes.
- Root cause: `staging_branch_name()` used slash-separated path-like names with `=` fields.
- Fix: staging branch names now use a LakeFS branch-id-safe format containing only letters, digits, underscores, and dashes.
- Regression target: metadata tests assert the staging branch name character set and exact attempt-scoped format.

## 7. Smoke timeout path could block while reading worker stdout

- Symptom: after a long-running smoke attempt, the runner did not return promptly from the timeout path.
- Root cause: `wait_workflow()` called `dump_worker_output()` while the worker process could still be alive, so reading from the pipe could block.
- Fix: timeout now calls `stop_process()` before reading stdout.
- Regression target: local mock should cover a timed-out worker and assert the runner terminates before draining output.

## 8. LakeFS fake assumed path-like staging branch names

- Symptom: after fixing real LakeFS branch names, `tests/test_lakefs_runtime.py` failed because the fake only treated branches starting with `perago/staging` as staging branches.
- Root cause: the fake encoded the old invalid branch-name shape, so local tests did not model LakeFS's branch-id constraints.
- Fix: the fake now recognizes `perago-staging-` branch ids and asserts the staged branch equals `staging_branch_name(attempt)`.
- Regression target: local mock tests should fail if staging branch naming drifts away from the LakeFS-safe format.

## 9. Worker subprocess could load stale installed package code

- Symptom: after fixing `staging_branch_name()`, a real smoke run still failed with the old slash-separated branch name.
- Root cause: the package was not installed editable, so the public `perago` command could resolve stale installed code instead of current workspace changes.
- Fix: install the project with `uv pip install -e .`; the runner only prepends `<repo>` so the tracked `scripts.*` worker module is importable.
- Regression target: integration setup should install editable before smoke, and local mock should verify the runner does not depend on importing `perago` via `PYTHONPATH=src`.

## 10. Attempt workspace cleanup left empty parent directories

- Symptom: local cleanup removed the deepest attempt directory contents but left empty parents such as `task_id=<id>`.
- Root cause: `cleanup_attempt_workspace()` removed only the marker-owned `retry_count=<n>` directory.
- Fix: cleanup now removes empty attempt parent directories up to the workflow attempt root while preserving non-empty siblings and the configured workspace root.
- Regression target: workspace tests assert empty parents are removed and non-empty sibling attempt directories are preserved.

## 11. Smoke runner used the internal module CLI entrypoint

- Symptom: the runner started workers and extracted TaskDefs with `python -m perago.cli`.
- Root cause: tested an implementation module instead of the public command installed by `[project.scripts]`.
- Fix: the runner now resolves and calls the public `perago` executable for both `extract` and `start`.
- Regression target: local mock should assert subprocess commands begin with `perago`, not `python -m perago.cli`.

## 12. Runner could hang after a completed workflow

- Symptom: the real smoke printed `workflow status=COMPLETED`, verified Conductor output and LakeFS output, then did not return promptly.
- Root cause: `perago start` was launched as a normal subprocess; worker children could keep the inherited stdout pipe open after the supervisor was signalled, and `dump_worker_output()` used a blocking pipe read.
- Fix: start `perago start` in a new process group, stop the whole group with SIGTERM/SIGKILL fallback, and drain output via `communicate(timeout=...)`. Completed workflows are no longer terminated in cleanup.
- Regression target: local mock should cover a completed workflow with a still-running worker child and assert the runner stops the process group without blocking.
