from pydantic import BaseModel

from perago import ExecutionLimits, TaskControls, task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@task(
    name="bad.controls",
    owner_email="data@example.com",
    controls=TaskControls(
        limits=ExecutionLimits(rate_limit_frequency_in_seconds=60),
    ),
)
def bad_controls(params: Params) -> Output:
    return Output(value=params.value)
