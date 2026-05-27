from pydantic import BaseModel, ConfigDict

from perago import task


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class Output(BaseModel):
    ok: bool


@task(name="tests.config_dict", owner_email="data@example.com")
def config_dict_task(params: Params) -> Output:
    return Output(ok=params.value > 0)
