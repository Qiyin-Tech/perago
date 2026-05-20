from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from perago.errors import PublishFenceError
from perago.models import WorkspaceInput, WorkspaceSpec


@dataclass(frozen=True)
class WorkspacePublicationPlan:
    """Capture the metadata and fence decisions for one workspace publish.

    Parameters
    ----------
    logical_task_key : str
        Stable workflow-scoped identity used by publish fences to decide whether
        branch advancement still belongs to the same logical task.
    staging_branch : str
        Internal LakeFS branch name that holds the staged attempt output before
        the publish step merges it into the target branch.
    publish_base_head : str
        Commit that the publish step expects to be the head of the target branch
        when the merge is attempted.
    superseded_commit : str | None
        Previous head commit superseded by the same logical task. ``None`` means
        the target branch has not advanced beyond the original input ref.
    try_metadata : dict[str, str]
        Metadata written onto the staging branch commit during the try phase.
    confirm_metadata : dict[str, str]
        Metadata written onto the publish merge commit during the confirm phase.
    """

    logical_task_key: str
    staging_branch: str
    publish_base_head: str
    superseded_commit: str | None
    try_metadata: dict[str, str]
    confirm_metadata: dict[str, str]


def logical_task_key(task: object) -> str:
    """Build the stable publish-fence identity for a Conductor task attempt.

    Parameters
    ----------
    task : object
        Attempt-like object exposing ``workflow_instance_id``,
        ``reference_task_name``, ``seq``, ``iteration``, and ``task_def_name``.

    Returns
    -------
    str
        Colon-delimited logical task key shared by retries of the same logical
        workflow step.

    Raises
    ------
    AttributeError
        Raised when ``task`` is missing any required workflow identity field.
    """

    parts = [
        _task_attr(task, "workflow_instance_id"),
        _task_attr(task, "reference_task_name"),
        str(_task_attr(task, "seq")),
        str(_task_attr(task, "iteration")),
        _task_attr(task, "task_def_name"),
    ]
    return ":".join(parts)


def metadata_value(value: object) -> str:
    """Serialize a LakeFS metadata value into the string form LakeFS stores.

    Parameters
    ----------
    value : object
        Metadata value candidate. ``None`` becomes an empty string, strings are
        preserved, and other JSON-serializable values are encoded with stable
        separators and key ordering.

    Returns
    -------
    str
        String value suitable for LakeFS metadata maps.

    Raises
    ------
    TypeError
        Raised when ``value`` is not JSON-serializable and is not already a
        string.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def perago_metadata(
    *,
    task: object,
    workspace: WorkspaceInput | dict[str, Any],
    workspace_spec: WorkspaceSpec,
    logical_task_key: str,
    phase: str,
    extra: dict[str, object] | None = None,
) -> dict[str, str]:
    """Build the Perago metadata map for one workspace transaction phase.

    Parameters
    ----------
    task : object
        Attempt-like object exposing the workflow identity, task identity, and
        retry attributes recorded in Perago metadata.
    workspace : WorkspaceInput | dict[str, Any]
        Workspace input reference for the attempt. Dictionaries are validated
        through :class:`perago.WorkspaceInput`.
    workspace_spec : WorkspaceSpec
        Workspace contract whose normalized prefix is recorded in metadata.
    logical_task_key : str
        Stable logical task identity, typically from
        :func:`perago.logical_task_key`.
    phase : str
        Transaction phase marker such as ``"try"`` or ``"confirm"``.
    extra : dict[str, object] | None, optional
        Additional metadata values merged into the standard Perago keys before
        string serialization.

    Returns
    -------
    dict[str, str]
        Metadata map with all values converted to LakeFS-compatible strings.

    Raises
    ------
    AttributeError
        Raised when ``task`` is missing required attempt attributes.
    pydantic.ValidationError
        Raised when ``workspace`` cannot be validated as
        :class:`perago.WorkspaceInput`.
    TypeError
        Raised when any metadata value is not JSON-serializable.
    """

    workspace_input = WorkspaceInput.model_validate(workspace)
    data: dict[str, object] = {
        "perago.phase": phase,
        "perago.logical_task_key": logical_task_key,
        "perago.workflow_instance_id": _task_attr(task, "workflow_instance_id"),
        "perago.task_def_name": _task_attr(task, "task_def_name"),
        "perago.reference_task_name": _task_attr(task, "reference_task_name"),
        "perago.seq": _task_attr(task, "seq"),
        "perago.iteration": _task_attr(task, "iteration"),
        "perago.input_ref": workspace_input.ref,
        "perago.target_branch": workspace_input.branch,
        "perago.prefix": workspace_spec.prefix,
        "perago.task_id": _task_attr(task, "task_id"),
        "perago.retry_count": _task_attr(task, "retry_count"),
        "perago.retried_task_id": getattr(task, "retried_task_id", None),
    }
    if extra:
        data.update(extra)
    return {key: metadata_value(value) for key, value in data.items()}


def choose_publish_base(
    *,
    workspace: WorkspaceInput | dict[str, Any],
    current_head: str,
    commits: Sequence[object],
    logical_task_key: str,
) -> tuple[str, str | None]:
    """Choose the publish base that the current attempt is allowed to merge on.

    Parameters
    ----------
    workspace : WorkspaceInput | dict[str, Any]
        Original workspace input for the attempt. Dictionaries are validated as
        :class:`perago.WorkspaceInput`.
    current_head : str
        Current head commit of the target branch at publish time.
    commits : Sequence[object]
        Commit range between the original input ref and ``current_head``. Each
        commit must expose ``id`` and optional ``metadata`` either as attributes
        or mapping keys.
    logical_task_key : str
        Stable task identity that is allowed to advance the branch without
        tripping the publish fence.

    Returns
    -------
    tuple[str, str | None]
        Pair of ``(publish_base_head, superseded_commit)``. ``superseded_commit``
        is ``None`` when the branch has not advanced beyond the input ref.

    Raises
    ------
    PublishFenceError
        Raised when the target branch advanced with commits that cannot all be
        attributed to ``logical_task_key``.
    pydantic.ValidationError
        Raised when ``workspace`` cannot be validated as
        :class:`perago.WorkspaceInput`.
    """

    workspace_input = WorkspaceInput.model_validate(workspace)
    if current_head == workspace_input.ref:
        return current_head, None

    if (
        commits
        and _commit_id(commits[-1]) == current_head
        and all(
            _commit_metadata(commit).get("perago.logical_task_key") == logical_task_key
            for commit in commits
        )
    ):
        return current_head, _commit_id(commits[-1])

    raise PublishFenceError(
        f"{workspace_input.branch} advanced from {workspace_input.ref} to {current_head}"
    )


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
    ]
    return "-".join(parts)


def confirm_metadata_extra(
    *,
    staging_branch: str,
    staging_commit: str,
    expected_head: str,
    superseded_commit: str | None,
) -> dict[str, object]:
    """Build extra metadata written during the confirm/publish phase.

    Parameters
    ----------
    staging_branch : str
        Internal staging branch merged during publish.
    staging_commit : str
        Commit id produced by staging before publish.
    expected_head : str
        Target branch head that the publish fence expects to still be current.
    superseded_commit : str | None
        Previous head commit replaced by the same logical task, if any.

    Returns
    -------
    dict[str, object]
        Extra metadata entries merged into the confirm-phase Perago metadata.
    """

    return {
        "perago.staging_branch": staging_branch,
        "perago.staging_commit": staging_commit,
        "perago.expected_head": expected_head,
        "perago.supersedes": superseded_commit,
    }


def build_workspace_publication_plan(
    *,
    task: object,
    workspace: WorkspaceInput | dict[str, Any],
    workspace_spec: WorkspaceSpec,
    current_head: str,
    commits: Sequence[object],
    staging_commit: str,
) -> WorkspacePublicationPlan:
    """Assemble the full publication plan for a workspace task attempt.

    Parameters
    ----------
    task : object
        Attempt-like object exposing workflow identity, task identity, and retry
        fields used for metadata and staging-branch naming.
    workspace : WorkspaceInput | dict[str, Any]
        Workspace input reference for the attempt.
    workspace_spec : WorkspaceSpec
        Workspace contract whose normalized prefix is recorded in metadata.
    current_head : str
        Current target branch head observed immediately before publish.
    commits : Sequence[object]
        Commit range between the original workspace ref and ``current_head``.
    staging_commit : str
        Commit id produced after staging the local workspace content.

    Returns
    -------
    WorkspacePublicationPlan
        Immutable plan containing publish-fence decisions and both metadata maps.

    Raises
    ------
    PublishFenceError
        Raised when the current branch advancement cannot be attributed to the
        same logical task.
    AttributeError
        Raised when ``task`` is missing required attempt attributes.
    pydantic.ValidationError
        Raised when ``workspace`` cannot be validated as
        :class:`perago.WorkspaceInput`.
    TypeError
        Raised when metadata values cannot be serialized.
    """

    key = logical_task_key(task)
    publish_base_head, superseded_commit = choose_publish_base(
        workspace=workspace,
        current_head=current_head,
        commits=commits,
        logical_task_key=key,
    )
    staging_branch = staging_branch_name(task)
    return WorkspacePublicationPlan(
        logical_task_key=key,
        staging_branch=staging_branch,
        publish_base_head=publish_base_head,
        superseded_commit=superseded_commit,
        try_metadata=perago_metadata(
            task=task,
            workspace=workspace,
            workspace_spec=workspace_spec,
            logical_task_key=key,
            phase="try",
        ),
        confirm_metadata=perago_metadata(
            task=task,
            workspace=workspace,
            workspace_spec=workspace_spec,
            logical_task_key=key,
            phase="confirm",
            extra=confirm_metadata_extra(
                staging_branch=staging_branch,
                staging_commit=staging_commit,
                expected_head=publish_base_head,
                superseded_commit=superseded_commit,
            ),
        ),
    )


def find_matching_publication_commit(
    commits: Sequence[object],
    *,
    logical_task_key: str,
    task_id: str,
    staging_commit: str,
) -> str | None:
    """Find the published commit that matches one staged workspace attempt.

    Parameters
    ----------
    commits : Sequence[object]
        Candidate commits from the target branch history. Each commit must expose
        ``id`` and optional ``metadata`` either as attributes or mapping keys.
    logical_task_key : str
        Stable logical task identity that must match the publish metadata.
    task_id : str
        Concrete Conductor task attempt id that must match the publish metadata.
    staging_commit : str
        Staging commit id that must match the publish metadata.

    Returns
    -------
    str | None
        Matching published commit id, or ``None`` when no commit satisfies the
        full metadata match.
    """

    for commit in commits:
        metadata = _commit_metadata(commit)
        if (
            metadata.get("perago.logical_task_key") == logical_task_key
            and metadata.get("perago.task_id") == task_id
            and metadata.get("perago.staging_commit") == staging_commit
        ):
            return _commit_id(commit)
    return None


def _commit_id(commit: object) -> str:
    if isinstance(commit, Mapping):
        return str(commit["id"])
    return str(getattr(commit, "id"))


def _commit_metadata(commit: object) -> Mapping[str, str]:
    if isinstance(commit, Mapping):
        metadata = commit.get("metadata", {})
    else:
        metadata = getattr(commit, "metadata", {})
    if not isinstance(metadata, Mapping):
        return {}
    return metadata


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
