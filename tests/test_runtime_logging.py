import json
import sys
from datetime import timedelta

from loguru import logger

from perago.runtime_logging import configure_worker_logging


def test_configures_worker_jsonl_logging(tmp_path) -> None:
    log_file = configure_worker_logging(
        log_root=tmp_path,
        module_target="app.workers.features_build",
        worker_id="prodAFeaturesBuild0003",
        max_bytes=1024 * 1024,
        retention=timedelta(days=1),
    )

    logger.info("log-check")
    logger.complete()

    data = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    assert log_file.parent == tmp_path / "app.workers.features_build" / "worker_id=prodAFeaturesBuild0003"
    assert log_file.name.startswith("pid=")
    assert log_file.name.endswith(".jsonl")
    assert data["record"]["message"] == "log-check"
    assert data["record"]["time"]["repr"].endswith("+08:00")

    logger.remove()
    logger.add(sys.stderr)
