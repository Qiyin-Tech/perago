from pathlib import Path

from pydantic import BaseModel

from perago import WorkspaceSpec, task


class Params(BaseModel):
    value: str


class Output(BaseModel):
    ok: bool


@task(name="bad.signature", owner_email="data@example.com", workspace=WorkspaceSpec())
def bad(path: Path, params: Params) -> Output:
    return Output(ok=True)
