import json
import sys
from datetime import timedelta

from loguru import logger

from perago import RuntimeConfig, prepare_worker_runtime
from perago.workspace import ATTEMPT_WORKSPACE_MARKER


def test_prepare_worker_runtime_does_not_sweep_marked_workspaces_and_configures_logging(tmp_path) -> None:
    marked = tmp_path / "workspaces" / "task_id=1"
    marked.mkdir(parents=True)
    (marked / ATTEMPT_WORKSPACE_MARKER).write_text("{}", encoding="utf-8")
    keep = tmp_path / "workspaces" / "keep"
    keep.mkdir(parents=True)
    (keep / "data.txt").write_text("keep", encoding="utf-8")
    config = RuntimeConfig(
        workspace_root=tmp_path / "workspaces",
        log_root=tmp_path / "logs",
        log_file_max_size=1024 * 1024,
        log_retention=timedelta(days=1),
        worker_id_prefix="prodAFeaturesBuild",
    )

    runtime = prepare_worker_runtime(
        config=config,
        module_target="app.workers.features_build",
        env={"PERAGO_WORKER_ID": "prodAFeaturesBuild0001"},
    )

    logger.info("runtime-ready")
    logger.complete()

    data = json.loads(runtime.log_file.read_text(encoding="utf-8").splitlines()[0])
    assert runtime.worker_id == "prodAFeaturesBuild0001"
    assert runtime.swept_workspaces == []
    assert marked.exists()
    assert keep.exists()
    assert runtime.log_file.parent == tmp_path / "logs" / "app.workers.features_build" / "worker_id=prodAFeaturesBuild0001"
    assert data["record"]["message"] == "runtime-ready"

    logger.remove()
    logger.add(sys.stderr)
