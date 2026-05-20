from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@task(name="bad.decorator.types", owner_email="data@example.com", controls={})
def bad_decorator_types(params: Params) -> Output:
    return Output(value=params.value)
