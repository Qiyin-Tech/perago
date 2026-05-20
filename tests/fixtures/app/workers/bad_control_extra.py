from pydantic import BaseModel

from perago import TaskControls, task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@task(
    name="bad.control.extra",
    owner_email="data@example.com",
    controls=TaskControls(retry_count=3),
)
def bad_control_extra(params: Params) -> Output:
    return Output(value=params.value)
