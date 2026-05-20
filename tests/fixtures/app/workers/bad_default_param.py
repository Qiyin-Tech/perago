from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@task(name="bad.default", owner_email="data@example.com")
def bad_default(params: Params = Params(value=1)) -> Output:
    return Output(value=params.value)
