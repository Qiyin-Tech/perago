from pydantic import BaseModel

from perago import MetricRecorder, MetricSpec, task


class Params(BaseModel):
    song_id: str


class Output(BaseModel):
    valid: bool


@task(name="metrics.validate", owner_email="data@example.com", metrics=MetricSpec())
def validate_metrics(params: Params, metrics: MetricRecorder) -> Output:
    del metrics
    return Output(valid=bool(params.song_id))
