from pydantic import BaseModel

from perago import task


class Output(BaseModel):
    value: int


@task(name="bad.missing_params_annotation", owner_email="data@example.com")
def bad_missing_params_annotation(params) -> Output:
    return Output(value=params["value"])
