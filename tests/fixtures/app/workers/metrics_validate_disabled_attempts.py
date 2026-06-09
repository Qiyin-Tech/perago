from pydantic import BaseModel

from perago import MetricRecorder, MetricSpec, task


class Params(BaseModel):
    song_id: str


class Output(BaseModel):
    valid: bool


@task(
    name="metrics.validate.disabled_attempts",
    owner_email="data@example.com",
    metrics=MetricSpec(attempts=False),
)
def validate_metrics_disabled_attempts(params: Params, metrics: MetricRecorder) -> Output:
    del metrics
    return Output(valid=bool(params.song_id))
