# Perago MVP Examples

This document records the expected first-demo user-facing shape. It is not an implementation architecture document.

## Task module

A task worker lives in a Python module that declares exactly one Perago task.

### Workspace task

```python
# app/workers/features_build.py

from pathlib import Path

from pydantic import BaseModel, Field

from perago import (
    ExecutionLimits,
    RetryPolicy,
    TaskControls,
    TimeoutPolicy,
    WorkspaceSpec,
    forbid_glob,
    require_dir,
    require_glob,
    task,
)


class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)


class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)


@task(
    name="features.build",
    description="Build feature parquet files.",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/",
        pre=[
            require_dir("raw"),
            require_glob("raw/**/*.parquet", min_count=1),
        ],
        post=[
            require_dir("features"),
            require_glob("features/**/*.parquet", min_count=1),
            forbid_glob("**/*.tmp"),
        ],
    ),
    controls=TaskControls(
        retry=RetryPolicy(
            count=3,
            logic="FIXED",
            delay_seconds=60,
            max_delay_seconds=0,
            jitter_ms=0,
        ),
        timeout=TimeoutPolicy(
            policy="TIME_OUT_WF",
            seconds=0,
            response_seconds=600,
            poll_seconds=0,
            total_seconds=0,
        ),
        limits=ExecutionLimits(
            concurrent_exec_limit=None,
            rate_limit_frequency_in_seconds=None,
            rate_limit_per_frequency=None,
        ),
        publish_budget=None,
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    # Business code only sees local paths and typed Python objects.
    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

### Workspace-free task

```python
# app/workers/metadata_validate.py

from pydantic import BaseModel, Field

from perago import task


class ValidateMetadataParams(BaseModel):
    song_id: str
    min_duration_seconds: int = Field(ge=1)


class ValidateMetadataOutput(BaseModel):
    valid: bool
    reason: str | None = None


@task(
    name="metadata.validate",
    description="Validate song metadata.",
    owner_email="data@example.com",
)
def validate_metadata(
    params: ValidateMetadataParams,
) -> ValidateMetadataOutput:
    return ValidateMetadataOutput(valid=True)
```

The function signature is the only source for the input and output model.

- `params: BuildFeaturesParams` defines the task input payload model.
- `-> BuildFeaturesOutput` defines the task output payload model.
- `workspace: Path`, when present, is a Perago-managed workspace injection, not a duplicated schema declaration.
- Workspace task Conductor input is wrapped as `{ "workspace": ..., "params": ... }`.
- Workspace-free task Conductor input is wrapped as `{ "params": ... }`.
- The MVP workspace input carries a LakeFS repository and ref, not LakeFS connection settings or a workspace prefix.

The MVP only supports these exact function shapes:

```python
# Workspace task.
def task_fn(
    workspace: Path,
    params: ParamsModel,
) -> OutputModel:
    ...


# Workspace-free task.
def task_fn(
    params: ParamsModel,
) -> OutputModel:
    ...
```

The decorator rejects incompatible functions when the module is imported, and `perago check` reports the same rule in a CLI-friendly way.

```python
# Not supported: wrong parameter names.
@task(name="features.build", workspace=WorkspaceSpec(prefix="/"))
def build_features(
    path: Path,
    input: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    ...


# Not supported: expanded business fields.
@task(name="features.build", workspace=WorkspaceSpec(prefix="/"))
def build_features(
    workspace: Path,
    feature_set: str,
    min_rows: int,
) -> BuildFeaturesOutput:
    ...


# Not supported in the MVP: extra injected context parameter.
@task(name="features.build", workspace=WorkspaceSpec(prefix="/"))
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
    context: TaskContext,
) -> BuildFeaturesOutput:
    ...
```

The decorator validation is based on `inspect.signature()` and `typing.get_type_hints()`:

- workspace task: exactly two positional-or-keyword parameters
- workspace task: first parameter is named `workspace`
- workspace task: second parameter is named `params`
- workspace task: `workspace` is annotated as `pathlib.Path`
- workspace-free task: exactly one positional-or-keyword parameter named `params`
- both task types: `params` is annotated as a Pydantic `BaseModel` subclass
- return annotation is a Pydantic `BaseModel` subclass
- no `*args`, `**kwargs`, keyword-only contract fields, or untyped parameters

The same validation function is used in two places:

- `@task(...)` validates immediately when the module is imported and raises `TaskDefinitionError` for invalid task definitions.
- `perago check` imports the module, catches task definition errors, and reports them as CLI diagnostics.

The decorator must not repeat the contract:

```python
# Not supported in the MVP.
@task(
    name="features.build",
    params=BuildFeaturesParams,
    output=BuildFeaturesOutput,
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    ...
```

The decorator owns TaskDef control fields through `controls=TaskControls(...)`, but not generated contract fields:

```python
@task(
    name="features.build",
    description="Build feature parquet files.",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/"),
    controls=TaskControls(
        retry=RetryPolicy(
            count=3,
            logic="FIXED",
            delay_seconds=60,
            max_delay_seconds=0,
            jitter_ms=0,
        ),
        timeout=TimeoutPolicy(
            policy="TIME_OUT_WF",
            seconds=0,
            response_seconds=600,
            poll_seconds=0,
            total_seconds=0,
        ),
        limits=ExecutionLimits(
            concurrent_exec_limit=None,
            rate_limit_frequency_in_seconds=None,
            rate_limit_per_frequency=None,
        ),
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    ...
```

`TaskControls`, `RetryPolicy`, `TimeoutPolicy`, `ExecutionLimits`, and `PublishBudget` are Pydantic models. Their validation errors are surfaced through the same import-time task validation and `perago check` diagnostics as task signature errors.

Perago maps these fields to Conductor TaskDef fields:

| Perago field | Conductor field | Required | Default |
| --- | --- | --- | --- |
| `name` | `name` | yes | none |
| `owner_email` | `ownerEmail` | yes | none |
| `workspace` | none | conditional | none |
| `controls` | none | no | `TaskControls()` |
| `description` | `description` | no | `None` |
| `controls.retry.count` | `retryCount` | no | `3` |
| `controls.retry.logic` | `retryLogic` | no | `"FIXED"` |
| `controls.retry.delay_seconds` | `retryDelaySeconds` | no | `60` |
| `controls.retry.max_delay_seconds` | `maxRetryDelaySeconds` | no | `0` |
| `controls.retry.jitter_ms` | `backoffJitterMs` | no | `0` |
| `controls.timeout.total_seconds` | `totalTimeoutSeconds` | no | `0` |
| `controls.timeout.policy` | `timeoutPolicy` | no | `"TIME_OUT_WF"` |
| `controls.timeout.seconds` | `timeoutSeconds` | no | `0` |
| `controls.timeout.response_seconds` | `responseTimeoutSeconds` | no | `600` |
| `controls.timeout.poll_seconds` | `pollTimeoutSeconds` | no | `0` |
| `controls.limits.concurrent_exec_limit` | `concurrentExecLimit` | no | `None` |
| `controls.limits.rate_limit_frequency_in_seconds` | `rateLimitFrequencyInSeconds` | no | `None` |
| `controls.limits.rate_limit_per_frequency` | `rateLimitPerFrequency` | no | `None` |
| `controls.publish_budget` | derives `responseTimeoutSeconds` | workspace tasks only | `None` |

Fields set to `None` are omitted from the extracted TaskDef JSON.

If `controls.publish_budget` is set, Perago derives `responseTimeoutSeconds` from `PublishBudget.response_timeout_seconds` instead of `controls.timeout.response_seconds`. At runtime, the same budget provides the LakeFS merge request timeout and a Conductor completion reserve inside `responseTimeoutSeconds`; it is not wired to the SDK `TaskRunner` result-update HTTP timeout. The publish budget itself is local runtime configuration and is not emitted into TaskDef JSON.

`TaskControls.response_timeout_seconds` is the single local source used for the generated TaskDef `responseTimeoutSeconds` value.

`workspace` is required for workspace task workers and forbidden for workspace-free task workers.
`controls.publish_budget` is valid only for workspace task workers.

The two rate limit fields must be configured together. `perago check` fails if only one of `controls.limits.rate_limit_frequency_in_seconds` or `controls.limits.rate_limit_per_frequency` is set.

Perago does not expose Conductor `inputTemplate` in the MVP. Pydantic field defaults remain part of the generated JSON Schema, but Perago does not copy them into TaskDef `inputTemplate`.

Perago generates these TaskDef fields and does not accept them in the decorator:

- `inputKeys`
- `outputKeys`
- `inputSchema`
- `outputSchema`
- `inputTemplate`

## CLI target syntax

MVP commands accept a Python module import path. They do not accept file paths, object paths, `module:app` targets, or task-selection flags.

The `perago` command is a Typer CLI app. The MVP must not implement the CLI as a `main.py` script or an ad-hoc argparse wrapper.

```python
# src/perago/cli.py

from pathlib import Path

import typer


app = typer.Typer(no_args_is_help=True)


@app.command()
def check(module_target: str) -> None:
    ...


@app.command()
def extract(module_target: str, output: Path) -> None:
    ...


@app.command()
def start(module_target: str, j: int = 1) -> None:
    ...
```

```bash
perago check app.workers.features_build
perago extract app.workers.features_build --output generated/features.build.json
perago start app.workers.features_build -j 4
```

`perago extract` writes one generated Conductor TaskDef JSON file to the explicit output path:

```text
generated/
  features.build.json
```

Schemas are embedded inside the generated task definition JSON. The MVP does not emit standalone schema files.

## Rejected MVP shapes

```bash
perago check app/workers/features_build.py
perago check app.workers.features_build:build_features
perago extract app.workers:app
perago start app.workers:app --task features.build
perago start app.workers:app --tasks features.build,model.train
python main.py check app.workers.features_build
```

## Single-task rule

For every command above, Perago imports the module and expects exactly one registered task.

- zero registered tasks: fail
- one registered task: continue
- more than one registered task: fail

## Worker process count

`perago start` defaults to one worker process. Passing `-j` starts that many independent worker processes under a supervisor.

```bash
perago start app.workers.features_build
perago start app.workers.features_build -j 1
perago start app.workers.features_build -j 4
```

Each worker process loads the same task module and polls Conductor independently. The supervisor does not dispatch tasks internally; Conductor remains the only task source.

If a child worker process exits unexpectedly, the supervisor restarts that child. Other worker processes keep running. A single child failure must not terminate the whole `perago start -j` process group.

Supervisor restart behavior:

- restart only the failed child process
- keep other child processes running
- use restart backoff of `1s`, `2s`, `4s`, `8s`, `16s`, then max `30s`
- restart indefinitely until the supervisor is stopped
- on `SIGTERM` or `SIGINT`, stop restarting and gracefully terminate all child processes
- if the task module fails validation before workers are launched, exit immediately instead of entering a restart loop

## Worker identity

The Worker Supervisor assigns one `PERAGO_WORKER_ID` to each child Worker Process before launch. `PERAGO_WORKER_ID` is used as:

- the Conductor worker id when polling and extending leases;
- the log directory segment under `PERAGO_LOG_ROOT`;
- a stable label for process-level operational diagnostics.

`PERAGO_WORKER_ID` is a process identity, not a task identity. It must not be used as a Task Attempt id, Logical Task Key, or workspace publication key.

The supervisor derives `PERAGO_WORKER_ID` from `PERAGO_WORKER_ID_PREFIX` plus the child slot index. `PERAGO_WORKER_ID_PREFIX` is configurable in `.env` to avoid collisions across hosts, deployments, or repeated local runs.

`PERAGO_WORKER_ID_PREFIX` must be a non-empty alphanumeric string: `A-Z`, `a-z`, and `0-9` only. Perago must not normalize, slugify, or replace invalid characters. If the configured prefix contains punctuation, separators, whitespace, or non-ASCII characters, `perago start` and `perago check` fail with a clear runtime configuration error.

If `PERAGO_WORKER_ID_PREFIX` is not configured, the supervisor derives a default alphanumeric prefix from the module target by removing non-alphanumeric characters. This default derivation is only for absent configuration; explicitly configured invalid prefixes still fail.

```python
import os
import re


class RuntimeConfigError(ValueError):
    pass


def validate_worker_id_prefix(value: str) -> str:
    if not value:
        raise RuntimeConfigError("PERAGO_WORKER_ID_PREFIX must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise RuntimeConfigError(
            "PERAGO_WORKER_ID_PREFIX must contain only ASCII letters and digits"
        )
    return value


def default_worker_id_prefix(module_target: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9]+", "", module_target)
    return validate_worker_id_prefix(candidate)


def resolve_worker_id_prefix(module_target: str, env: dict[str, str]) -> str:
    configured = env.get("PERAGO_WORKER_ID_PREFIX")
    if configured is not None:
        return validate_worker_id_prefix(configured.strip())
    return default_worker_id_prefix(module_target)


def worker_id_for_child(prefix: str, index: int) -> str:
    return f"{prefix}{index:04d}"


def child_environment(base_env: dict[str, str], module_target: str, index: int) -> dict[str, str]:
    env = dict(base_env)
    prefix = resolve_worker_id_prefix(module_target, env)
    env["PERAGO_WORKER_ID_PREFIX"] = prefix
    env["PERAGO_WORKER_ID"] = worker_id_for_child(prefix, index)
    return env
```

Examples:

```bash
perago start app.workers.features_build
# PERAGO_WORKER_ID=appworkersfeaturesbuild0001

perago start app.workers.features_build -j 4
# PERAGO_WORKER_ID=appworkersfeaturesbuild0001
# PERAGO_WORKER_ID=appworkersfeaturesbuild0002
# PERAGO_WORKER_ID=appworkersfeaturesbuild0003
# PERAGO_WORKER_ID=appworkersfeaturesbuild0004

PERAGO_WORKER_ID_PREFIX=prodAFeaturesBuild perago start app.workers.features_build -j 2
# PERAGO_WORKER_ID=prodAFeaturesBuild0001
# PERAGO_WORKER_ID=prodAFeaturesBuild0002

PERAGO_WORKER_ID_PREFIX=prod-a-features-build perago check app.workers.features_build
# RuntimeConfigError: PERAGO_WORKER_ID_PREFIX must contain only ASCII letters and digits
```

If a supervised Worker Process exits and is restarted into the same child slot, the supervisor reuses that slot's `PERAGO_WORKER_ID`. The new process has a different pid and a new log file, but the same worker id label.

If a Worker Process is started without a supervisor, it may fall back to a generated `PERAGO_WORKER_ID` that includes the module target and pid:

```python
def resolve_worker_id(module_target: str, env: dict[str, str]) -> str:
    configured = env.get("PERAGO_WORKER_ID")
    if configured:
        return configured
    return f"{default_worker_id_prefix(module_target)}-pid-{os.getpid()}"
```

## Runtime configuration

The MVP implementation uses Pydantic for public models and runtime configuration validation, Typer for CLI commands, and loguru for runtime logging.

Perago uses the standard environment variable names consumed by the underlying SDKs. It does not introduce a `PERAGO_` prefix for Conductor or LakeFS connection settings.

```bash
CONDUCTOR_SERVER_URL=http://localhost:8080/api

LAKECTL_SERVER_ENDPOINT_URL=http://localhost:8000
LAKECTL_CREDENTIALS_ACCESS_KEY_ID=...
LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY=...

PERAGO_WORKSPACE_ROOT=/var/tmp/perago/workspaces
PERAGO_LOG_ROOT=/var/tmp/perago/logs
PERAGO_LOG_FILE_MAX_SIZE=100MB
PERAGO_LOG_RETENTION=30d
PERAGO_WORKER_ID_PREFIX=prodAFeaturesBuild
```

For local development these may be loaded from `.env`. They are worker-local runtime configuration and are not part of Conductor task input or output. Real process environment variables take precedence over `.env`; `.env` only fills missing values. `perago check`, `perago extract`, and `perago start` all read `.env` with the same precedence rule before loading the task module.

Perago targets Conductor OSS for the MVP and parses only `CONDUCTOR_SERVER_URL` into `ConductorConfig`. It does not parse or validate Conductor auth keys. LakeFS endpoint, access key, and secret key must be configured together.

For `perago start`, `PERAGO_WORKER_ID` is written by the Worker Supervisor rather than by a user's `.env`. A user-provided value is only a fallback for unsupervised or local debugging runs. `PERAGO_WORKER_ID_PREFIX` may be set in `.env`; it is the user-facing knob for avoiding worker id collisions.

`PERAGO_WORKSPACE_ROOT` is Perago-specific because it controls local disk placement for all attempt-local workspaces. It is a host file-system path, not a LakeFS path and not a Workspace Path. Perago parses it with `pathlib.Path` so deployments can use platform-native paths:

```bash
# macOS / Linux
PERAGO_WORKSPACE_ROOT=/var/tmp/perago/workspaces

# Windows
PERAGO_WORKSPACE_ROOT=C:\perago\workspaces
```

If `PERAGO_WORKSPACE_ROOT` is not set, Perago uses a platform temp directory under a `perago/workspaces` namespace. `perago check` should report the resolved default in diagnostics, but the value is not emitted into TaskDef JSON.

`perago check` validates that `PERAGO_WORKSPACE_ROOT` and `PERAGO_LOG_ROOT` can be created and written by doing a local dry-run. It must not connect to Conductor or LakeFS.

```python
from pathlib import Path
import tempfile


def load_runtime_env(process_env: dict[str, str], dotenv_env: dict[str, str]) -> dict[str, str]:
    merged = dict(dotenv_env)
    merged.update(process_env)
    return merged


def check_writable_root(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".perago-check-", dir=path) as probe:
        probe_file = Path(probe) / "write-test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
```

All runtime logs use `loguru`.

```python
from loguru import logger
```

`PERAGO_LOG_ROOT` is Perago-specific and controls where Worker Process log files are written. `PERAGO_LOG_FILE_MAX_SIZE` controls the maximum size of a single log file before rotation. If `PERAGO_LOG_ROOT` is not set, Perago uses a platform temp directory under a `perago/logs` namespace. If `PERAGO_LOG_FILE_MAX_SIZE` is not set, the MVP default is `100MB`. If `PERAGO_LOG_RETENTION` is not set, the MVP default is `30d`.

Worker logs are JSONL. Each Worker Process writes to its own active persistent log file; Worker Processes must not share one writable log file. Log files may rotate when they reach `PERAGO_LOG_FILE_MAX_SIZE`. Log event timestamps must use UTC+08:00 and must not depend on the host machine's local timezone.

`PERAGO_LOG_FILE_MAX_SIZE` accepts positive integers or decimals with `KB`, `MB`, or `GB` units. Units are binary: `1KB = 1024` bytes, `1MB = 1024 * 1024` bytes, and `1GB = 1024 * 1024 * 1024` bytes. Whitespace between the number and unit is allowed. Values are case-insensitive. Bare numbers are not accepted.

```python
from decimal import Decimal, ROUND_CEILING
from datetime import timedelta
import re


LOG_SIZE_UNITS = {
    "KB": 1024,
    "MB": 1024 * 1024,
    "GB": 1024 * 1024 * 1024,
}


class RuntimeConfigError(ValueError):
    pass


def parse_log_file_max_size(value: str | None) -> int:
    if value is None or value.strip() == "":
        return 100 * 1024 * 1024

    match = re.fullmatch(
        r"((?:0|[1-9][0-9]*)(?:\.[0-9]+)?)\s*(KB|MB|GB)",
        value.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        raise RuntimeConfigError(
            "PERAGO_LOG_FILE_MAX_SIZE must be a positive size such as '512KB', '100MB', or '1.5GB'"
        )

    amount = Decimal(match.group(1))
    if amount <= 0:
        raise RuntimeConfigError("PERAGO_LOG_FILE_MAX_SIZE must be greater than zero")

    unit = match.group(2).upper()
    return int((amount * LOG_SIZE_UNITS[unit]).to_integral_value(rounding=ROUND_CEILING))


def parse_log_retention(value: str | None) -> timedelta:
    if value is None or value.strip() == "":
        return timedelta(days=30)

    match = re.fullmatch(r"([1-9][0-9]*)d", value.strip(), flags=re.IGNORECASE)
    if not match:
        raise RuntimeConfigError("PERAGO_LOG_RETENTION must be a positive day count such as '7d' or '30d'")

    return timedelta(days=int(match.group(1)))
```

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import os
import re

from loguru import logger


PERAGO_LOG_TIMEZONE = timezone(timedelta(hours=8), name="UTC+08:00")


def patch_log_record(record: dict[str, Any]) -> None:
    record["time"] = record["time"].astimezone(PERAGO_LOG_TIMEZONE)


def safe_segment(value: object) -> str:
    text = str(value)
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", text).strip("._") or "unknown"


def configure_worker_logging(
    *,
    log_root: Path,
    module_target: str,
    worker_id: str,
    max_bytes: int,
    retention: timedelta,
) -> Path:
    started_at = datetime.now(PERAGO_LOG_TIMEZONE).strftime("%Y%m%dT%H%M%S%z")
    log_file = (
        log_root
        / safe_segment(module_target)
        / f"worker_id={safe_segment(worker_id)}"
        / f"pid={os.getpid()}__started={started_at}.jsonl"
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.configure(patcher=patch_log_record)
    logger.add(
        log_file,
        serialize=True,
        rotation=max_bytes,
        retention=retention,
        enqueue=True,
    )
    return log_file
```

Example:

```text
/var/tmp/perago/logs/
  app.workers.features_build/
    worker_id=prodAFeaturesBuild0003/
      pid=42118__started=20260520T200102+0800.jsonl
```

## Attempt-local workspaces

Every Conductor Task Attempt gets its own local workspace directory under `PERAGO_WORKSPACE_ROOT`. Perago must not reuse a fixed workspace directory by task name, Worker Process, repository, or branch.

The directory name must include at least `workflow_instance_id`, `task_id`, and `retry_count`. It may include task and worker labels for readability, but those labels are not the identity boundary.

```python
from pathlib import Path
import re


def safe_segment(value: object) -> str:
    text = str(value)
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", text).strip("._") or "unknown"


def attempt_workspace_dir(workspace_root: Path, task) -> Path:
    return (
        workspace_root
        / safe_segment(task.workflow_instance_id)
        / safe_segment(task.task_def_name)
        / f"task_id={safe_segment(task.task_id)}"
        / f"retry_count={safe_segment(task.retry_count)}"
    )
```

Example:

```text
/var/tmp/perago/workspaces/
  wf-7f3d/
    features.build/
      task_id=9b4c/
        retry_count=2/
```

The runtime creates the attempt directory before downloading the workspace, writes a Perago marker file, and attempts to remove the directory before reporting the task result to Conductor. Cleanup failure is logged with `loguru` and does not change the Conductor result. Startup sweep only deletes directories under `PERAGO_WORKSPACE_ROOT` that contain a Perago marker file; it must not clean the whole root.

```python
import json


ATTEMPT_WORKSPACE_MARKER = ".perago-attempt.json"


def prepare_attempt_workspace(workspace_root: Path, task) -> Path:
    workspace_dir = attempt_workspace_dir(workspace_root, task)
    workspace_dir.mkdir(parents=True, exist_ok=False)
    (workspace_dir / ATTEMPT_WORKSPACE_MARKER).write_text(
        json.dumps(
            {
                "workflow_instance_id": task.workflow_instance_id,
                "task_id": task.task_id,
                "retry_count": task.retry_count,
                "task_def_name": task.task_def_name,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return workspace_dir
```

## LakeFS edition assumptions

The MVP targets LakeFS Community/OSS. It must not rely on Enterprise-only behavior such as async merge.

Perago assumes these LakeFS Community capabilities:

- the target workspace branch can be configured as a protected branch, so direct object writes, deletes, commits, and resets fail;
- successful workspace publication reaches the protected branch through LakeFS merge from a staging commit;
- replacement publication can relocate the target branch to the current staged commit when the observed HEAD is an abandoned publication;
- squash merge is available and should be used so the target branch stays linear;
- pre-merge hooks/actions may be used as fast gates that abort invalid merge attempts;
- pre-merge hooks/actions must not perform long-running waits or lock acquisition.

Perago does not use LakeFS as a business lock service. LakeFS protects the integrity of branch updates and merge conflict detection. Conductor attempt checks and the LakeFS HEAD-state publish fence provide worker-level idempotency. A separate external coordinator or ledger is only needed if Perago must provide strict exactly-once publication or durable result recovery.

## Conductor task input

The workflow passes workspace identity and business params to the task.

Workspace task input:

```json
{
  "workspace": {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3"
  },
  "params": {
    "feature_set": "default",
    "min_rows": 100
  }
}
```

Workspace-free task input:

```json
{
  "params": {
    "song_id": "song-000123",
    "min_duration_seconds": 30
  }
}
```

LakeFS endpoint and credentials are worker-local configuration, for example from `.env`. They are not part of Conductor task input.

The workspace prefix is task metadata, not workflow data. Each task declares the prefix it exposes to the business function through `WorkspaceSpec(prefix=...)`. Prefix mapping is required MVP runtime behavior.

Perago validates the workflow-carried workspace input with the `WorkspaceInput` model.

`WorkspaceSpec(prefix=...)` tells Perago which LakeFS path prefix should become the local workspace root for this task. For example, if a task declares `WorkspaceSpec(prefix="audio/render")`, then `workspace / "raw"` in business code maps to the `audio/render/raw` path under the LakeFS repository/ref from Conductor input.

Different workers may expose different prefixes from the same repository/ref. That is why `prefix` belongs to task code, not to the workflow-carried `workspace` value.

`WorkspaceSpec(prefix="/")` is the default. Prefix values must stay inside the LakeFS repository:

- `"/"` is allowed
- `"audio/render"` is allowed
- `"/audio/render"` is normalized to `"audio/render"`
- `""` is not allowed
- `"../raw"` is not allowed
- `"audio/../raw"` is not allowed

## Workspace guardrails

Workspace guardrails are local file-system checks over the workspace root exposed by `WorkspaceSpec(prefix=...)`.

They are not data transformations, business validators, TaskDef schema rules, or cross-repository scans. They only inspect the local workspace tree Perago prepared for the task.

Guardrail paths are workspace logical paths, not process working-directory paths. Perago canonicalizes every guardrail path or glob pattern to a relative POSIX workspace path at module import time. The canonical form is stable across macOS, Linux, and Windows because it is also the form that maps to LakeFS object paths.

The MVP supports four file-shape guardrails:

```python
from pathlib import Path, PureWindowsPath

from perago import forbid_glob, require_dir, require_file, require_glob


WorkspaceSpec(
    prefix="/",
    pre=[
        require_dir("raw"),
        require_file("manifest.json"),
        require_glob("raw/**/*.parquet", min_count=1),
    ],
    post=[
        require_dir("features"),
        require_glob("features/**/*.parquet", min_count=1, max_count=128),
        forbid_glob("**/*.tmp"),
    ],
)
```

Task authors declare guardrails only through `require_file`, `require_dir`,
`require_glob`, and `forbid_glob`. The internal guardrail model is not part of
the public task author API and should not be imported from `perago`.

The guardrail API accepts `str | os.PathLike`. Documentation examples should prefer `/`-separated relative strings, but task code may use `pathlib` to construct paths portably:

```python
WorkspaceSpec(
    prefix="/",
    pre=[
        require_file("manifest.json"),
        require_file(Path("raw") / "manifest.json"),
        require_glob(Path("raw") / "**" / "*.parquet", min_count=1),
        require_file(PureWindowsPath("raw") / "windows-authored.json"),
    ],
)
```

All accepted forms above canonicalize to POSIX workspace paths:

```text
manifest.json
raw/manifest.json
raw/**/*.parquet
raw/windows-authored.json
```

Invalid guardrail paths fail during `@task(...)` import validation and `perago check` with explicit diagnostics:

```python
require_file("/raw/manifest.json")
# TaskDefinitionError: invalid workspace guardrail path for require_file('/raw/manifest.json'):
# guardrail paths must be relative to WorkspaceSpec(prefix=...); remove the leading slash.

require_file("../raw/manifest.json")
# TaskDefinitionError: invalid workspace guardrail path for require_file('../raw/manifest.json'):
# '..' segments may escape the workspace root.

require_file("raw/../manifest.json")
# TaskDefinitionError: invalid workspace guardrail path for require_file('raw/../manifest.json'):
# '..' segments are not allowed in guardrail paths.

require_file(r"raw\manifest.json")
# TaskDefinitionError: invalid workspace guardrail path for require_file('raw\\manifest.json'):
# string guardrail paths must use '/' separators; use pathlib.Path for platform-native construction.

require_file(PureWindowsPath("C:/raw/manifest.json"))
# TaskDefinitionError: invalid workspace guardrail path for require_file(PureWindowsPath('C:/raw/manifest.json')):
# drive-qualified paths are not allowed.
```

Guardrail paths and glob patterns are evaluated relative to the local workspace root, not relative to the process working directory:

```text
LakeFS repository path        Local business path
--------------------------    -------------------
raw/events/day=2026/a.parquet  workspace / "raw/events/day=2026/a.parquet"
manifest.json                 workspace / "manifest.json"
features/model.parquet        workspace / "features/model.parquet"
```

When a task uses a non-root prefix, guardrails still see only the exposed local root:

```python
WorkspaceSpec(
    prefix="audio/render",
    pre=[
        require_file("manifest.json"),
        require_glob("raw/**/*.wav", min_count=1),
    ],
    post=[
        require_dir("stems"),
        require_glob("stems/**/*.wav", min_count=4, max_count=32),
    ],
)
```

For this task, `require_file("manifest.json")` checks `audio/render/manifest.json` in LakeFS after Perago maps `audio/render` to the local `workspace` path. It must not inspect `manifest.json` at the repository root.

Guardrails run in this order:

```text
download WorkspaceSpec(prefix=...) from the input Workspace Ref
run pre guardrails against the local workspace root
call the business function
run post guardrails against the local workspace root
choose read-only, no-op, or publication path
if writable and changed, upload WorkspaceSpec(prefix=...) to a staging branch
if writable, pass the required HEAD-state fence
if writable and changed, publish the staging commit to the Workspace Branch
attempt cleanup of the attempt-local workspace
complete the Conductor task
```

Perago attempts local workspace cleanup before returning a result to the SDK `TaskRunner`. Cleanup errors are logged with `loguru` but do not change the Conductor result:

```python
from pathlib import Path
from loguru import logger


def cleanup_attempt_workspace(workspace_dir: Path, task) -> None:
    try:
        remove_workspace_tree(workspace_dir)
    except OSError as exc:
        logger.bind(
            workspace_dir=str(workspace_dir),
            workflow_instance_id=task.workflow_instance_id,
            task_id=task.task_id,
            retry_count=task.retry_count,
        ).opt(exception=exc).error("failed to clean attempt-local workspace")


def run_workspace_attempt(task) -> None:
    workspace_dir = download_workspace(task.workspace)

    try:
        run_pre_guardrails(workspace_dir)
        result = call_business_function(workspace_dir, task.params)
        run_post_guardrails(workspace_dir)
        output_workspace = complete_workspace(task, workspace_dir)
    except PreGuardrailViolation as exc:
        cleanup_attempt_workspace(workspace_dir, task)
        complete_task(
            task,
            status="FAILED_WITH_TERMINAL_ERROR",
            reason_for_incompletion=str(exc),
        )
        return
    except PostGuardrailViolation as exc:
        cleanup_attempt_workspace(workspace_dir, task)
        complete_task(
            task,
            status="FAILED",
            reason_for_incompletion=str(exc),
        )
        return
    except TransientRuntimeError as exc:
        cleanup_attempt_workspace(workspace_dir, task)
        complete_task(
            task,
            status="FAILED",
            reason_for_incompletion=str(exc),
        )
        return

    cleanup_attempt_workspace(workspace_dir, task)
    complete_task(
        task,
        status="COMPLETED",
        output={"workspace": output_workspace, "result": result},
    )
```

A pre guardrail failure means the downloaded workspace does not satisfy the task's input file contract. Perago does not call the business function, attempts local cleanup, does not publish a workspace output, and reports a terminal task failure:

```python
# app/workers/features_build.py

@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/",
        pre=[require_glob("raw/**/*.parquet", min_count=1)],
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    raise AssertionError("pre guardrails failed to stop execution")
```

If `raw/**/*.parquet` matches zero files, the runtime result is terminal:

```json
{
  "status": "FAILED_WITH_TERMINAL_ERROR",
  "reasonForIncompletion": "pre guardrail require_glob('raw/**/*.parquet') matched 0 files; min_count=1"
}
```

A post guardrail failure means the business function returned successfully but did not produce the promised file shape. Perago does not upload or publish the workspace, attempts local cleanup, and reports a regular retryable failure:

```python
@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/",
        post=[require_glob("features/**/*.parquet", min_count=1)],
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    (workspace / "features").mkdir(exist_ok=True)
    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

If `features/**/*.parquet` matches zero files, the runtime result follows the task retry policy:

```json
{
  "status": "FAILED",
  "reasonForIncompletion": "post guardrail require_glob('features/**/*.parquet') matched 0 files; min_count=1"
}
```

Pre guardrail failures bypass `TaskControls.retry` because they represent upstream input workspace contract violations. Post guardrail failures use `TaskControls.retry` because the current task attempt failed to produce the promised output shape and a retry may rerun the business function for the same Logical Task.

Conductor owns retry scheduling. A retry may be picked up by the same Worker Process, a different Worker Process under the same supervisor, or a Worker Process on another machine. Perago must not rely on local retry state. Every retry starts by downloading the same deterministic input Workspace Ref into a fresh local workspace.

The failed attempt-local workspace must not become the next attempt's input. The next attempt downloads the deterministic input Workspace Ref into its own fresh local workspace, regardless of which Worker Process receives the retry.

If local workspace cleanup fails after an attempt-local workspace was created, Perago logs an error and still reports the original task result to Conductor. Cleanup failure must not convert a `COMPLETED`, `FAILED`, or `FAILED_WITH_TERMINAL_ERROR` result into another status. Startup cleanup may sweep abandoned attempt workspaces before the process polls.

Guardrails are not emitted into the Conductor TaskDef schema. The generated TaskDef still describes only `workspace`, `params`, and `result`; guardrails remain Perago runtime metadata validated by module import and `perago check`.

## Conductor task output

Perago wraps the business return value with the completed workspace reference.

Workspace task output:

```json
{
  "workspace": {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "9c6f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca"
  },
  "result": {
    "row_count": 100,
    "feature_count": 24
  }
}
```

For workspace task workers, Perago must complete the workspace path and attempt local cleanup before reporting the Conductor task as completed. Read-only and no-op completions keep the input immutable commit ref; writable completions that changed workspace content publish a new immutable commit ref. Downstream workers receive the same target branch plus the output ref.

Perago validates the completed workspace reference with the `WorkspaceOutput` model before reporting task completion.

The workflow carries both a writable branch name, such as `main`, and an immutable commit ref. The commit ref gives each worker a deterministic input version for retries; the branch is the write target advanced by successful workers.

Workspace-free task output:

```json
{
  "result": {
    "valid": true,
    "reason": null
  }
}
```

## Workspace transaction runtime

Workspace publishing uses a TCC-inspired model when a writable task produces workspace changes, but the user function does not implement `try`, `confirm`, or `cancel` methods. Perago owns the transaction boundary around the workspace.

| TCC phase | Perago runtime behavior | Main branch visibility |
| --- | --- | --- |
| Try | For a writable changed workspace, create a short-lived staging branch, upload the task prefix, and commit to that staging branch. | Not visible on the target branch. |
| Confirm | Re-check the current Conductor attempt, check whether the target branch may still be advanced, then squash merge the staging commit into the target branch. Writable no-op completion only checks or relocates the target branch without creating an empty commit. | Visible as one linear commit, or unchanged for no-op. |
| Cancel | Delete the staging branch after failure, stale attempt detection, publish-fence failure, or successful merge. | No target branch change unless confirm already succeeded. |

The staging branch is internal runtime state. It is not part of Conductor task input or output. Inside the runtime, the staged workspace reference carries the LakeFS repository, staging branch, and staging commit so cleanup is driven by explicit identity instead of worker-local mutable state.

```text
Conductor task input
  workspace.repository = song-000123
  workspace.branch     = main
  workspace.ref        = immutable input commit

Perago runtime
  if WorkspaceSpec(read_only=True): keep input ref and skip HEAD checks
  elif writable diff is empty: check HEAD and keep input ref
  else:
    create hidden staging branch from the publish base
    sync WorkspaceSpec(prefix=...) into the staging branch
    commit staging branch
    publish through fences

Conductor task output
  workspace.repository = song-000123
  workspace.branch     = main
  workspace.ref        = input commit or published merge commit
```

### Attempt fence

发布前必须确认当前 worker 仍持有 Conductor task attempt。

```python
class StaleAttemptError(RuntimeError):
    pass


def assert_current_attempt(task_client, task) -> None:
    fresh = task_client.get_task(task.task_id)
    if (
        fresh.status != "IN_PROGRESS"
        or fresh.workflow_instance_id != task.workflow_instance_id
        or fresh.task_id != task.task_id
        or fresh.retry_count != task.retry_count
    ):
        raise StaleAttemptError(task.task_id)
```

Perago 在可写路径的 stage 或 no-op branch relocation 前检查一次；如果已经创建 staging branch，则在 publish 前再检查一次。检查失败时，attempt 返回 `FAILED`，并执行 cleanup。

### LakeFS publish protocol

完整协议见 [LakeFS 发布协议](lakefs-publication-protocol.md)。核心规则：

- 不需要任何 commit metadata。
- read-only workspace task 不检查 HEAD、不 stage、不 publish，output ref 保持 input ref。
- staging branch 从 input `workspace.ref` 创建，但只在可写且 diff 非空时创建。
- 可写 task diff 为空时 Perago 不会创建 empty commit；如果 target HEAD 是 input ref 的直接子提交，relocate 回 input ref 后完成。
- 如果 target `HEAD == input_ref`，merge staging branch。
- 如果 `parent(HEAD) == input_ref`，把当前 HEAD 当作 abandoned publication，并 hard-reset / relocate target branch 到本次 staging commit。
- 其他 HEAD 状态全部 fail closed。
- publish 后 best-effort 删除 staging branch。

简化伪代码：

```python
def complete_workspace(task_client, task, workspace, workspace_dir):
    if task.workspace.read_only:
        return workspace.ref

    changed = workspace_has_diff(workspace_dir, workspace)
    assert_current_attempt(task_client, task)

    if changed:
        staging = stage_workspace(workspace_dir, workspace)
        assert_current_attempt(task_client, task)

    repo = lakefs.repository(workspace.repository)
    target = repo.branch(workspace.branch)
    head = target.get_commit()

    if not changed:
        if head.id == workspace.ref:
            return workspace.ref
        if first_parent(head) == workspace.ref:
            repo.client.sdk_client.experimental_api.hard_reset_branch(
                workspace.repository,
                workspace.branch,
                ref=workspace.ref,
                force=False,
            )
            return workspace.ref
        raise PublishFenceError(
            f"{workspace.branch} cannot complete no-op from input ref {workspace.ref}"
        )

    if head.id == workspace.ref:
        return staging.merge_into(target, squash_merge=True)
    if first_parent(head) == workspace.ref:
        published_ref = staging.get_commit().id
        repo.client.sdk_client.experimental_api.hard_reset_branch(
            workspace.repository,
            workspace.branch,
            ref=published_ref,
            force=False,
        )
    else:
        raise PublishFenceError(
            f"{workspace.branch} cannot publish from input ref {workspace.ref}"
        )

    return published_ref
```

### Publish budget

Publish budget 必须来自真实限制和观测：

- LakeFS publish request timeout；
- Conductor completion budget reserve；
- 目标数据量下的 LakeFS publish 延迟观测值；
- worker shutdown grace 和 heartbeat interval。

Workspace publication 不跟随或上传 symbolic link。workspace 内出现 symlink 时，stage 前拒绝发布。

## Extracted task definition

`perago extract` must emit a Conductor task definition with complete input and output schema metadata.

For Conductor 3.x, input and output schemas are embedded directly in the task definition. The MVP output should not rely on a separately registered schema resource. The example below also avoids JSON Schema `$ref` because the local Conductor task definition notes do not document `$ref` support.

Workspace task definition:

```json
{
  "name": "features.build",
  "description": "Build feature parquet files.",
  "ownerEmail": "data@example.com",
  "retryCount": 3,
  "retryLogic": "FIXED",
  "retryDelaySeconds": 60,
  "maxRetryDelaySeconds": 0,
  "backoffJitterMs": 0,
  "totalTimeoutSeconds": 0,
  "timeoutPolicy": "TIME_OUT_WF",
  "timeoutSeconds": 0,
  "responseTimeoutSeconds": 600,
  "pollTimeoutSeconds": 0,
  "inputKeys": ["workspace", "params"],
  "outputKeys": ["workspace", "result"],
  "inputSchema": {
    "name": "features.build.input",
    "version": 1,
    "type": "JSON",
    "data": {
      "type": "object",
      "properties": {
        "workspace": {
          "type": "object",
          "properties": {
            "repository": {
              "type": "string"
            },
            "branch": {
              "type": "string"
            },
            "ref_type": {
              "type": "string",
              "enum": ["commit"]
            },
            "ref": {
              "type": "string"
            }
          },
          "required": ["repository", "branch", "ref_type", "ref"],
          "additionalProperties": false
        },
        "params": {
          "type": "object",
          "properties": {
            "feature_set": {
              "type": "string"
            },
            "min_rows": {
              "type": "integer",
              "minimum": 1
            }
          },
          "required": ["feature_set", "min_rows"]
        }
      },
      "required": ["workspace", "params"],
      "additionalProperties": false
    }
  },
  "outputSchema": {
    "name": "features.build.output",
    "version": 1,
    "type": "JSON",
    "data": {
      "type": "object",
      "properties": {
        "workspace": {
          "type": "object",
          "properties": {
            "repository": {
              "type": "string"
            },
            "branch": {
              "type": "string"
            },
            "ref_type": {
              "type": "string",
              "enum": ["commit"]
            },
            "ref": {
              "type": "string"
            }
          },
          "required": ["repository", "branch", "ref_type", "ref"],
          "additionalProperties": false
        },
        "result": {
          "type": "object",
          "properties": {
            "row_count": {
              "type": "integer",
              "minimum": 0
            },
            "feature_count": {
              "type": "integer",
              "minimum": 0
            }
          },
          "required": ["row_count", "feature_count"],
          "additionalProperties": false
        }
      },
      "required": ["workspace", "result"],
      "additionalProperties": false
    }
  }
}
```

Workspace-free task definition:

```json
{
  "name": "metadata.validate",
  "description": "Validate song metadata.",
  "ownerEmail": "data@example.com",
  "retryCount": 3,
  "retryLogic": "FIXED",
  "retryDelaySeconds": 60,
  "maxRetryDelaySeconds": 0,
  "backoffJitterMs": 0,
  "totalTimeoutSeconds": 0,
  "timeoutPolicy": "TIME_OUT_WF",
  "timeoutSeconds": 0,
  "responseTimeoutSeconds": 600,
  "pollTimeoutSeconds": 0,
  "inputKeys": ["params"],
  "outputKeys": ["result"],
  "inputSchema": {
    "name": "metadata.validate.input",
    "version": 1,
    "type": "JSON",
    "data": {
      "type": "object",
      "properties": {
        "params": {
          "type": "object",
          "properties": {
            "song_id": {
              "type": "string"
            },
            "min_duration_seconds": {
              "type": "integer",
              "minimum": 1
            }
          },
          "required": ["song_id", "min_duration_seconds"],
          "additionalProperties": false
        }
      },
      "required": ["params"],
      "additionalProperties": false
    }
  },
  "outputSchema": {
    "name": "metadata.validate.output",
    "version": 1,
    "type": "JSON",
    "data": {
      "type": "object",
      "properties": {
        "result": {
          "type": "object",
          "properties": {
            "valid": {
              "type": "boolean"
            },
            "reason": {
              "anyOf": [
                {
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null
            }
          },
          "required": ["valid"],
          "additionalProperties": false
        }
      },
      "required": ["result"],
      "additionalProperties": false
    }
  }
}
```

`perago check` must fail if the task contract cannot produce complete schemas.

- function signature is not exactly `(workspace: Path, params: ParamsModel)` or `(params: ParamsModel)`: fail
- function has `workspace: Path` but `@task(...)` does not provide `workspace=WorkspaceSpec(...)`: fail
- function has no `workspace: Path` but `@task(...)` provides `workspace=WorkspaceSpec(...)`: fail
- missing type annotation for `params`: fail
- `params` is not a Pydantic model: fail
- missing return annotation: fail
- return type is not a Pydantic model: fail
- duplicate `params=` or `output=` declarations in `@task(...)`: fail
