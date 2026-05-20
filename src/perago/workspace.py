from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from loguru import logger

from perago._segments import safe_segment
from perago.errors import PublishBudgetError, TaskInputError
from perago.guards import _canonical_workspace_path
from perago.models import PublishBudget, WorkspaceSpec


ATTEMPT_WORKSPACE_MARKER = ".perago-attempt.json"


@dataclass(frozen=True)
class WorkspaceUploadFile:
    local_path: Path
    object_path: str


@dataclass(frozen=True)
class WorkspaceDownloadFile:
    object_path: str
    local_path: Path


@dataclass(frozen=True)
class WorkspaceSyncPlan:
    upload_files: list[WorkspaceUploadFile]
    delete_object_paths: list[str]

    @property
    def changed_object_count(self) -> int:
        return len(self.upload_files) + len(self.delete_object_paths)

    @property
    def upload_bytes(self) -> int:
        return sum(file.local_path.stat().st_size for file in self.upload_files)


def workspace_object_prefix(workspace_spec: WorkspaceSpec) -> str:
    if workspace_spec.prefix == "/":
        return ""
    return workspace_spec.prefix


def workspace_object_path(workspace_spec: WorkspaceSpec, workspace_path: str | PathLike[str]) -> str:
    local_path = _canonical_workspace_path(workspace_path)
    prefix = workspace_object_prefix(workspace_spec)
    if not prefix:
        return local_path
    return f"{prefix}/{local_path}"


def workspace_local_path(workspace_spec: WorkspaceSpec, object_path: str | PathLike[str]) -> Path | None:
    remote_path = _canonical_workspace_path(object_path)
    prefix = workspace_object_prefix(workspace_spec)
    if prefix:
        prefix_with_separator = f"{prefix}/"
        if not remote_path.startswith(prefix_with_separator):
            return None
        remote_path = remote_path.removeprefix(prefix_with_separator)

    local_path = Path(remote_path)
    if local_path.name == ATTEMPT_WORKSPACE_MARKER:
        return None
    return local_path


def attempt_workspace_dir(workspace_root: Path, task: object) -> Path:
    return (
        workspace_root
        / safe_segment(_task_attr(task, "workflow_instance_id"))
        / safe_segment(_task_attr(task, "task_def_name"))
        / f"task_id={safe_segment(_task_attr(task, 'task_id'))}"
        / f"retry_count={safe_segment(_task_attr(task, 'retry_count'))}"
    )


def prepare_attempt_workspace(workspace_root: Path, task: object) -> Path:
    workspace_dir = attempt_workspace_dir(workspace_root, task)
    workspace_dir.mkdir(parents=True, exist_ok=False)
    marker = {
        "workflow_instance_id": _task_attr(task, "workflow_instance_id"),
        "task_id": _task_attr(task, "task_id"),
        "retry_count": _task_attr(task, "retry_count"),
        "task_def_name": _task_attr(task, "task_def_name"),
    }
    (workspace_dir / ATTEMPT_WORKSPACE_MARKER).write_text(
        json.dumps(marker, sort_keys=True),
        encoding="utf-8",
    )
    return workspace_dir


def workspace_upload_files(workspace_dir: Path, workspace_spec: WorkspaceSpec) -> list[WorkspaceUploadFile]:
    files: list[WorkspaceUploadFile] = []
    for local_path in sorted(workspace_dir.rglob("*")):
        relative_path = local_path.relative_to(workspace_dir)
        if relative_path.name == ATTEMPT_WORKSPACE_MARKER:
            continue
        if local_path.is_symlink():
            raise TaskInputError(f"workspace publication does not support symlinks: {relative_path.as_posix()}")
        if not local_path.is_file():
            continue
        files.append(
            WorkspaceUploadFile(
                local_path=local_path,
                object_path=workspace_object_path(workspace_spec, relative_path),
            )
        )
    return files


def workspace_download_files(
    workspace_dir: Path,
    workspace_spec: WorkspaceSpec,
    object_paths: list[str],
) -> list[WorkspaceDownloadFile]:
    files: list[WorkspaceDownloadFile] = []
    for object_path in sorted(object_paths):
        local_path = workspace_local_path(workspace_spec, object_path)
        if local_path is None:
            continue
        files.append(
            WorkspaceDownloadFile(
                object_path=object_path,
                local_path=workspace_dir / local_path,
            )
        )
    return files


def workspace_delete_object_paths(
    workspace_spec: WorkspaceSpec,
    existing_object_paths: list[str],
    uploaded_files: list[WorkspaceUploadFile],
) -> list[str]:
    uploaded_object_paths = {file.object_path for file in uploaded_files}
    delete_paths: list[str] = []
    for object_path in sorted(existing_object_paths):
        if object_path in uploaded_object_paths:
            continue
        if workspace_local_path(workspace_spec, object_path) is None:
            continue
        delete_paths.append(object_path)
    return delete_paths


def build_workspace_sync_plan(
    workspace_dir: Path,
    workspace_spec: WorkspaceSpec,
    existing_object_paths: list[str],
) -> WorkspaceSyncPlan:
    upload_files = workspace_upload_files(workspace_dir, workspace_spec)
    return WorkspaceSyncPlan(
        upload_files=upload_files,
        delete_object_paths=workspace_delete_object_paths(
            workspace_spec,
            existing_object_paths,
            upload_files,
        ),
    )


def assert_workspace_sync_plan_within_budget(plan: WorkspaceSyncPlan, budget: PublishBudget) -> None:
    if plan.changed_object_count > budget.max_changed_objects:
        raise PublishBudgetError(
            f"workspace publication changes {plan.changed_object_count} objects, "
            f"exceeding max_changed_objects={budget.max_changed_objects}"
        )
    if plan.upload_bytes > budget.max_changed_bytes:
        raise PublishBudgetError(
            f"workspace publication uploads {plan.upload_bytes} bytes, "
            f"exceeding max_changed_bytes={budget.max_changed_bytes}"
        )


def cleanup_attempt_workspace(workspace_dir: Path) -> None:
    _require_attempt_marker(workspace_dir)
    shutil.rmtree(workspace_dir)


def cleanup_attempt_workspace_safely(workspace_dir: Path, task: object) -> bool:
    try:
        cleanup_attempt_workspace(workspace_dir)
    except OSError as exc:
        logger.bind(
            workspace_dir=str(workspace_dir),
            workflow_instance_id=_task_attr(task, "workflow_instance_id"),
            task_id=_task_attr(task, "task_id"),
            retry_count=_task_attr(task, "retry_count"),
        ).opt(exception=exc).error("failed to clean attempt-local workspace")
        return False
    return True


def sweep_abandoned_attempt_workspaces(workspace_root: Path) -> list[Path]:
    if not workspace_root.exists():
        return []

    removed: list[Path] = []
    for marker in sorted(workspace_root.rglob(ATTEMPT_WORKSPACE_MARKER)):
        workspace_dir = marker.parent
        _require_inside(workspace_root, workspace_dir)
        shutil.rmtree(workspace_dir)
        removed.append(workspace_dir)
    return removed


def _require_attempt_marker(workspace_dir: Path) -> None:
    marker = workspace_dir / ATTEMPT_WORKSPACE_MARKER
    if not marker.is_file():
        raise FileNotFoundError(f"{workspace_dir} is not a Perago attempt workspace")


def _require_inside(root: Path, child: Path) -> None:
    child.resolve().relative_to(root.resolve())


def _task_attr(task: object, name: str) -> Any:
    try:
        return getattr(task, name)
    except AttributeError as exc:
        raise AttributeError(f"task is missing required attribute {name}") from exc
