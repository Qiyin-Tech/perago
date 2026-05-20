from collections.abc import Callable

from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    callback: Callable[[int], int]


class Output(BaseModel):
    value: int


@task(name="bad.schema", owner_email="data@example.com")
def bad_schema(params: Params) -> Output:
    return Output(value=1)
