from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@task(name="bad.variadic_signature", owner_email="data@example.com")
def bad_variadic_signature(params: Params, *extra: object) -> Output:
    del extra
    return Output(value=params.value)
