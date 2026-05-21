from __future__ import annotations

import re


def staging_branch_name(task: object) -> str:
    """Build the internal LakeFS staging branch name for one attempt.

    Parameters
    ----------
    task : object
        Attempt-like object exposing workflow, task, and retry identity fields.

    Returns
    -------
    str
        LakeFS-safe branch name scoped to one concrete task attempt.

    Raises
    ------
    AttributeError
        Raised when ``task`` is missing required identity fields.
    """

    parts = [
        "perago",
        "staging",
        _lakefs_branch_segment(_task_attr(task, "workflow_instance_id")),
        _lakefs_branch_segment(_task_attr(task, "reference_task_name")),
        f"seq-{_lakefs_branch_segment(_task_attr(task, 'seq'))}",
        f"iteration-{_lakefs_branch_segment(_task_attr(task, 'iteration'))}",
        f"task-id-{_lakefs_branch_segment(_task_attr(task, 'task_id'))}",
        f"retry-{_lakefs_branch_segment(_task_attr(task, 'retry_count'))}",
        f"exec-{_lakefs_branch_segment(_task_attr(task, 'execution_id'))}",
    ]
    return "-".join(parts)


def _task_attr(task: object, name: str) -> object:
    try:
        return getattr(task, name)
    except AttributeError as exc:
        raise AttributeError(f"task is missing required attribute {name}") from exc


def _lakefs_branch_segment(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value)).strip("-_")
    if not text or text.startswith("-"):
        return "unknown"
    return text
