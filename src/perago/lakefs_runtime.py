from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import lakefs_sdk
from lakefs import Client, Repository
from lakefs.exceptions import api_exception_handler

from perago.config import LakeFSConfig
from perago.errors import PublishFenceError
from perago.execution import StagedWorkspace
from perago.metadata import build_workspace_publication_plan, perago_metadata, staging_branch_name
from perago.models import PublishBudget, WorkspaceInput, WorkspaceSpec
from perago.workspace import (
    build_workspace_sync_plan,
    workspace_download_files,
    workspace_object_prefix,
)


MAX_TARGET_COMMIT_RANGE = 1024


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
        branch = repo.branch(staging_branch).create(workspace_input.ref, exist_ok=False)
        existing_paths = [
            getattr(item, "path")
            for item in branch.objects(prefix=workspace_object_prefix(workspace_spec))
            if getattr(item, "path_type", "object") == "object"
        ]
        plan = build_workspace_sync_plan(workspace_dir, workspace_spec, existing_paths)

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
        return StagedWorkspace(repository=workspace_input.repository, branch=staging_branch, commit=commit_ref.id)

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
        commits = self._target_commit_range(target_branch, workspace_input, current_head)
        plan = build_workspace_publication_plan(
            task=attempt,
            workspace=workspace_input,
            workspace_spec=workspace_spec,
            current_head=current_head,
            commits=commits,
            staging_commit=staged.commit,
        )
        if self._publish_budget is None:
            return repo.branch(staged.branch).merge_into(
                target_branch,
                squash_merge=True,
                metadata=plan.confirm_metadata,
            )

        with api_exception_handler():
            merge_result = self._client.sdk_client.refs_api.merge_into_branch(
                workspace_input.repository,
                staged.branch,
                workspace_input.branch,
                merge=lakefs_sdk.Merge(squash_merge=True, metadata=plan.confirm_metadata),
                _request_timeout=self._publish_budget.lakefs_merge_timeout_seconds,
            )
        return merge_result.reference

    def cleanup_staging(self, staged: StagedWorkspace) -> None:
        self._repo(staged.repository).branch(staged.branch).delete()

    def _target_commit_range(
        self,
        target_branch: object,
        workspace_input: WorkspaceInput,
        current_head: str,
    ) -> Sequence[object]:
        if current_head == workspace_input.ref:
            return []

        commits: list[object] = []
        reached_input_ref = False
        for commit in target_branch.log(first_parent=True):
            commit_id = getattr(commit, "id", None)
            if commit_id == workspace_input.ref:
                reached_input_ref = True
                break
            commits.append(commit)
            if len(commits) > MAX_TARGET_COMMIT_RANGE:
                raise PublishFenceError(
                    f"{workspace_input.branch} advanced beyond supported publish range "
                    f"({MAX_TARGET_COMMIT_RANGE} commits)"
                )

        if not reached_input_ref:
            raise PublishFenceError(
                f"{workspace_input.branch} no longer contains workspace input ref {workspace_input.ref}"
            )

        commits.reverse()
        return commits

    def _repo(self, repository: str) -> Repository:
        return Repository(repository, client=self._client)
