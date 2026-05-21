import json
from dataclasses import dataclass

import pytest

from perago.workspace import (
    ATTEMPT_WORKSPACE_MARKER,
    WorkspaceOwner,
    cleanup_attempt_workspace,
    cleanup_attempt_workspace_safely,
    prepare_attempt_workspace,
)


@dataclass(frozen=True)
class Attempt:
    workflow_instance_id: str
    task_def_name: str
    task_id: str
    retry_count: int
    execution_id: str = "exec-1"


def test_prepares_and_cleans_attempt_workspace(tmp_path) -> None:
    task = Attempt(
        workflow_instance_id="wf-7f3d",
        task_def_name="features.build",
        task_id="9b4c",
        retry_count=2,
    )

    owner = WorkspaceOwner(worker_id="featuresBuild0001", pid=1234, token="owner-token")
    workspace_dir = prepare_attempt_workspace(tmp_path, task, owner)

    marker = workspace_dir / ATTEMPT_WORKSPACE_MARKER
    assert workspace_dir == tmp_path / "task_id=9b4c-exec=exec-1"
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_data.pop("started_at")
    assert marker_data == {
        "owner_pid": 1234,
        "owner_token": "owner-token",
        "owner_worker_id": "featuresBuild0001",
        "execution_id": "exec-1",
        "retry_count": 2,
        "task_def_name": "features.build",
        "task_id": "9b4c",
        "workflow_instance_id": "wf-7f3d",
    }

    cleanup_attempt_workspace(workspace_dir)

    assert not workspace_dir.exists()
    assert tmp_path.exists()


def test_preparing_duplicate_task_workspace_fails_closed(tmp_path) -> None:
    task = Attempt(
        workflow_instance_id="wf-7f3d",
        task_def_name="features.build",
        task_id="9b4c",
        retry_count=2,
    )
    owner = WorkspaceOwner(worker_id="featuresBuild0001", pid=1234, token="owner-token")
    prepare_attempt_workspace(tmp_path, task, owner)

    with pytest.raises(FileExistsError):
        prepare_attempt_workspace(tmp_path, task, owner)


def test_cleanup_requires_attempt_marker(tmp_path) -> None:
    workspace_dir = tmp_path / "not-owned"
    workspace_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="not a Perago attempt workspace"):
        cleanup_attempt_workspace(workspace_dir)

    assert workspace_dir.exists()


def test_safe_cleanup_logs_and_preserves_cleanup_failure(tmp_path) -> None:
    task = Attempt(
        workflow_instance_id="wf-7f3d",
        task_def_name="features.build",
        task_id="9b4c",
        retry_count=2,
    )
    workspace_dir = tmp_path / "not-owned"
    workspace_dir.mkdir()

    cleaned = cleanup_attempt_workspace_safely(workspace_dir, task)

    assert cleaned is False
    assert workspace_dir.exists()
