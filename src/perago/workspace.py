from __future__ import annotations

import json
import shutil
from os import PathLike
from pathlib import Path
from typing import Any

from loguru import logger

from perago._segments import safe_segment
from perago.guards import _canonical_workspace_path
from perago.models import WorkspaceSpec


ATTEMPT_WORKSPACE_MARKER = ".perago-attempt.json"


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
