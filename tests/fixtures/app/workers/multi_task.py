from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: str


class Output(BaseModel):
    ok: bool


@task(name="multi.one", owner_email="data@example.com")
def one(params: Params) -> Output:
    return Output(ok=True)


@task(name="multi.two", owner_email="data@example.com")
def two(params: Params) -> Output:
    return Output(ok=True)
