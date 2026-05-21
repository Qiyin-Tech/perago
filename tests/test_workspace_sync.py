from pathlib import Path

import pytest

from perago import TaskInputError, WorkspaceSpec
from perago.workspace import (
    ATTEMPT_WORKSPACE_MARKER,
    WorkspaceUploadFile,
    build_workspace_sync_plan,
    workspace_delete_object_paths,
    workspace_download_files,
    workspace_upload_files,
)


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


def test_workspace_delete_object_paths_accepts_explicit_upload_records() -> None:
    uploaded = [WorkspaceUploadFile(Path("raw/input.wav"), "audio/render/raw/input.wav")]

    delete_paths = workspace_delete_object_paths(
        WorkspaceSpec(prefix="/audio/render"),
        ["audio/render/raw/input.wav", "audio/render/old.tmp"],
        uploaded,
    )

    assert delete_paths == ["audio/render/old.tmp"]
