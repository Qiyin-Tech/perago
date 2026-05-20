from __future__ import annotations

import shutil
from pathlib import Path

from lakefs import Client, Repository

from perago.config import LakeFSConfig
from perago.execution import StagedWorkspace
from perago.metadata import build_workspace_publication_plan, perago_metadata, staging_branch_name
from perago.models import PublishBudget, WorkspaceInput, WorkspaceSpec
from perago.workspace import (
    build_budgeted_workspace_sync_plan,
    build_workspace_sync_plan,
    workspace_download_files,
    workspace_object_prefix,
)


class LakeFSWorkspaceRuntime:
    def __init__(self, *, client: Client, publish_budget: PublishBudget | None = None) -> None:
        self._client = client
        self._publish_budget = publish_budget

    @classmethod
    def from_config(
        cls,
        config: LakeFSConfig,
        *,
        publish_budget: PublishBudget | None = None,
    ) -> LakeFSWorkspaceRuntime:
        return cls(
            client=Client(
                host=config.endpoint_url,
                username=config.access_key_id,
                password=config.secret_access_key,
            ),
            publish_budget=publish_budget,
        )

    def download_workspace(
        self,
        workspace_input: WorkspaceInput,
        workspace_spec: WorkspaceSpec,
        workspace_dir: Path,
    ) -> None:
        ref = self._repo(workspace_input.repository).ref(workspace_input.ref)
        object_paths = [
            getattr(item, "path")
            for item in ref.objects(prefix=workspace_object_prefix(workspace_spec))
            if getattr(item, "path_type", "object") == "object"
        ]
        for file in workspace_download_files(workspace_dir, workspace_spec, object_paths):
            file.local_path.parent.mkdir(parents=True, exist_ok=True)
            with ref.object(file.object_path).reader(mode="rb") as reader:
                with file.local_path.open("wb") as output:
                    shutil.copyfileobj(reader, output)

    def stage_workspace(
        self,
        workspace_dir: Path,
        workspace_input: WorkspaceInput,
        workspace_spec: WorkspaceSpec,
        attempt: object,
    ) -> StagedWorkspace:
        repo = self._repo(workspace_input.repository)
        staging_branch = staging_branch_name(attempt)
        branch = repo.branch(staging_branch).create(workspace_input.ref, exist_ok=True)
        existing_paths = [
            getattr(item, "path")
            for item in branch.objects(prefix=workspace_object_prefix(workspace_spec))
            if getattr(item, "path_type", "object") == "object"
        ]
        if self._publish_budget is None:
            plan = build_workspace_sync_plan(workspace_dir, workspace_spec, existing_paths)
        else:
            plan = build_budgeted_workspace_sync_plan(
                workspace_dir,
                workspace_spec,
                existing_paths,
                self._publish_budget,
            )

        for object_path in plan.delete_object_paths:
            branch.object(object_path).delete()
        for file in plan.upload_files:
            branch.object(file.object_path).upload(file.local_path.read_bytes(), mode="wb")

        key = getattr(attempt, "logical_task_key", None)
        if key is None:
            from perago.metadata import logical_task_key

            key = logical_task_key(attempt)
        commit_ref = branch.commit(
            "perago try",
            metadata=perago_metadata(
                task=attempt,
                workspace=workspace_input,
                workspace_spec=workspace_spec,
                logical_task_key=str(key),
                phase="try",
            ),
        )
        return StagedWorkspace(branch=staging_branch, commit=commit_ref.id)

    def publish_workspace(
        self,
        staged: StagedWorkspace,
        workspace_input: WorkspaceInput,
        workspace_spec: WorkspaceSpec,
        attempt: object,
    ) -> str:
        repo = self._repo(workspace_input.repository)
        target_branch = repo.branch(workspace_input.branch)
        head_commit = target_branch.get_commit()
        current_head = head_commit.id
        plan = build_workspace_publication_plan(
            task=attempt,
            workspace=workspace_input,
            workspace_spec=workspace_spec,
            current_head=current_head,
            commits=[head_commit],
            staging_commit=staged.commit,
        )
        return repo.branch(staged.branch).merge_into(
            target_branch,
            squash_merge=True,
            metadata=plan.confirm_metadata,
        )

    def cleanup_staging(self, staged: StagedWorkspace) -> None:
        # Repository is intentionally not encoded in StagedWorkspace; the callback is bound per attempt below.
        raise NotImplementedError("cleanup_staging must be bound with workspace input")

    def _repo(self, repository: str) -> Repository:
        return Repository(repository, client=self._client)


class BoundLakeFSWorkspaceRuntime:
    def __init__(self, runtime: LakeFSWorkspaceRuntime) -> None:
        self._runtime = runtime
        self._last_workspace_input: WorkspaceInput | None = None

    def download_workspace(
        self,
        workspace_input: WorkspaceInput,
        workspace_spec: WorkspaceSpec,
        workspace_dir: Path,
    ) -> None:
        self._last_workspace_input = workspace_input
        self._runtime.download_workspace(workspace_input, workspace_spec, workspace_dir)

    def stage_workspace(
        self,
        workspace_dir: Path,
        workspace_input: WorkspaceInput,
        workspace_spec: WorkspaceSpec,
        attempt: object,
    ) -> StagedWorkspace:
        self._last_workspace_input = workspace_input
        return self._runtime.stage_workspace(workspace_dir, workspace_input, workspace_spec, attempt)

    def publish_workspace(
        self,
        staged: StagedWorkspace,
        workspace_input: WorkspaceInput,
        workspace_spec: WorkspaceSpec,
        attempt: object,
    ) -> str:
        self._last_workspace_input = workspace_input
        return self._runtime.publish_workspace(staged, workspace_input, workspace_spec, attempt)

    def cleanup_staging(self, staged: StagedWorkspace) -> None:
        if self._last_workspace_input is None:
            raise RuntimeError("workspace input is not available for staging cleanup")
        self._runtime._repo(self._last_workspace_input.repository).branch(staged.branch).delete()
