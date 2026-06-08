from perago.metrics import InMemoryMetricSink, TaskAttemptMetricContext, create_metric_recorder


def test_metric_recorder_records_application_histogram_and_gauge_with_task_label() -> None:
    sink = InMemoryMetricSink()
    recorder = create_metric_recorder(
        TaskAttemptMetricContext(
            task_name="features.build",
            task_id="task-9b4c",
            workflow_instance_id="wf-7f3d",
            execution_id="exec-1",
            worker_id="worker-1",
            retry_count=2,
        ),
        sink=sink,
    )

    recorder.histogram("audio_seconds", 31.4, labels={"format": "wav"})
    recorder.gauge("busy_slots", 3)

    assert [sample.name for sample in sink.samples] == ["app.audio_seconds", "app.busy_slots"]
    assert sink.samples[0].value == 31.4
    assert sink.samples[0].labels == {"task_name": "features.build", "format": "wav"}
    assert sink.samples[1].value == 3
    assert sink.samples[1].labels == {"task_name": "features.build"}


def test_metric_recorder_keeps_perago_label_on_conflict_and_warns(monkeypatch) -> None:
    warnings: list[str] = []
    sink = InMemoryMetricSink()
    recorder = create_metric_recorder(
        TaskAttemptMetricContext(
            task_name="features.build",
            task_id="task-9b4c",
            workflow_instance_id="wf-7f3d",
            execution_id="exec-1",
            worker_id="worker-1",
            retry_count=2,
        ),
        sink=sink,
    )
    monkeypatch.setattr("perago.metrics.logger.warning", lambda message, **kwargs: warnings.append(message.format(**kwargs)))

    recorder.histogram("audio_seconds", 31.4, labels={"task_name": "fake", "format": "wav"})

    assert sink.samples[0].labels == {"task_name": "features.build", "format": "wav"}
    assert warnings == [
        "metric_name=app.audio_seconds label_key=task_name "
        "perago_label_value=features.build ignored_user_label_value=fake"
    ]


def test_metric_recorder_timer_records_elapsed_seconds(monkeypatch) -> None:
    clock = iter([10.0, 12.5])
    sink = InMemoryMetricSink()
    recorder = create_metric_recorder(
        TaskAttemptMetricContext(
            task_name="features.build",
            task_id="task-9b4c",
            workflow_instance_id="wf-7f3d",
            execution_id="exec-1",
            worker_id="worker-1",
            retry_count=2,
        ),
        sink=sink,
    )
    monkeypatch.setattr("perago.metrics.perf_counter", lambda: next(clock))

    with recorder.timer("model_call", labels={"provider": "openai"}):
        pass

    assert sink.samples[0].name == "app.model_call"
    assert sink.samples[0].value == 2.5
    assert sink.samples[0].labels == {"task_name": "features.build", "provider": "openai"}
