# perago

`perago` is a typed Python runtime layer for Conductor workers that operate on versioned workspaces.

The first MVP targets LakeFS as the workspace backend. Task authors write ordinary typed Python functions; Perago owns task definition extraction, validation, worker process startup, workspace download/publish, guardrails, and Conductor completion.

## Status

Early internal package. APIs are still being shaped before `1.0`.

The current development slice implements the parts that do not require a live
Conductor server or LakeFS server:

- task author API: `@task`, `WorkspaceSpec`, guardrail helper functions, and
  grouped `TaskControls` including explicit publish budgets;
- import-time task validation for the single-task module contract;
- `perago check` diagnostics for task declarations and local runtime config;
- `perago extract` generation of local Conductor TaskDef JSON.

`perago start`, Conductor polling/completion, LakeFS workspace download, and
LakeFS publication are integration-phase work and are intentionally not wired
to external services yet.

The current implementation target is documented in:

- [MVP examples](docs/mvp_examples.md)
- [Context glossary](CONTEXT.md)
- [Architecture decisions](docs/adr/README.md)
- [Conductor TaskDef notes](docs/conductor/task_def.md)

## Task shape

Each Python module declares exactly one task worker. The function signature is the source of the business input and output contract.

```python
from pathlib import Path

from pydantic import BaseModel, Field

from perago import WorkspaceSpec, require_dir, require_glob, task


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
        ],
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

Workspace-free tasks use the same model without `workspace: Path`.

```python
@task(
    name="metadata.validate",
    description="Validate metadata.",
    owner_email="data@example.com",
)
def validate_metadata(params: ValidateMetadataParams) -> ValidateMetadataOutput:
    return ValidateMetadataOutput(valid=True)
```

## CLI

The `perago` command is a Typer CLI. MVP commands accept a Python module import path, not file paths or `module:app` targets.

```bash
perago check app.workers.features_build
perago extract app.workers.features_build --output generated/features.build.json
perago start app.workers.features_build -j 4
```

- `perago check` imports the module, validates the task declaration, validates Perago runtime config from `.env`, and reports CLI diagnostics.
- `perago extract` emits Conductor TaskDef JSON with embedded input/output schemas.
- `perago start` currently validates startup inputs and exits with a clear diagnostic until the Conductor/LakeFS worker integration is added.

## Runtime configuration

Perago reads `.env` for local development. Real process environment variables take precedence over `.env`; `.env` only fills missing values.

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

Runtime models and config validation use Pydantic. CLI commands use Typer. Runtime logs use loguru JSONL files with UTC+08:00 timestamps.

Perago targets Conductor OSS for the MVP and only parses `CONDUCTOR_SERVER_URL`; it does not configure Conductor auth keys. LakeFS endpoint, access key, and secret key are parsed together and checked as one credential group. `perago check` still does not connect to either service.

## Workspace guardrails

Workspace guardrails are file-shape checks over the local workspace root exposed by `WorkspaceSpec(prefix=...)`.

- task authors declare guardrails only through `require_file`, `require_dir`, `require_glob`, and `forbid_glob`;
- the internal guardrail model is not part of the public task author API;
- pre guardrail failure returns `FAILED_WITH_TERMINAL_ERROR`;
- post guardrail failure returns retryable `FAILED`;
- guardrail paths are relative workspace paths;
- absolute paths, `..` segments, backslash-separated strings, and drive-qualified paths are rejected during module import and `perago check`;
- invalid guardrail declarations fail import validation and `perago check`.

## Workspace runtime

For workspace tasks, Perago downloads the input workspace ref, runs the function against an attempt-local workspace directory, publishes changes through a staging LakeFS branch, attempts local cleanup, and then reports the task result to Conductor.

Every Conductor Task Attempt gets its own local workspace directory under `PERAGO_WORKSPACE_ROOT`; workspaces are not reused across attempts, task workers, or worker processes.
