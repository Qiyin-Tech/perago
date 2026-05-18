# perago
A typed task runtime for executing Conductor workers over versioned workspaces.
`perago` provides a small internal runtime layer for writing workflow workers as ordinary typed Python functions. It hides workflow orchestration, workspace lifecycle management, schema validation, file guardrails, and commit handling behind a consistent task interface.
The goal is to let business worker code focus on local file operations and typed parameters, without directly depending on Conductor or a specific versioned-storage backend.

## Status
Early internal package. APIs are expected to change before `1.0`.

## Design goals
- Keep business workers simple.
- Avoid direct Conductor SDK usage in task implementation code.
- Avoid direct LakeFS usage in task implementation code.
- Treat each task as a typed operation over a local workspace.
- Generate task metadata from Python type annotations and registration bindings.
- Support workspace guardrails such as required files, glob checks, and mutation constraints.
- Keep the workspace backend replaceable.
- Make local development, CI validation, and worker deployment reproducible.

## Core model
> Just an early stage intuition for the usage of perago, may changed in the future.

A task is written as a normal Python function:
```python
from pathlib import Path
from pydantic import BaseModel, Field
class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)
class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    # Read and write files under workspace.
    # No Conductor SDK.
    # No LakeFS SDK.
    # No process pool or global concurrency control.
```

The task is registered with runtime metadata:

```python
from perago import task, WorkspaceSpec, require_dir, require_glob, forbid_glob
@task(
    name="features.build",
    params=BuildFeaturesParams,
    output=BuildFeaturesOutput,
    workspace=WorkspaceSpec(
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
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
```

At runtime, perago is responsible for:

* polling and completing Conductor tasks;
* validating task input and output;
* opening the workflow workspace;
* checking pre-task and post-task file guardrails;
* running the business function;
* committing workspace changes through the configured backend;
* returning typed task output to Conductor.
