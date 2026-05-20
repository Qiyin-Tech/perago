from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@task(name="bad.async", owner_email="data@example.com")
async def bad_async(params: Params) -> Output:
    return Output(value=params.value)
