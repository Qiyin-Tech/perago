from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ATTEMPT_WORKSPACE_MARKER = ".perago-attempt.json"


@dataclass(frozen=True, init=False)
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

    def __init__(self, local_path: Path, object_path: str) -> None:
        """
        Initialize a workspace upload record.

        Parameters
        ----------
        local_path : pathlib.Path
            Absolute or attempt-workspace-relative path to the local file
            selected for upload.
        object_path : str
            LakeFS object path where the file should be written.
        """
        object.__setattr__(self, "local_path", local_path)
        object.__setattr__(self, "object_path", object_path)


@dataclass(frozen=True, init=False)
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

    def __init__(self, object_path: str, local_path: Path) -> None:
        """
        Initialize a workspace download record.

        Parameters
        ----------
        object_path : str
            LakeFS object path selected from the input workspace ref.
        local_path : pathlib.Path
            Destination path under the attempt-local workspace root.
        """
        object.__setattr__(self, "object_path", object_path)
        object.__setattr__(self, "local_path", local_path)


@dataclass(frozen=True, init=False)
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

    def __init__(self, upload_files: list[WorkspaceUploadFile], delete_object_paths: list[str]) -> None:
        """
        Initialize a workspace sync plan.

        Parameters
        ----------
        upload_files : list of WorkspaceUploadFile
            Local files that should be uploaded to the staging branch.
        delete_object_paths : list of str
            Existing object paths under the workspace prefix that should be
            deleted.
        """
        object.__setattr__(self, "upload_files", upload_files)
        object.__setattr__(self, "delete_object_paths", delete_object_paths)

    @property
    def changed_object_count(self) -> int:
        """Number of remote object changes in this sync plan."""
        return len(self.upload_files) + len(self.delete_object_paths)

    @property
    def upload_bytes(self) -> int:
        """Total byte size of files selected for upload."""
        return sum(file.local_path.stat().st_size for file in self.upload_files)


@dataclass(frozen=True)
class WorkspaceOwner:
    worker_id: str
    pid: int
    token: str
