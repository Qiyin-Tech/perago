from pathlib import Path

from pydantic import BaseModel

from perago import WorkspaceSpec, require_file, task


class Params(BaseModel):
    value: str


class Output(BaseModel):
    ok: bool


@task(
    name="bad.guardrail.absolute",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(pre=[require_file("/raw/manifest.json")]),
)
def bad_guardrail_absolute(workspace: Path, params: Params) -> Output:
    return Output(ok=True)
