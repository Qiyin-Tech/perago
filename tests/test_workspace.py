import json
from dataclasses import dataclass

import pytest

from perago.workspace import (
    ATTEMPT_WORKSPACE_MARKER,
    cleanup_attempt_workspace,
    cleanup_attempt_workspace_safely,
    prepare_attempt_workspace,
    sweep_abandoned_attempt_workspaces,
)


@dataclass(frozen=True)
class Attempt:
    workflow_instance_id: str
    task_def_name: str
    task_id: str
    retry_count: int


def test_prepares_and_cleans_attempt_workspace(tmp_path) -> None:
    task = Attempt(
        workflow_instance_id="wf-7f3d",
        task_def_name="features.build",
        task_id="9b4c",
        retry_count=2,
    )

    workspace_dir = prepare_attempt_workspace(tmp_path, task)

    marker = workspace_dir / ATTEMPT_WORKSPACE_MARKER
    assert workspace_dir == tmp_path / "wf-7f3d" / "features.build" / "task_id=9b4c" / "retry_count=2"
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "retry_count": 2,
        "task_def_name": "features.build",
        "task_id": "9b4c",
        "workflow_instance_id": "wf-7f3d",
    }

    cleanup_attempt_workspace(workspace_dir)

    assert not workspace_dir.exists()


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


def test_sweep_removes_only_marked_attempt_workspaces(tmp_path) -> None:
    marked = tmp_path / "wf" / "task" / "task_id=1" / "retry_count=0"
    marked.mkdir(parents=True)
    (marked / ATTEMPT_WORKSPACE_MARKER).write_text("{}", encoding="utf-8")
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "file.txt").write_text("keep", encoding="utf-8")

    removed = sweep_abandoned_attempt_workspaces(tmp_path)

    assert removed == [marked]
    assert not marked.exists()
    assert keep.exists()
