---
name: perago-runtime-configuration
description: Manage Perago runtime configuration boundaries. Use when adding, changing, reviewing, or documenting runtime settings, environment variables, local worker defaults, supervisor/executor propagation, timeout/polling knobs, logging/workspace paths, failure reason limits, or any value that might be configured through RuntimeConfig or .env.
---

# Perago Runtime Configuration

## Purpose

Keep runtime configuration in one ownership chain: environment input is parsed into `RuntimeConfig`, then concrete typed values are explicitly passed into runtime components. Do not let defaults, env names, or operator-facing error messages drift across execution code, tests, and docs.

## Core Rule

Default values belong at the configuration boundary, not inside execution helpers.

Use this shape:

1. Define any default constant in `src/perago/config.py` when the value is a runtime config default.
2. Parse `.env` and process environment in `load_runtime_config(...)` or a focused `parse_*` helper.
3. Store the typed value on `RuntimeConfig`.
4. Pass `config.<field>` explicitly through supervisor, broker, worker, executor, and runtime functions.
5. Make lower-level runtime functions require the value when they need it. Do not give those functions their own default fallback.

Avoid this shape:

```python
def execute_polled_task(..., failure_reason_max_length: int = 500):
    ...
```

Prefer this shape:

```python
def execute_polled_task(..., failure_reason_max_length: int):
    ...
```

The direct caller must pass a value that came from `RuntimeConfig` or from an explicit test fixture.

## Adding a Runtime Setting

1. Decide whether the value is truly runtime-local.
   - Runtime-local values affect worker process behavior, logging, workspace roots, Conductor/LakeFS connections, polling, shutdown, GC, or result reporting.
   - Task author contract values belong in task models or TaskDef controls, not `RuntimeConfig`.
   - Business payload values belong in task params/result models, not environment variables.

2. Add the config field in `src/perago/config.py`.
   - Use a typed `RuntimeConfig` field.
   - Add a `DEFAULT_*` constant only if the setting has an environment default.
   - Add a focused `parse_*` helper for non-trivial validation.
   - Keep `.env < process environment` precedence by using `load_runtime_env(...)`.

3. Parse and validate at the boundary.
   - Empty or missing optional env values should resolve in the parser, not in consumers.
   - Invalid configured values should raise `RuntimeConfigError`.
   - Reject partial config sets for coupled settings, as LakeFS config does.
   - Treat placeholders such as `replace-me` as invalid for connection secrets and endpoints.

4. Thread the typed value explicitly.
   - `run_worker_supervisor(...)` and `_thread_runner_main(...)` pass `config.<field>`.
   - Process mode passes through broker and executor entry points as needed.
   - `PeragoThreadWorker`, `PeragoProcessDispatchWorker`, `run_process_executor_loop`, and `execute_polled_task` should store or receive explicit values, not re-read env.
   - Task execution helpers should receive explicit values from the runtime adapter. Direct tests may use local helper wrappers to provide test defaults.

5. Update docs and tests in the same change.
   - `docs/reference/environment-variables.md` is the precise env-var table.
   - `docs/runtime/configuration.md` is the runtime explanation page.
   - If the setting affects input/output or Conductor status, update the relevant reference/runtime page.
   - `tests/test_config.py` must cover default, `.env`, process-env override, and invalid values.
   - Runtime propagation tests should assert that the parsed value is passed through, not that a callee falls back.

## Environment Variable Rules

- Prefix Perago-owned worker-local variables with `PERAGO_`.
- Use uppercase snake case: `PERAGO_FAILURE_REASON_MAX_LENGTH`.
- Name variables by the runtime concept, not by one call site.
- Keep external tool variables in their native namespace when Perago is consuming that tool's established config, such as `CONDUCTOR_SERVER_URL` and `LAKECTL_*`.
- Empty strings mean "not configured" only where the parser explicitly accepts that behavior.
- Do not read environment variables in execution functions. Read them in `load_runtime_config(...)` or worker-runtime preparation code only.

## Default Ownership

Use this decision table:

| Value type | Default owner | Consumer behavior |
| --- | --- | --- |
| Env-configurable runtime value | `src/perago/config.py` parser / `RuntimeConfig` | Required explicit argument |
| Internal timing constant not user-configurable | Owning runtime module constant | Use named constant, not magic number |
| Task author public default | Pydantic model / task control model | Document with model docs and TaskDef reference |
| Test fixture data | Test module | Literal values are fine when they improve readability |
| Documentation example data | Documentation page | Literal values are fine if they are clearly examples |

When reviewing a hardcoded value, first classify it using this table. Do not promote every literal to `RuntimeConfig`.

## Propagation Checklist

- No `= DEFAULT_*` or `= 500` fallback on lower-level execution functions for env-configured values.
- No repeated env var name parsing outside `config.py`.
- No `os.environ.get(...)` in task execution paths except established worker identity fallback paths.
- Supervisor, thread runtime, process broker, and process executor receive the setting deliberately.
- Broker-side failure paths use the same configured value as executor-side failure paths.
- Tests include a non-default value that proves propagation.
- Tests do not pass the same keyword twice.

## Documentation Checklist

- `docs/reference/environment-variables.md` states status, default, `RuntimeConfig` field, validation, and operational behavior.
- `docs/runtime/configuration.md` explains the setting only once at runtime level.
- User-facing docs should describe task author APIs, not internal helpers such as `result_for_exception`.
- Internal classes/functions may be mentioned in architecture/runtime docs, but should not be added to public API autosummary unless they are intended for task authors.
- If a default changes, update docs and tests in the same commit.

## Verification

For runtime configuration changes, run the smallest relevant set first:

```bash
rtk uv run pytest -q tests/test_config.py tests/test_conductor_runtime.py tests/test_supervisor.py
```

If execution or result mapping changes, add:

```bash
rtk uv run pytest -q tests/test_result.py tests/test_execution.py
```

Before finishing documentation or public API changes, run:

```bash
rtk git diff --check
rtk uv run --with-requirements docs/requirements.txt sphinx-build -W -b html docs /tmp/perago-docs-runtime-config
```

For mechanical call-site edits, run an AST duplicate-keyword check:

```bash
rtk uv run python - <<'PY'
import ast
from pathlib import Path

for path in [*Path("src").glob("perago/**/*.py"), *Path("tests").glob("test_*.py")]:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            names = [kw.arg for kw in node.keywords if kw.arg is not None]
            duplicates = sorted({name for name in names if names.count(name) > 1})
            if duplicates:
                print(f"{path}:{node.lineno}: duplicate keywords {duplicates}")
PY
```
