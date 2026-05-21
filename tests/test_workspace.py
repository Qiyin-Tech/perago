import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from perago import TaskDefinitionError, TaskInputError, WorkspaceSpec
from perago.workspace import (
    ATTEMPT_WORKSPACE_MARKER,
    build_workspace_sync_plan,
    cleanup_attempt_workspace,
    cleanup_attempt_workspace_safely,
    garbage_collect_attempt_workspaces,
    garbage_collect_workspace_owner,
    prepare_attempt_workspace,
    WorkspaceOwner,
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
    execution_id: str = "exec-1"


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


@pytest.mark.parametrize(
    ("prefix", "object_path"),
    [
        ("/", "C:/Users/Public/payload.py"),
        ("/", "C:evil/file.txt"),
        ("audio/render", "audio/render/C:/Users/Public/payload.py"),
        ("audio/render", "audio/render/C:evil/file.txt"),
    ],
)
def test_workspace_local_path_rejects_drive_qualified_strings(
    prefix: str, object_path: str
) -> None:
    with pytest.raises(TaskDefinitionError, match="drive-qualified"):
        workspace_local_path(WorkspaceSpec(prefix=prefix), object_path)


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
    marked = tmp_path / "task_id=1"
    marked.mkdir(parents=True)
    (marked / ATTEMPT_WORKSPACE_MARKER).write_text(
        json.dumps(
            {
                "workflow_instance_id": "wf",
                "task_id": "1",
                "execution_id": "exec-1",
                "retry_count": 0,
                "task_def_name": "task",
                "owner_worker_id": "worker0001",
                "owner_pid": 1234,
                "owner_token": "dead-token",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "file.txt").write_text("keep", encoding="utf-8")

    removed = sweep_abandoned_attempt_workspaces(tmp_path)

    assert removed == [marked]
    assert not marked.exists()
    assert keep.exists()


def test_gc_keeps_active_owners_and_removes_old_dead_owners(tmp_path) -> None:
    old_started_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    active_process = _marked_workspace(
        tmp_path,
        "active-process",
        owner_worker_id="worker0001",
        owner_pid=111,
        owner_token="process-token",
        started_at=old_started_at,
    )
    active_token = _marked_workspace(
        tmp_path,
        "active-token",
        owner_worker_id="worker0002",
        owner_pid=222,
        owner_token="token-live",
        started_at=old_started_at,
    )
    dead = _marked_workspace(
        tmp_path,
        "dead",
        owner_worker_id="worker0003",
        owner_pid=333,
        owner_token="token-dead",
        started_at=old_started_at,
    )
    young = _marked_workspace(
        tmp_path,
        "young",
        owner_worker_id="worker0004",
        owner_pid=444,
        owner_token="token-young",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    removed = garbage_collect_attempt_workspaces(
        tmp_path,
        ttl=timedelta(hours=1),
        active_process_owners={("worker0001", 111)},
        active_owner_tokens={"token-live"},
    )

    assert removed == [dead]
    assert active_process.exists()
    assert active_token.exists()
    assert not dead.exists()
    assert young.exists()


def test_targeted_gc_removes_only_dead_owner_workspaces(tmp_path) -> None:
    started_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    target = _marked_workspace(
        tmp_path,
        "target",
        owner_worker_id="worker0001",
        owner_pid=111,
        owner_token="target-token",
        started_at=started_at,
    )
    other = _marked_workspace(
        tmp_path,
        "other",
        owner_worker_id="worker0002",
        owner_pid=222,
        owner_token="other-token",
        started_at=started_at,
    )

    removed = garbage_collect_workspace_owner(tmp_path, owner_worker_id="worker0001", owner_pid=111)

    assert removed == [target]
    assert not target.exists()
    assert other.exists()


def test_gc_skips_legacy_and_bad_markers(tmp_path) -> None:
    legacy = tmp_path / "task_id=legacy"
    legacy.mkdir()
    (legacy / ATTEMPT_WORKSPACE_MARKER).write_text("{}", encoding="utf-8")
    bad = tmp_path / "task_id=bad"
    bad.mkdir()
    (bad / ATTEMPT_WORKSPACE_MARKER).write_text("{bad-json", encoding="utf-8")

    removed = garbage_collect_attempt_workspaces(
        tmp_path,
        ttl=timedelta(seconds=0),
        active_process_owners=set(),
        active_owner_tokens=set(),
    )

    assert removed == []
    assert legacy.exists()
    assert bad.exists()


def _marked_workspace(
    root: Path,
    task_id: str,
    *,
    owner_worker_id: str,
    owner_pid: int,
    owner_token: str,
    started_at: str,
) -> Path:
    workspace = root / f"task_id={task_id}"
    workspace.mkdir()
    (workspace / ATTEMPT_WORKSPACE_MARKER).write_text(
        json.dumps(
            {
                "workflow_instance_id": "wf",
                "task_id": task_id,
                "execution_id": f"exec-{task_id}",
                "retry_count": 0,
                "task_def_name": "task",
                "owner_worker_id": owner_worker_id,
                "owner_pid": owner_pid,
                "owner_token": owner_token,
                "started_at": started_at,
            }
        ),
        encoding="utf-8",
    )
    return workspace
