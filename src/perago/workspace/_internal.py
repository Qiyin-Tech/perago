from __future__ import annotations

from pathlib import Path
from typing import Any

from perago.workspace.models import ATTEMPT_WORKSPACE_MARKER


def require_attempt_marker(workspace_dir: Path) -> None:
    marker = workspace_dir / ATTEMPT_WORKSPACE_MARKER
    if not marker.is_file():
        raise FileNotFoundError(f"{workspace_dir} is not a Perago attempt workspace")


def require_inside(root: Path, child: Path) -> None:
    child.resolve().relative_to(root.resolve())


def task_attr(task: object, name: str) -> Any:
    try:
        return getattr(task, name)
    except AttributeError as exc:
        raise AttributeError(f"task is missing required attribute {name}") from exc


def optional_task_attr(task: object, name: str) -> str | None:
    value = getattr(task, name, None)
    if value is None:
        return None
    return str(value)
