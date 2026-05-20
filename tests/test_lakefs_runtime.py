from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from perago.execution import StagedWorkspace
from perago.lakefs_runtime import BoundLakeFSWorkspaceRuntime, LakeFSWorkspaceRuntime
from perago.metadata import staging_branch_name
from perago.models import WorkspaceInput, WorkspaceSpec


@dataclass
class Attempt:
    workflow_instance_id: str = "wf-7f3d"
    task_def_name: str = "features.build"
    reference_task_name: str = "build_features"
    task_id: str = "task-9b4c"
    retry_count: int = 2
    seq: int = 3
    iteration: int = 1


@dataclass
class FakeItem:
    path: str


@dataclass
class FakeCommit:
    id: str
    metadata: dict[str, str] = field(default_factory=dict)


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
    def __init__(self, branch_id: str, store: dict[str, bytes], deleted: list[str]) -> None:
        super().__init__(store, deleted)
        self.id = branch_id
        self.created_from = None
        self.deleted = False
        self.commits: list[FakeCommit] = []
        self.merges: list[dict] = []

    def create(self, source_reference: str, exist_ok: bool = False):
        assert exist_ok is True
        self.created_from = source_reference
        return self

    def commit(self, message: str, metadata: dict[str, str]):
        assert message == "perago try"
        commit = FakeCommit(id="staging-commit", metadata=metadata)
        self.commits.append(commit)
        return commit

    def get_commit(self):
        return FakeCommit(id="input-commit")

    def merge_into(self, destination_branch, **kwargs):
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

    def ref(self, ref_id: str):
        assert ref_id == "input-commit"
        return FakeRef(self.source_store, self.deleted)

    def branch(self, branch_id: str):
        if branch_id not in self.branches:
            store = self.staging_store if branch_id.startswith("perago-staging-") else {}
            self.branches[branch_id] = FakeBranch(branch_id, store, self.deleted)
        return self.branches[branch_id]


class FakeRuntime(LakeFSWorkspaceRuntime):
    def __init__(self, repo: FakeRepo) -> None:
        self.repo = repo
        self._publish_budget = None

    def _repo(self, repository: str):
        assert repository == "song-000123"
        return self.repo


def _workspace_input() -> WorkspaceInput:
    return WorkspaceInput(repository="song-000123", branch="main", ref_type="commit", ref="input-commit")


def test_lakefs_runtime_download_stage_publish_and_cleanup(tmp_path) -> None:
    repo = FakeRepo()
    runtime = BoundLakeFSWorkspaceRuntime(FakeRuntime(repo))
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

    assert staged.branch == staging_branch_name(attempt)
    staging_branch = repo.branches[staged.branch]
    assert staging_branch.created_from == "input-commit"
    assert repo.staging_store["audio/render/raw/input.txt"] == b"updated"
    assert repo.staging_store["audio/render/features/out.txt"] == b"feature"
    assert "audio/render/old.txt" in repo.deleted
    assert staging_branch.commits[0].metadata["perago.phase"] == "try"

    published = runtime.publish_workspace(staged, workspace, spec, attempt)

    assert published == "published-commit"
    assert staging_branch.merges[0]["destination"] == "main"
    assert staging_branch.merges[0]["squash_merge"] is True
    assert staging_branch.merges[0]["metadata"]["perago.phase"] == "confirm"
    assert staging_branch.merges[0]["metadata"]["perago.staging_commit"] == "staging-commit"

    runtime.cleanup_staging(StagedWorkspace(branch=staged.branch, commit=staged.commit))

    assert staging_branch.deleted is True
