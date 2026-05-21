from __future__ import annotations

from pathlib import Path

from perago.errors import TaskInputError
from perago.models import WorkspaceSpec
from perago.workspace.models import (
    ATTEMPT_WORKSPACE_MARKER,
    WorkspaceDownloadFile,
    WorkspaceSyncPlan,
    WorkspaceUploadFile,
)
from perago.workspace.paths import workspace_local_path, workspace_object_path


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
