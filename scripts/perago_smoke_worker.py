from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from perago import WorkspaceSpec, task


WORKSPACE_PREFIX = "perago-smoke"
TASK_NAME = "perago.smoke.workspace"


class HelloParams(BaseModel):
    run_id: str
    greeting: str


class HelloOutput(BaseModel):
    message: str
    input_text: str


@task(
    name=TASK_NAME,
    description="Real Conductor/LakeFS smoke workspace task.",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(prefix=WORKSPACE_PREFIX),
)
def hello_workspace(workspace: Path, params: HelloParams) -> HelloOutput:
    run_dir = workspace / params.run_id
    input_text = (run_dir / "input.txt").read_text(encoding="utf-8")
    message = f"{params.greeting}, {input_text}"
    (run_dir / "output.txt").write_text(message, encoding="utf-8")
    return HelloOutput(message=message, input_text=input_text)
