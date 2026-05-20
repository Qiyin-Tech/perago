from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: int


@task(name="bad.missing_return_annotation", owner_email="data@example.com")
def bad_missing_return_annotation(params: Params):
    return {"value": params.value}
