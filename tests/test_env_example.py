from pathlib import Path

from perago.config import read_dotenv


def test_env_example_contains_runtime_connection_keys() -> None:
    values = read_dotenv(Path(".env.example"))

    assert values == {
        "CONDUCTOR_SERVER_URL": "http://localhost:8080/api",
        "PERAGO_WORKSPACE_ROOT": "/var/tmp/perago/workspaces",
        "PERAGO_LOG_ROOT": "/var/tmp/perago/logs",
        "PERAGO_LOG_FILE_MAX_SIZE": "100MB",
        "PERAGO_LOG_RETENTION": "30d",
        "PERAGO_EXECUTION_MODE": "process",
        "PERAGO_FAILURE_REASON_MAX_LENGTH": "500",
        "PERAGO_WORKSPACE_GC_TTL": "24h",
        "PERAGO_WORKSPACE_GC_INTERVAL": "1h",
        "PERAGO_WORKER_ID_PREFIX": "peragoLocalWorker",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://victoria-metrics:8428/opentelemetry/v1/metrics",
        "OTEL_EXPORTER_OTLP_METRICS_COMPRESSION": "gzip",
        "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT": "10000",
        "OTEL_METRIC_EXPORT_INTERVAL": "60000",
        "PERAGO_INSTANCE_ID": "perago-local-001",
    }

    configured_key_order = [
        line.split("=", 1)[0]
        for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert configured_key_order == [
        "CONDUCTOR_SERVER_URL",
        "PERAGO_WORKSPACE_ROOT",
        "PERAGO_LOG_ROOT",
        "PERAGO_LOG_FILE_MAX_SIZE",
        "PERAGO_LOG_RETENTION",
        "PERAGO_EXECUTION_MODE",
        "PERAGO_FAILURE_REASON_MAX_LENGTH",
        "PERAGO_WORKSPACE_GC_TTL",
        "PERAGO_WORKSPACE_GC_INTERVAL",
        "PERAGO_WORKER_ID_PREFIX",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_COMPRESSION",
        "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT",
        "OTEL_METRIC_EXPORT_INTERVAL",
        "PERAGO_INSTANCE_ID",
    ]

    assert "PERAGO_WORKER_ID" not in values
    assert "LAKECTL_SERVER_ENDPOINT_URL" not in values
    assert "LAKECTL_CREDENTIALS_ACCESS_KEY_ID" not in values
    assert "LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY" not in values
