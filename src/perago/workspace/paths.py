from __future__ import annotations

from os import PathLike
from pathlib import Path

from perago.guards import _canonical_workspace_path
from perago.models import WorkspaceSpec
from perago.workspace.models import ATTEMPT_WORKSPACE_MARKER


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
        # Re-validate the visible workspace-relative suffix after stripping the
        # LakeFS prefix. Without this second pass, an object like
        # "audio/render/C:/payload.py" becomes a drive-qualified local path.
        remote_path = _canonical_workspace_path(remote_path)

    local_path = Path(remote_path)
    if local_path.name == ATTEMPT_WORKSPACE_MARKER:
        return None
    return local_path
