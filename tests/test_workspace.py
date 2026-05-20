import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from perago import PublishBudget, PublishBudgetError, TaskDefinitionError, TaskInputError, WorkspaceSpec
from perago.workspace import (
    ATTEMPT_WORKSPACE_MARKER,
    assert_workspace_sync_plan_within_budget,
    build_workspace_sync_plan,
    cleanup_attempt_workspace,
    cleanup_attempt_workspace_safely,
    prepare_attempt_workspace,
    sweep_abandoned_attempt_workspaces,
    workspace_delete_object_paths,
    workspace_download_files,
    workspace_local_path,
    workspace_object_path,
    workspace_object_prefix,
    workspace_upload_files,
)


@dataclass(frozen=True)
class Attempt:
    workflow_instance_id: str
    task_def_name: str
    task_id: str
    retry_count: int


def test_workspace_object_prefix_maps_root_prefix_to_empty_object_prefix() -> None:
    assert workspace_object_prefix(WorkspaceSpec(prefix="/")) == ""
    assert workspace_object_prefix(WorkspaceSpec(prefix="/audio/render")) == "audio/render"


def test_workspace_object_path_maps_local_paths_under_workspace_prefix() -> None:
    spec = WorkspaceSpec(prefix="audio/render")

    assert workspace_object_path(spec, "raw/input.wav") == "audio/render/raw/input.wav"
    assert workspace_object_path(spec, Path("stems") / "voice.wav") == "audio/render/stems/voice.wav"


def test_workspace_object_path_keeps_root_prefix_at_repository_root() -> None:
    assert workspace_object_path(WorkspaceSpec(prefix="/"), "manifest.json") == "manifest.json"


def test_workspace_local_path_maps_object_paths_under_prefix() -> None:
    spec = WorkspaceSpec(prefix="audio/render")

    assert workspace_local_path(spec, "audio/render/raw/input.wav") == Path("raw/input.wav")
    assert workspace_local_path(spec, "other/raw/input.wav") is None


def test_workspace_local_path_keeps_root_prefix_at_repository_root() -> None:
    assert workspace_local_path(WorkspaceSpec(prefix="/"), "manifest.json") == Path("manifest.json")


def test_workspace_local_path_skips_attempt_marker() -> None:
    assert workspace_local_path(WorkspaceSpec(prefix="/"), ATTEMPT_WORKSPACE_MARKER) is None
    assert workspace_local_path(WorkspaceSpec(prefix="/audio/render"), f"audio/render/{ATTEMPT_WORKSPACE_MARKER}") is None


def test_workspace_object_path_rejects_paths_that_escape_workspace_root() -> None:
    with pytest.raises(TaskDefinitionError, match="relative"):
        workspace_object_path(WorkspaceSpec(prefix="/"), "../manifest.json")


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


def test_workspace_upload_files_map_local_files_under_prefix_and_skip_marker(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / ATTEMPT_WORKSPACE_MARKER).write_text("{}", encoding="utf-8")
    (workspace_dir / "raw").mkdir()
    (workspace_dir / "raw" / "input.wav").write_text("ok", encoding="utf-8")
    (workspace_dir / "features").mkdir()
    (workspace_dir / "features" / "out.parquet").write_text("ok", encoding="utf-8")

    files = workspace_upload_files(workspace_dir, WorkspaceSpec(prefix="/audio/render"))

    assert [file.local_path.relative_to(workspace_dir) for file in files] == [
        Path("features/out.parquet"),
        Path("raw/input.wav"),
    ]
    assert [file.object_path for file in files] == [
        "audio/render/features/out.parquet",
        "audio/render/raw/input.wav",
    ]


def test_workspace_upload_files_reject_symlinks(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace_dir / "escape.txt").symlink_to(outside)

    with pytest.raises(TaskInputError, match="does not support symlinks"):
        workspace_upload_files(workspace_dir, WorkspaceSpec(prefix="/audio/render"))


def test_workspace_download_files_filter_to_prefix_and_skip_marker(tmp_path) -> None:
    files = workspace_download_files(
        tmp_path / "workspace",
        WorkspaceSpec(prefix="/audio/render"),
        [
            "audio/render/raw/input.wav",
            "audio/render/.perago-attempt.json",
            "audio/other/input.wav",
            "audio/render/features/out.parquet",
        ],
    )

    assert [(file.object_path, file.local_path.relative_to(tmp_path / "workspace")) for file in files] == [
        ("audio/render/features/out.parquet", Path("features/out.parquet")),
        ("audio/render/raw/input.wav", Path("raw/input.wav")),
    ]


def test_workspace_delete_object_paths_only_removes_stale_objects_under_prefix(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "raw").mkdir()
    (workspace_dir / "raw" / "input.wav").write_text("ok", encoding="utf-8")
    uploaded = workspace_upload_files(workspace_dir, WorkspaceSpec(prefix="/audio/render"))

    delete_paths = workspace_delete_object_paths(
        WorkspaceSpec(prefix="/audio/render"),
        [
            "audio/render/raw/input.wav",
            "audio/render/old.tmp",
            "audio/render/.perago-attempt.json",
            "other/old.tmp",
        ],
        uploaded,
    )

    assert delete_paths == ["audio/render/old.tmp"]


def test_workspace_sync_plan_combines_uploads_and_stale_deletes(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / ATTEMPT_WORKSPACE_MARKER).write_text("{}", encoding="utf-8")
    (workspace_dir / "raw").mkdir()
    (workspace_dir / "raw" / "input.wav").write_text("ok", encoding="utf-8")

    plan = build_workspace_sync_plan(
        workspace_dir,
        WorkspaceSpec(prefix="/audio/render"),
        [
            "audio/render/raw/input.wav",
            "audio/render/old.tmp",
            "other/old.tmp",
        ],
    )

    assert [(file.local_path.relative_to(workspace_dir), file.object_path) for file in plan.upload_files] == [
        (Path("raw/input.wav"), "audio/render/raw/input.wav"),
    ]
    assert plan.delete_object_paths == ["audio/render/old.tmp"]


def test_workspace_sync_plan_reports_changed_objects_and_upload_bytes(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "raw").mkdir()
    (workspace_dir / "raw" / "input.wav").write_bytes(b"abcd")

    plan = build_workspace_sync_plan(
        workspace_dir,
        WorkspaceSpec(prefix="/audio/render"),
        ["audio/render/old.tmp"],
    )

    assert plan.changed_object_count == 2
    assert plan.upload_bytes == 4


def test_workspace_sync_plan_budget_rejects_object_and_byte_overruns(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "raw").mkdir()
    (workspace_dir / "raw" / "input.wav").write_bytes(b"abcd")
    plan = build_workspace_sync_plan(
        workspace_dir,
        WorkspaceSpec(prefix="/audio/render"),
        ["audio/render/old.tmp"],
    )

    base_budget = {
        "observed_merge_p99_seconds": 1,
        "safety_margin_seconds": 1,
        "lakefs_merge_timeout_seconds": 2,
        "conductor_completion_timeout_seconds": 1,
        "worker_shutdown_grace_seconds": 1,
        "heartbeat_interval_seconds": 1,
    }
    with pytest.raises(PublishBudgetError, match="max_changed_objects"):
        assert_workspace_sync_plan_within_budget(
            plan,
            PublishBudget(max_changed_objects=1, max_changed_bytes=4, **base_budget),
        )
    with pytest.raises(PublishBudgetError, match="max_changed_bytes"):
        assert_workspace_sync_plan_within_budget(
            plan,
            PublishBudget(max_changed_objects=2, max_changed_bytes=3, **base_budget),
        )

    assert_workspace_sync_plan_within_budget(
        plan,
        PublishBudget(max_changed_objects=2, max_changed_bytes=4, **base_budget),
    )


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
