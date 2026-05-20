from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from loguru import logger

from perago._segments import safe_segment
from perago.errors import TaskInputError
from perago.guards import _canonical_workspace_path
from perago.models import WorkspaceSpec


ATTEMPT_WORKSPACE_MARKER = ".perago-attempt.json"


@dataclass(frozen=True)
class WorkspaceUploadFile:
    """
    Local file that should be uploaded into a workspace prefix.

    ``WorkspaceUploadFile`` is produced while staging a workspace task output.
    The record keeps the local file selected from the attempt workspace next to
    the LakeFS object path that will receive its contents.

    Parameters
    ----------
    local_path : pathlib.Path
        Absolute or attempt-workspace-relative path to the local file selected
        for upload.
    object_path : str
        LakeFS object path where the file should be written. The path already
        includes the task's ``WorkspaceSpec.prefix``.

    See Also
    --------
    workspace_upload_files : Build upload records from a local workspace.
    WorkspaceSyncPlan : Combine upload records with stale-object deletes.

    Notes
    -----
    This is a runtime planning object. Task authors normally interact with the
    local workspace directory instead of constructing upload records directly.

    Examples
    --------
    >>> record = WorkspaceUploadFile(Path("raw/input.wav"), "audio/render/raw/input.wav")
    >>> record.object_path
    'audio/render/raw/input.wav'
    """

    local_path: Path
    object_path: str


@dataclass(frozen=True)
class WorkspaceDownloadFile:
    """
    Remote object that should be downloaded into an attempt workspace.

    ``WorkspaceDownloadFile`` is produced from the object listing for a
    workspace input ref. The destination path always points under the
    attempt-local workspace directory.

    Parameters
    ----------
    object_path : str
        LakeFS object path selected from the input workspace ref.
    local_path : pathlib.Path
        Destination path under the attempt-local workspace root after removing
        the task's ``WorkspaceSpec.prefix``.

    See Also
    --------
    workspace_download_files : Build download records from LakeFS object paths.
    workspace_local_path : Convert a remote object path to a local path.

    Notes
    -----
    Objects outside the task prefix are not represented by this class because
    they are filtered before the download plan is returned.

    Examples
    --------
    >>> record = WorkspaceDownloadFile("audio/render/raw/input.wav", Path("raw/input.wav"))
    >>> record.local_path
    PosixPath('raw/input.wav')
    """

    object_path: str
    local_path: Path


@dataclass(frozen=True)
class WorkspaceSyncPlan:
    """
    Plan for synchronizing an attempt workspace to a LakeFS prefix.

    Runtime code uses this plan during the stage phase of workspace
    publication. Uploads and deletes are calculated together so the staging
    branch mirrors the complete local projection for the task prefix.

    Parameters
    ----------
    upload_files : list of WorkspaceUploadFile
        Local files that should be uploaded to the staging branch.
    delete_object_paths : list of str
        Existing object paths under the workspace prefix that should be deleted
        from the staging branch because they are absent locally.

    Attributes
    ----------
    changed_object_count : int
        Number of uploaded plus deleted objects represented by the plan.
    upload_bytes : int
        Total size, in bytes, of the files selected for upload.

    See Also
    --------
    build_workspace_sync_plan : Build the complete plan from local and remote state.
    WorkspaceUploadFile : One file selected for upload.

    Notes
    -----
    The plan represents the complete projected contents of a
    ``WorkspaceSpec.prefix``. It is not an append-only list of files created by
    the current task body.

    Examples
    --------
    >>> plan = WorkspaceSyncPlan(
    ...     upload_files=[WorkspaceUploadFile(Path("raw/input.wav"), "audio/render/raw/input.wav")],
    ...     delete_object_paths=["audio/render/old.tmp"],
    ... )
    >>> plan.changed_object_count
    2
    """

    upload_files: list[WorkspaceUploadFile]
    delete_object_paths: list[str]

    @property
    def changed_object_count(self) -> int:
        """Number of remote object changes in this sync plan."""
        return len(self.upload_files) + len(self.delete_object_paths)

    @property
    def upload_bytes(self) -> int:
        """Total byte size of files selected for upload."""
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
    """
    Map a LakeFS object path to a local workspace path.

    The workspace prefix is removed from visible remote paths before they are
    exposed to the task body. Objects outside the prefix remain invisible to the
    local attempt workspace.

    Parameters
    ----------
    workspace_spec : WorkspaceSpec
        Workspace declaration whose ``prefix`` defines the visible LakeFS
        object subtree.
    object_path : str or os.PathLike[str]
        LakeFS object path to map into the local attempt workspace.

    Returns
    -------
    pathlib.Path or None
        Workspace-relative local path when the object is inside the prefix.
        ``None`` is returned for objects outside the prefix and for Perago's
        attempt marker file.

    Raises
    ------
    TaskDefinitionError
        If ``object_path`` is not a safe relative POSIX path.

    See Also
    --------
    workspace_download_files : Build download records from object paths.
    WorkspaceSpec : Declares the workspace prefix.

    Examples
    --------
    >>> workspace_local_path(WorkspaceSpec(prefix="/audio/render"), "audio/render/raw/input.wav")
    PosixPath('raw/input.wav')
    >>> workspace_local_path(WorkspaceSpec(prefix="/audio/render"), "other/raw/input.wav") is None
    True
    """
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
    """
    List local files that should be uploaded for a workspace publication.

    The local workspace is scanned recursively and mapped to LakeFS object paths
    under the task prefix. Perago's attempt marker is implementation state and
    is never published as a workspace object.

    Parameters
    ----------
    workspace_dir : pathlib.Path
        Attempt-local workspace root to scan recursively.
    workspace_spec : WorkspaceSpec
        Workspace declaration whose ``prefix`` is prepended to every uploaded
        object path.

    Returns
    -------
    list of WorkspaceUploadFile
        Upload records sorted by local path. Directories and Perago's attempt
        marker file are skipped.

    Raises
    ------
    TaskInputError
        If the local workspace contains a symbolic link. Workspace publication
        only supports regular files.
    TaskDefinitionError
        If a scanned relative path cannot be represented as a safe workspace
        object path.

    See Also
    --------
    build_workspace_sync_plan : Combine uploads with stale remote deletes.
    workspace_delete_object_paths : Find stale remote objects.

    Examples
    --------
    >>> files = workspace_upload_files(workspace_dir, WorkspaceSpec(prefix="/audio/render"))
    >>> [file.object_path for file in files]
    ['audio/render/raw/input.wav']
    """
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
    """
    Build download records for objects visible to a workspace task.

    Download planning applies the task's workspace prefix before constructing
    local destination paths. The returned records are enough for runtime code to
    fetch object contents without exposing prefix-outside paths to task code.

    Parameters
    ----------
    workspace_dir : pathlib.Path
        Attempt-local workspace root where files should be written.
    workspace_spec : WorkspaceSpec
        Workspace declaration whose ``prefix`` filters the remote object list.
    object_paths : list of str
        LakeFS object paths listed from the input workspace ref.

    Returns
    -------
    list of WorkspaceDownloadFile
        Download records sorted by object path. Objects outside the prefix and
        Perago's attempt marker file are omitted.

    Raises
    ------
    TaskDefinitionError
        If any object path cannot be represented as a safe workspace path.

    See Also
    --------
    workspace_local_path : Map one object path to a local path.

    Examples
    --------
    >>> files = workspace_download_files(
    ...     workspace_dir,
    ...     WorkspaceSpec(prefix="/audio/render"),
    ...     ["audio/render/raw/input.wav", "other/input.wav"],
    ... )
    >>> [file.local_path.relative_to(workspace_dir).as_posix() for file in files]
    ['raw/input.wav']
    """
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
    """
    Find stale remote objects that should be deleted from a staging branch.

    The delete list is restricted to the task's workspace prefix. It removes
    remote objects that still exist on the staging branch but no longer appear
    in the local attempt workspace.

    Parameters
    ----------
    workspace_spec : WorkspaceSpec
        Workspace declaration whose ``prefix`` limits the delete scope.
    existing_object_paths : list of str
        Object paths currently present under the staging branch.
    uploaded_files : list of WorkspaceUploadFile
        Upload records generated from the local attempt workspace.

    Returns
    -------
    list of str
        Existing object paths under the workspace prefix that are absent from
        the upload list. Objects outside the prefix and Perago's attempt marker
        file are not returned.

    Raises
    ------
    TaskDefinitionError
        If an existing object path cannot be represented as a safe workspace
        path.

    See Also
    --------
    workspace_upload_files : Build the upload side of the sync plan.
    build_workspace_sync_plan : Build the complete upload/delete plan.

    Examples
    --------
    >>> uploaded = [WorkspaceUploadFile(Path("raw/input.wav"), "audio/render/raw/input.wav")]
    >>> workspace_delete_object_paths(
    ...     WorkspaceSpec(prefix="/audio/render"),
    ...     ["audio/render/raw/input.wav", "audio/render/old.tmp"],
    ...     uploaded,
    ... )
    ['audio/render/old.tmp']
    """
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
    """
    Build a complete sync plan for a workspace prefix.

    This is the high-level planning helper used by LakeFS staging code. It
    combines the local upload records with remote stale-object deletes for the
    same prefix.

    Parameters
    ----------
    workspace_dir : pathlib.Path
        Attempt-local workspace root after the task body has finished.
    workspace_spec : WorkspaceSpec
        Workspace declaration whose ``prefix`` defines the LakeFS projection to
        synchronize.
    existing_object_paths : list of str
        Object paths currently present on the staging branch before uploading
        the new local workspace contents.

    Returns
    -------
    WorkspaceSyncPlan
        Upload and delete operations needed to make the staging branch prefix
        match the local attempt workspace.

    Raises
    ------
    TaskInputError
        If the local workspace contains a symbolic link.
    TaskDefinitionError
        If a local or remote path is not a valid workspace-relative path.

    See Also
    --------
    workspace_upload_files : Discover local files selected for upload.
    workspace_delete_object_paths : Discover remote objects selected for deletion.
    WorkspaceSyncPlan : Return type containing both operation lists.

    Notes
    -----
    This helper treats the local workspace as the desired state for the whole
    prefix. Remote objects under the prefix that are not present locally are
    scheduled for deletion.

    Examples
    --------
    >>> plan = build_workspace_sync_plan(
    ...     workspace_dir,
    ...     WorkspaceSpec(prefix="/audio/render"),
    ...     ["audio/render/old.tmp"],
    ... )
    >>> plan.changed_object_count
    1
    """
    upload_files = workspace_upload_files(workspace_dir, workspace_spec)
    return WorkspaceSyncPlan(
        upload_files=upload_files,
        delete_object_paths=workspace_delete_object_paths(
            workspace_spec,
            existing_object_paths,
            upload_files,
        ),
    )


def cleanup_attempt_workspace(workspace_dir: Path) -> None:
    _require_attempt_marker(workspace_dir)
    shutil.rmtree(workspace_dir)
    _cleanup_empty_attempt_parents(workspace_dir, depth=3)


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


def _cleanup_empty_attempt_parents(workspace_dir: Path, *, depth: int) -> None:
    current = workspace_dir.parent
    for _ in range(depth):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


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
