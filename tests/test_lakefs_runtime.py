from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from perago.execution import StagedWorkspace
from perago.errors import PublishFenceError
from perago.lakefs_runtime import LakeFSWorkspaceRuntime, _commit_parents, _first_parent_id
from perago.config import LakeFSConfig
from perago.models import PublishBudget, WorkspaceInput, WorkspaceSpec
from perago.staging import staging_branch_name


@dataclass
class Attempt:
    workflow_instance_id: str = "wf-7f3d"
    task_def_name: str = "features.build"
    reference_task_name: str = "build_features"
    task_id: str = "task-9b4c"
    retry_count: int = 2
    seq: int = 3
    iteration: int = 1
    execution_id: str = "exec-1"


@dataclass
class FakeItem:
    path: str


@dataclass
class FakeCommit:
    id: str
    parents: list[object] = field(default_factory=list)


class FakeReader:
    def __init__(self, data: bytes) -> None:
        self._data = BytesIO(data)

    def __enter__(self):
        return self._data

    def __exit__(self, exc_type, exc, tb) -> None:
        self._data.close()


class FakeObject:
    def __init__(self, path: str, store: dict[str, bytes], deleted: list[str]) -> None:
        self.path = path
        self._store = store
        self._deleted = deleted

    def reader(self, mode: str):
        assert mode == "rb"
        return FakeReader(self._store[self.path])

    def upload(self, data: bytes, mode: str):
        assert mode == "wb"
        self._store[self.path] = data

    def delete(self) -> None:
        self._deleted.append(self.path)
        self._store.pop(self.path, None)


class FakeRef:
    def __init__(self, store: dict[str, bytes], deleted: list[str]) -> None:
        self._store = store
        self._deleted = deleted

    def objects(self, prefix: str):
        return [FakeItem(path) for path in sorted(self._store) if path.startswith(prefix)]

    def object(self, path: str) -> FakeObject:
        return FakeObject(path, self._store, self._deleted)


class FakeBranch(FakeRef):
    def __init__(
        self,
        branch_id: str,
        store: dict[str, bytes],
        deleted: list[str],
        commit_log: list[FakeCommit] | None = None,
    ) -> None:
        super().__init__(store, deleted)
        self.id = branch_id
        self.created_from = None
        self.create_exist_ok: bool | None = None
        self.deleted = False
        self.commits: list[FakeCommit] = []
        self.commit_kwargs: list[dict] = []
        self.merges: list[dict] = []
        self.commit_log = commit_log or [FakeCommit(id="input-commit")]
        self.log_calls: list[dict] = []

    def create(self, source_reference: str, exist_ok: bool = False):
        self.created_from = source_reference
        self.create_exist_ok = exist_ok
        return self

    def commit(self, message: str, **kwargs):
        assert message == "perago try"
        assert "metadata" not in kwargs
        self.commit_kwargs.append(kwargs)
        commit = FakeCommit(id="staging-commit")
        self.commits.append(commit)
        return commit

    def get_commit(self):
        return self.commit_log[0]

    def log(self, **kwargs):
        self.log_calls.append(kwargs)
        stop_at = kwargs.get("stop_at")
        for commit in self.commit_log:
            if commit.id == stop_at:
                break
            yield commit

    def merge_into(self, destination_branch, **kwargs):
        assert "metadata" not in kwargs
        self.merges.append({"destination": destination_branch.id, **kwargs})
        return "published-commit"

    def delete(self) -> None:
        self.deleted = True


class FakeRepo:
    def __init__(self) -> None:
        self.source_store = {
            "audio/render/raw/input.txt": b"input",
            "other/ignored.txt": b"ignored",
        }
        self.staging_store = {
            "audio/render/raw/input.txt": b"input",
            "audio/render/old.txt": b"old",
        }
        self.deleted: list[str] = []
        self.branches: dict[str, FakeBranch] = {}
        self.main_commit_log = [FakeCommit(id="input-commit")]

    def ref(self, ref_id: str):
        assert ref_id == "input-commit"
        return FakeRef(self.source_store, self.deleted)

    def branch(self, branch_id: str):
        if branch_id not in self.branches:
            store = self.staging_store if branch_id.startswith("perago-staging-") else {}
            commit_log = self.main_commit_log if branch_id == "main" else None
            self.branches[branch_id] = FakeBranch(branch_id, store, self.deleted, commit_log=commit_log)
        return self.branches[branch_id]


class FakeRefsApi:
    def __init__(self) -> None:
        self.merge_calls: list[dict] = []

    def merge_into_branch(self, repository, source_ref, destination_branch, *, merge, _request_timeout):
        self.merge_calls.append(
            {
                "repository": repository,
                "source_ref": source_ref,
                "destination_branch": destination_branch,
                "merge": merge,
                "_request_timeout": _request_timeout,
            }
        )
        return SimpleNamespace(reference="published-commit")


class FakeSdkClient:
    def __init__(self) -> None:
        self.refs_api = FakeRefsApi()
        self.experimental_api = FakeExperimentalApi()


class FakeExperimentalApi:
    def __init__(self) -> None:
        self.hard_reset_calls: list[dict] = []

    def hard_reset_branch(self, repository, branch, *, ref, force, _request_timeout=None):
        self.hard_reset_calls.append(
            {
                "repository": repository,
                "branch": branch,
                "ref": ref,
                "force": force,
                "_request_timeout": _request_timeout,
            }
        )
        return SimpleNamespace(reference=ref)


class FakeClient:
    def __init__(self) -> None:
        self.sdk_client = FakeSdkClient()


class FakeRuntime(LakeFSWorkspaceRuntime):
    def __init__(self, repo: FakeRepo, publish_budget: PublishBudget | None = None) -> None:
        self.repo = repo
        super().__init__(client=FakeClient(), publish_budget=publish_budget)

    def _repo(self, repository: str):
        assert repository == "song-000123"
        return self.repo


class MultiRepoRuntime(LakeFSWorkspaceRuntime):
    def __init__(self, repos: dict[str, FakeRepo]) -> None:
        super().__init__(client=FakeClient())
        self.repos = repos

    def _repo(self, repository: str):
        return self.repos[repository]


def _workspace_input() -> WorkspaceInput:
    return WorkspaceInput(repository="song-000123", branch="main", ref_type="commit", ref="input-commit")


def test_lakefs_runtime_download_stage_publish_and_cleanup(tmp_path) -> None:
    repo = FakeRepo()
    runtime = FakeRuntime(repo)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    attempt = Attempt()

    runtime.download_workspace(workspace, spec, tmp_path)

    assert (tmp_path / "raw" / "input.txt").read_bytes() == b"input"
    assert not (tmp_path / "other").exists()

    (tmp_path / "raw" / "input.txt").write_bytes(b"updated")
    (tmp_path / "features").mkdir()
    (tmp_path / "features" / "out.txt").write_bytes(b"feature")
    staged = runtime.stage_workspace(tmp_path, workspace, spec, attempt)

    assert staged.repository == "song-000123"
    assert staged.branch == staging_branch_name(attempt)
    staging_branch = repo.branches[staged.branch]
    assert staging_branch.created_from == "input-commit"
    assert staging_branch.create_exist_ok is False
    assert repo.staging_store["audio/render/raw/input.txt"] == b"updated"
    assert repo.staging_store["audio/render/features/out.txt"] == b"feature"
    assert "audio/render/old.txt" in repo.deleted
    assert staging_branch.commits[0].id == "staging-commit"
    assert staging_branch.commit_kwargs == [{}]

    published = runtime.publish_workspace(staged, workspace, spec, attempt)

    assert published == "published-commit"
    assert staging_branch.merges[0]["destination"] == "main"
    assert staging_branch.merges[0]["squash_merge"] is True
    assert "metadata" not in staging_branch.merges[0]

    runtime.cleanup_staging(StagedWorkspace(repository=staged.repository, branch=staged.branch, commit=staged.commit))

    assert staging_branch.deleted is True


def test_lakefs_runtime_from_config_builds_client(monkeypatch) -> None:
    created = {}

    class FakeLakeFSClient:
        def __init__(self, *, host, username, password) -> None:
            created.update({"host": host, "username": username, "password": password})

    monkeypatch.setattr("perago.lakefs_runtime.Client", FakeLakeFSClient)

    runtime = LakeFSWorkspaceRuntime.from_config(
        LakeFSConfig(
            endpoint_url="http://lakefs.local",
            access_key_id="lakefs-key",
            secret_access_key="lakefs-secret",
        )
    )

    assert isinstance(runtime, LakeFSWorkspaceRuntime)
    assert created == {"host": "http://lakefs.local", "username": "lakefs-key", "password": "lakefs-secret"}


def test_lakefs_cleanup_uses_staged_repository_without_prior_runtime_state() -> None:
    shared_branch = "perago-staging-shared"
    repo_a = FakeRepo()
    repo_b = FakeRepo()
    branch_a = repo_a.branch(shared_branch)
    branch_b = repo_b.branch(shared_branch)
    runtime = MultiRepoRuntime({"song-a": repo_a, "song-b": repo_b})

    staged_a = StagedWorkspace(repository="song-a", branch=shared_branch, commit="staging-commit-a")
    staged_b = StagedWorkspace(repository="song-b", branch=shared_branch, commit="staging-commit-b")

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(runtime.cleanup_staging, [staged_a, staged_b]))

    assert branch_a.deleted is True
    assert branch_b.deleted is True


def test_lakefs_publish_uses_merge_request_timeout_from_publish_budget(tmp_path) -> None:
    repo = FakeRepo()
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=45,
        conductor_completion_timeout_seconds=15,
        worker_shutdown_grace_seconds=30,
        heartbeat_interval_seconds=10,
    )
    runtime = FakeRuntime(repo, publish_budget=budget)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    attempt = Attempt()

    staged = StagedWorkspace(repository="song-000123", branch=staging_branch_name(attempt), commit="staging-commit")
    published = runtime.publish_workspace(staged, workspace, spec, attempt)

    assert published == "published-commit"
    merge_call = runtime._client.sdk_client.refs_api.merge_calls[0]
    assert merge_call["repository"] == "song-000123"
    assert merge_call["source_ref"] == staged.branch
    assert merge_call["destination_branch"] == "main"
    assert merge_call["_request_timeout"] == 45
    assert merge_call["merge"].squash_merge is True
    assert getattr(merge_call["merge"], "metadata", None) is None


def test_lakefs_publish_replaces_abandoned_publication_with_hard_reset() -> None:
    repo = FakeRepo()
    attempt = Attempt()
    repo.main_commit_log = [
        FakeCommit(id="abandoned-commit", parents=["input-commit"]),
    ]
    runtime = FakeRuntime(repo)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    staged = StagedWorkspace(repository="song-000123", branch=staging_branch_name(attempt), commit="staging-commit")

    published = runtime.publish_workspace(staged, workspace, spec, attempt)

    assert published == "staging-commit"
    assert staged.branch not in repo.branches
    assert runtime._client.sdk_client.experimental_api.hard_reset_calls == [
        {
            "repository": "song-000123",
            "branch": "main",
            "ref": "staging-commit",
            "force": False,
            "_request_timeout": None,
        }
    ]


def test_lakefs_complete_noop_returns_input_ref_when_head_matches() -> None:
    repo = FakeRepo()
    runtime = FakeRuntime(repo)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    attempt = Attempt()

    output_ref = runtime.complete_noop_workspace(workspace, spec, attempt)

    assert output_ref == "input-commit"
    assert runtime._client.sdk_client.experimental_api.hard_reset_calls == []


def test_lakefs_complete_noop_relocates_abandoned_publication_to_input_ref() -> None:
    repo = FakeRepo()
    repo.main_commit_log = [
        FakeCommit(id="abandoned-commit", parents=["input-commit"]),
    ]
    runtime = FakeRuntime(repo)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    attempt = Attempt()

    output_ref = runtime.complete_noop_workspace(workspace, spec, attempt)

    assert output_ref == "input-commit"
    assert runtime._client.sdk_client.experimental_api.hard_reset_calls == [
        {
            "repository": "song-000123",
            "branch": "main",
            "ref": "input-commit",
            "force": False,
            "_request_timeout": None,
        }
    ]


def test_lakefs_complete_noop_rejects_head_without_input_ref_parent() -> None:
    repo = FakeRepo()
    repo.main_commit_log = [
        FakeCommit(id="head-2", parents=["head-1"]),
    ]
    runtime = FakeRuntime(repo)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    attempt = Attempt()

    with pytest.raises(PublishFenceError, match="cannot complete no-op from input ref"):
        runtime.complete_noop_workspace(workspace, spec, attempt)


def test_lakefs_publish_hard_reset_uses_publish_budget_timeout() -> None:
    repo = FakeRepo()
    attempt = Attempt()
    repo.main_commit_log = [
        FakeCommit(id="abandoned-commit", parents=["input-commit"]),
    ]
    budget = PublishBudget(
        observed_merge_p99_seconds=20,
        safety_margin_seconds=10,
        lakefs_merge_timeout_seconds=45,
        conductor_completion_timeout_seconds=15,
        worker_shutdown_grace_seconds=30,
        heartbeat_interval_seconds=10,
    )
    runtime = FakeRuntime(repo, publish_budget=budget)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    staged = StagedWorkspace(repository="song-000123", branch=staging_branch_name(attempt), commit="staging-commit")

    published = runtime.publish_workspace(staged, workspace, spec, attempt)

    assert published == "staging-commit"
    hard_reset_call = runtime._client.sdk_client.experimental_api.hard_reset_calls[0]
    assert hard_reset_call["_request_timeout"] == 45


def test_lakefs_publish_rejects_head_without_input_ref_parent() -> None:
    repo = FakeRepo()
    attempt = Attempt()
    repo.main_commit_log = [
        FakeCommit(id="head-2", parents=["head-1"]),
    ]
    runtime = FakeRuntime(repo)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    staged = StagedWorkspace(repository="song-000123", branch=staging_branch_name(attempt), commit="staging-commit")

    with pytest.raises(PublishFenceError, match="cannot publish from input ref"):
        runtime.publish_workspace(staged, workspace, spec, attempt)


@pytest.mark.parametrize("parents", [None, []])
def test_lakefs_publish_rejects_head_without_parent_metadata(parents) -> None:
    repo = FakeRepo()
    attempt = Attempt()
    if parents is None:
        head = SimpleNamespace(id="head-without-parents")
    else:
        head = FakeCommit(id="head-without-parents", parents=parents)
    repo.main_commit_log = [head]
    runtime = FakeRuntime(repo)
    workspace = _workspace_input()
    spec = WorkspaceSpec(prefix="/audio/render")
    staged = StagedWorkspace(repository="song-000123", branch=staging_branch_name(attempt), commit="staging-commit")

    with pytest.raises(PublishFenceError, match="cannot publish from input ref"):
        runtime.publish_workspace(staged, workspace, spec, attempt)


@pytest.mark.parametrize(
    ("commit", "expected"),
    [
        ({"parents": [{"id": "input-commit"}]}, "input-commit"),
        ({"parents": [SimpleNamespace(id="input-commit")]}, "input-commit"),
        ({"parents": [{}]}, None),
    ],
)
def test_first_parent_id_handles_mapping_and_object_parent_shapes(commit, expected) -> None:
    assert _first_parent_id(commit) == expected


def test_commit_parents_ignores_string_parent_payload() -> None:
    assert _commit_parents({"parents": "input-commit"}) == []
