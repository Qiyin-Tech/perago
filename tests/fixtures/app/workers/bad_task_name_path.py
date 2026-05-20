from pydantic import BaseModel

from perago import task


class Params(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@task(name="../bad.name", owner_email="data@example.com")
def bad_task_name_path(params: Params) -> Output:
    return Output(value=params.value)
