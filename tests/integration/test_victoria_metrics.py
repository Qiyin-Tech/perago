from __future__ import annotations

import json
import os
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

import pytest

from perago.config import MetricsConfig
from perago.metrics import OtelMetricRecorder, RuntimeMetricContext, TaskAttemptMetricContext


pytestmark = pytest.mark.skipif(
    os.environ.get("PERAGO_RUN_VICTORIA_METRICS_TEST") != "1",
    reason="set PERAGO_RUN_VICTORIA_METRICS_TEST=1 to run the local VictoriaMetrics smoke test",
)


def test_otel_recorder_writes_application_and_runtime_metrics_to_victoria_metrics() -> None:
    endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "http://localhost:8428/opentelemetry/v1/metrics",
    )
    query_base = endpoint.removesuffix("/opentelemetry/v1/metrics")
    unique = f"vm_{uuid4().hex}"
    task_name = f"tests.{unique}"
    recorder = OtelMetricRecorder(
        MetricsConfig(
            endpoint=endpoint,
            timeout_millis=5000,
            export_interval_millis=1000,
        )
    )

    attempt_recorder = recorder.with_context(
        TaskAttemptMetricContext(
            task_name=task_name,
            task_id=f"task-{unique}",
            workflow_instance_id=f"wf-{unique}",
            execution_id=f"exec-{unique}",
            worker_id="worker-vm-smoke",
            retry_count=0,
        )
    )
    attempt_recorder.histogram("vm_smoke_value", 1, labels={"smoke_id": unique})
    recorder.runtime_gauge(
        "busy_slots",
        1,
        context=RuntimeMetricContext(task_name=task_name, perago_instance_id=f"instance-{unique}"),
    )
    recorder.shutdown()

    assert _eventually_series(
        query_base,
        f'{{__name__="app.vm_smoke_value_count", smoke_id="{unique}"}}',
    )
    assert _eventually_series(
        query_base,
        f'{{__name__="runtime.busy_slots", task_name="{task_name}"}}',
    )


def _eventually_series(query_base: str, match: str) -> bool:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _query_victoria_metrics_series(query_base, match):
            return True
        time.sleep(0.5)
    return False


def _query_victoria_metrics_series(query_base: str, match: str) -> bool:
    params = urlencode({"match[]": match})
    with urlopen(f"{query_base}/api/v1/series?{params}", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        return False
    return bool(payload.get("data"))
