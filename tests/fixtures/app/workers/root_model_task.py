from pydantic import BaseModel, RootModel

from perago import task


class Params(RootModel[list[int]]):
    pass


class Output(BaseModel):
    total: int


@task(name="tests.root_model", owner_email="data@example.com")
def root_model_task(params: Params) -> Output:
    return Output(total=sum(params.root))
