from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import lakefs_sdk
from lakefs import Client, Repository
from lakefs.exceptions import api_exception_handler

from perago.config import LakeFSConfig
from perago.errors import PublishFenceError
from perago.execution import StagedWorkspace
from perago.models import PublishBudget, WorkspaceInput, WorkspaceSpec
from perago.staging import staging_branch_name
from perago.workspace import (
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

        commit_ref = branch.commit("perago try")
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
        if current_head == workspace_input.ref:
            return self._merge_staged_workspace(staged, workspace_input, target_branch)

        if _first_parent_id(head_commit) == workspace_input.ref:
            return self._hard_reset_target_to_staged_commit(staged, workspace_input)

        raise PublishFenceError(
            f"{workspace_input.branch} cannot publish from input ref {workspace_input.ref}; "
            f"current head is {current_head}"
        )

    def cleanup_staging(self, staged: StagedWorkspace) -> None:
        self._repo(staged.repository).branch(staged.branch).delete()

    def _merge_staged_workspace(
        self,
        staged: StagedWorkspace,
        workspace_input: WorkspaceInput,
        target_branch: object,
    ) -> str:
        if self._publish_budget is None:
            return self._repo(staged.repository).branch(staged.branch).merge_into(
                target_branch,
                squash_merge=True,
            )

        with api_exception_handler():
            merge_result = self._client.sdk_client.refs_api.merge_into_branch(
                workspace_input.repository,
                staged.branch,
                workspace_input.branch,
                merge=lakefs_sdk.Merge(squash_merge=True),
                _request_timeout=self._publish_budget.lakefs_merge_timeout_seconds,
            )
        return merge_result.reference

    def _hard_reset_target_to_staged_commit(
        self,
        staged: StagedWorkspace,
        workspace_input: WorkspaceInput,
    ) -> str:
        kwargs = {
            "ref": staged.commit,
            "force": False,
        }
        if self._publish_budget is not None:
            kwargs["_request_timeout"] = self._publish_budget.lakefs_merge_timeout_seconds
        with api_exception_handler():
            self._client.sdk_client.experimental_api.hard_reset_branch(
                workspace_input.repository,
                workspace_input.branch,
                **kwargs,
            )
        return staged.commit

    def _repo(self, repository: str) -> Repository:
        return Repository(repository, client=self._client)


def _first_parent_id(commit: object) -> str | None:
    parents = _commit_parents(commit)
    if not parents:
        return None
    parent = parents[0]
    if isinstance(parent, str):
        return parent
    if isinstance(parent, Mapping):
        parent_id = parent.get("id")
    else:
        parent_id = getattr(parent, "id", None)
    if parent_id is None:
        return None
    return str(parent_id)


def _commit_parents(commit: object) -> Sequence[object]:
    if isinstance(commit, Mapping):
        parents = commit.get("parents", [])
    else:
        parents = getattr(commit, "parents", [])
    if isinstance(parents, Sequence) and not isinstance(parents, str):
        return parents
    return []
