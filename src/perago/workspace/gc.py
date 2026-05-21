from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from perago.workspace._internal import require_inside
from perago.workspace.lifecycle import active_workspace_owner_tokens
from perago.workspace.models import ATTEMPT_WORKSPACE_MARKER


def sweep_abandoned_attempt_workspaces(workspace_root: Path) -> list[Path]:
    return garbage_collect_attempt_workspaces(
        workspace_root,
        ttl=timedelta(seconds=0),
        active_process_owners=set(),
        active_owner_tokens=active_workspace_owner_tokens(),
    )


def garbage_collect_workspace_owner(
    workspace_root: Path,
    *,
    owner_worker_id: str,
    owner_pid: int,
) -> list[Path]:
    return garbage_collect_attempt_workspaces(
        workspace_root,
        ttl=timedelta(seconds=0),
        active_process_owners=set(),
        active_owner_tokens=set(),
        target_process_owner=(owner_worker_id, owner_pid),
    )


def garbage_collect_attempt_workspaces(
    workspace_root: Path,
    *,
    ttl: timedelta,
    active_process_owners: set[tuple[str, int]] | None = None,
    active_owner_tokens: set[str] | None = None,
    target_process_owner: tuple[str, int] | None = None,
    now: datetime | None = None,
) -> list[Path]:
    if not workspace_root.exists():
        return []

    current_time = now or datetime.now(timezone.utc)
    process_owners = active_process_owners or set()
    owner_tokens = active_owner_tokens if active_owner_tokens is not None else active_workspace_owner_tokens()
    removed: list[Path] = []
    for marker in sorted(workspace_root.rglob(ATTEMPT_WORKSPACE_MARKER)):
        workspace_dir = marker.parent
        require_inside(workspace_root, workspace_dir)
        marker_data = _read_gc_marker(marker)
        if marker_data is None:
            continue
        owner_worker_id = marker_data["owner_worker_id"]
        owner_pid = marker_data["owner_pid"]
        owner_token = marker_data["owner_token"]
        if target_process_owner is not None and (owner_worker_id, owner_pid) != target_process_owner:
            continue
        if (owner_worker_id, owner_pid) in process_owners:
            continue
        if owner_token in owner_tokens:
            continue
        started_at = marker_data["started_at"]
        if current_time - started_at < ttl:
            continue
        shutil.rmtree(workspace_dir)
        removed.append(workspace_dir)
    return removed


def _read_gc_marker(marker: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        owner_worker_id = data["owner_worker_id"]
        owner_pid = data["owner_pid"]
        owner_token = data["owner_token"]
        execution_id = data["execution_id"]
        started_at = data["started_at"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(owner_worker_id, str) or not owner_worker_id:
        return None
    if not isinstance(owner_pid, int):
        return None
    if not isinstance(owner_token, str) or not owner_token:
        return None
    if not isinstance(execution_id, str) or not execution_id:
        return None
    if not isinstance(started_at, str):
        return None
    try:
        parsed_started_at = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if parsed_started_at.tzinfo is None:
        parsed_started_at = parsed_started_at.replace(tzinfo=timezone.utc)
    return {
        "owner_worker_id": owner_worker_id,
        "owner_pid": owner_pid,
        "owner_token": owner_token,
        "execution_id": execution_id,
        "started_at": parsed_started_at,
    }
