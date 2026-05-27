from pathlib import Path

from pydantic import BaseModel

from perago import PublishBudget, TaskControls, TimeoutPolicy, WorkspaceSpec, task


class InspectParams(BaseModel):
    expected: str


class InspectOutput(BaseModel):
    found: bool


budget = PublishBudget(
    observed_merge_p99_seconds=20,
    safety_margin_seconds=10,
    lakefs_merge_timeout_seconds=45,
    conductor_completion_timeout_seconds=15,
    worker_shutdown_grace_seconds=30,
    heartbeat_interval_seconds=10,
)


@task(
    name="metadata.inspect",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix="/audio/render", read_only=True),
    controls=TaskControls(timeout=TimeoutPolicy(response_seconds=999), publish_budget=budget),
)
def inspect_metadata(workspace: Path, params: InspectParams) -> InspectOutput:
    return InspectOutput(found=(workspace / params.expected).exists())
