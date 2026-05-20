from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@task(name="bad.keyword_only_signature", owner_email="data@example.com")
def bad_keyword_only_signature(params: Params, *, flag: bool) -> Output:
    del flag
    return Output(value=params.value)
