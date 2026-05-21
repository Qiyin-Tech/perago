from perago.workspace import (
    ATTEMPT_WORKSPACE_MARKER,
    WorkspaceDownloadFile,
    WorkspaceOwner,
    WorkspaceSyncPlan,
    WorkspaceUploadFile,
    active_workspace_owner_tokens,
    attempt_workspace_dir,
    build_workspace_sync_plan,
    cleanup_attempt_workspace,
    cleanup_attempt_workspace_safely,
    garbage_collect_attempt_workspaces,
    garbage_collect_workspace_owner,
    new_workspace_owner,
    prepare_attempt_workspace,
    register_active_workspace_owner,
    sweep_abandoned_attempt_workspaces,
    unregister_active_workspace_owner,
    workspace_delete_object_paths,
    workspace_download_files,
    workspace_local_path,
    workspace_object_path,
    workspace_object_prefix,
    workspace_upload_files,
)


def test_workspace_public_import_surface_remains_available() -> None:
    assert ATTEMPT_WORKSPACE_MARKER == ".perago-attempt.json"
    assert WorkspaceDownloadFile
    assert WorkspaceOwner
    assert WorkspaceSyncPlan
    assert WorkspaceUploadFile
    assert active_workspace_owner_tokens
    assert attempt_workspace_dir
    assert build_workspace_sync_plan
    assert cleanup_attempt_workspace
    assert cleanup_attempt_workspace_safely
    assert garbage_collect_attempt_workspaces
    assert garbage_collect_workspace_owner
    assert new_workspace_owner
    assert prepare_attempt_workspace
    assert register_active_workspace_owner
    assert sweep_abandoned_attempt_workspaces
    assert unregister_active_workspace_owner
    assert workspace_delete_object_paths
    assert workspace_download_files
    assert workspace_local_path
    assert workspace_object_path
    assert workspace_object_prefix
    assert workspace_upload_files
