from pathlib import Path

from perago.config import read_dotenv


def test_env_example_contains_runtime_connection_keys() -> None:
    values = read_dotenv(Path(".env.example"))

    assert values == {
        "CONDUCTOR_SERVER_URL": "http://localhost:8080/api",
        "LAKECTL_SERVER_ENDPOINT_URL": "http://localhost:8000",
        "LAKECTL_CREDENTIALS_ACCESS_KEY_ID": "replace-me",
        "LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY": "replace-me",
        "PERAGO_WORKSPACE_ROOT": "/var/tmp/perago/workspaces",
        "PERAGO_LOG_ROOT": "/var/tmp/perago/logs",
        "PERAGO_LOG_FILE_MAX_SIZE": "100MB",
        "PERAGO_LOG_RETENTION": "30d",
        "PERAGO_WORKER_ID_PREFIX": "peragoLocalWorker",
    }

    assert "PERAGO_WORKER_ID" not in values
