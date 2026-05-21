from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from loguru import logger

from perago._segments import safe_segment
from perago.workspace._internal import optional_task_attr, require_attempt_marker, task_attr
from perago.workspace.models import ATTEMPT_WORKSPACE_MARKER, WorkspaceOwner


_ACTIVE_OWNER_TOKENS: set[str] = set()
_ACTIVE_OWNER_TOKENS_LOCK = Lock()


def attempt_workspace_dir(workspace_root: Path, task: object) -> Path:
    task_part = f"task_id={safe_segment(task_attr(task, 'task_id'))}"
    execution_id = optional_task_attr(task, "execution_id")
    if execution_id is None:
        return workspace_root / task_part
    return workspace_root / f"{task_part}-exec={safe_segment(execution_id)}"


def new_workspace_owner(worker_id: str) -> WorkspaceOwner:
    return WorkspaceOwner(worker_id=worker_id, pid=os.getpid(), token=uuid4().hex)


def register_active_workspace_owner(owner: WorkspaceOwner) -> None:
    with _ACTIVE_OWNER_TOKENS_LOCK:
        _ACTIVE_OWNER_TOKENS.add(owner.token)


def unregister_active_workspace_owner(owner: WorkspaceOwner) -> None:
    with _ACTIVE_OWNER_TOKENS_LOCK:
        _ACTIVE_OWNER_TOKENS.discard(owner.token)


def active_workspace_owner_tokens() -> set[str]:
    with _ACTIVE_OWNER_TOKENS_LOCK:
        return set(_ACTIVE_OWNER_TOKENS)


def prepare_attempt_workspace(workspace_root: Path, task: object, owner: WorkspaceOwner | None = None) -> Path:
    if owner is None:
        owner = new_workspace_owner(os.environ.get("PERAGO_WORKER_ID", f"pid-{os.getpid()}"))
    workspace_dir = attempt_workspace_dir(workspace_root, task)
    workspace_dir.mkdir(parents=True, exist_ok=False)
    marker = {
        "workflow_instance_id": task_attr(task, "workflow_instance_id"),
        "task_id": task_attr(task, "task_id"),
        "execution_id": task_attr(task, "execution_id"),
        "retry_count": task_attr(task, "retry_count"),
        "task_def_name": task_attr(task, "task_def_name"),
        "owner_worker_id": owner.worker_id,
        "owner_pid": owner.pid,
        "owner_token": owner.token,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (workspace_dir / ATTEMPT_WORKSPACE_MARKER).write_text(
        json.dumps(marker, sort_keys=True),
        encoding="utf-8",
    )
    return workspace_dir


def cleanup_attempt_workspace(workspace_dir: Path) -> None:
    require_attempt_marker(workspace_dir)
    shutil.rmtree(workspace_dir)


def cleanup_attempt_workspace_safely(workspace_dir: Path, task: object) -> bool:
    try:
        cleanup_attempt_workspace(workspace_dir)
    except OSError as exc:
        logger.bind(
            workspace_dir=str(workspace_dir),
            workflow_instance_id=task_attr(task, "workflow_instance_id"),
            task_id=task_attr(task, "task_id"),
            retry_count=task_attr(task, "retry_count"),
        ).opt(exception=exc).error("failed to clean attempt-local workspace")
        return False
    return True
