from __future__ import annotations

import argparse
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from lakefs import Client, Repository

from perago.config import load_runtime_config
from perago.errors import PublishFenceError
from perago.execution import StagedWorkspace
from perago.lakefs_runtime import LakeFSWorkspaceRuntime
from perago.models import WorkspaceInput, WorkspaceSpec


DEFAULT_REPO = "test-repo-3"
DEFAULT_BRANCH = "main"


@dataclass(frozen=True)
class ProtocolAttempt:
    workflow_instance_id: str
    reference_task_name: str
    seq: int
    iteration: int
    task_id: str
    retry_count: int
    execution_id: str


def main() -> int:
    args = parse_args()
    run_id = uuid.uuid4().hex[:10]
    config = load_runtime_config("scripts.perago_smoke_worker", probe_roots=False)
    if config.lakefs is None:
        raise RuntimeError("LakeFS config is required")

    client = Client(
        host=config.lakefs.endpoint_url,
        username=config.lakefs.access_key_id,
        password=config.lakefs.secret_access_key,
    )
    repo = Repository(args.repo, client=client)
    runtime = LakeFSWorkspaceRuntime(client=client)
    created_branches: list[str] = []

    try:
        run_merge_path(repo, runtime, args.branch, run_id, created_branches)
        run_replacement_path(repo, runtime, args.branch, run_id, created_branches)
        run_fail_closed_path(repo, runtime, args.branch, run_id, created_branches)
        run_missing_staging_merge_failure(repo, runtime, args.branch, run_id, created_branches)
        run_missing_staging_reset_failure(repo, runtime, args.branch, run_id, created_branches)
        log("protocol smoke completed")
        return 0
    finally:
        cleanup_branches(repo, created_branches)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real LakeFS publication protocol smoke tests.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="LakeFS repository to use.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Existing LakeFS branch used as the test base.")
    return parser.parse_args()


def run_merge_path(
    repo: Repository,
    runtime: LakeFSWorkspaceRuntime,
    base_branch: str,
    run_id: str,
    created_branches: list[str],
) -> None:
    scenario = "merge"
    target = create_target_branch(repo, base_branch, run_id, scenario, created_branches)
    spec = WorkspaceSpec(prefix=f"perago-protocol/{run_id}/{scenario}")
    input_commit = commit_object(
        repo,
        target,
        f"{spec.prefix}/input.txt",
        b"merge-input",
        f"{run_id} merge input",
    )
    staged = stage_output(repo, runtime, target, spec, input_commit, run_id, scenario, "merge-output")

    published_ref = runtime.publish_workspace(
        staged,
        workspace_input(repo, target, input_commit),
        spec,
        attempt(run_id, scenario),
    )
    runtime.cleanup_staging(staged)

    assert_branch_head(repo, target, published_ref)
    assert_object(repo, published_ref, f"{spec.prefix}/output.txt", b"merge-output")
    log(f"merge path ok target={target} published_ref={published_ref}")


def run_replacement_path(
    repo: Repository,
    runtime: LakeFSWorkspaceRuntime,
    base_branch: str,
    run_id: str,
    created_branches: list[str],
) -> None:
    scenario = "replace"
    target = create_target_branch(repo, base_branch, run_id, scenario, created_branches)
    spec = WorkspaceSpec(prefix=f"perago-protocol/{run_id}/{scenario}")
    input_commit = commit_object(
        repo,
        target,
        f"{spec.prefix}/input.txt",
        b"replace-input",
        f"{run_id} replace input",
    )
    abandoned_path = f"{spec.prefix}/abandoned.txt"
    commit_object(repo, target, abandoned_path, b"abandoned", f"{run_id} abandoned publish")
    staged = stage_output(repo, runtime, target, spec, input_commit, run_id, scenario, "replacement-output")

    published_ref = runtime.publish_workspace(
        staged,
        workspace_input(repo, target, input_commit),
        spec,
        attempt(run_id, scenario),
    )
    runtime.cleanup_staging(staged)

    if published_ref != staged.commit:
        raise AssertionError(f"replacement publish must return staging commit, got {published_ref!r}")
    assert_branch_head(repo, target, staged.commit)
    assert_object(repo, target, f"{spec.prefix}/output.txt", b"replacement-output")
    assert_object_missing(repo, target, abandoned_path)
    log(f"replacement path ok target={target} published_ref={published_ref}")


def run_fail_closed_path(
    repo: Repository,
    runtime: LakeFSWorkspaceRuntime,
    base_branch: str,
    run_id: str,
    created_branches: list[str],
) -> None:
    scenario = "failclosed"
    target = create_target_branch(repo, base_branch, run_id, scenario, created_branches)
    spec = WorkspaceSpec(prefix=f"perago-protocol/{run_id}/{scenario}")
    input_commit = commit_object(repo, target, f"{spec.prefix}/input.txt", b"fail-input", f"{run_id} fail input")
    commit_object(repo, target, f"{spec.prefix}/first.txt", b"first", f"{run_id} first divergent commit")
    divergent_head = commit_object(
        repo,
        target,
        f"{spec.prefix}/second.txt",
        b"second",
        f"{run_id} second divergent commit",
    )
    staged = stage_output(repo, runtime, target, spec, input_commit, run_id, scenario, "must-not-publish")

    try:
        runtime.publish_workspace(
            staged,
            workspace_input(repo, target, input_commit),
            spec,
            attempt(run_id, scenario),
        )
    except PublishFenceError:
        pass
    else:
        raise AssertionError("diverged target HEAD must fail closed")
    finally:
        runtime.cleanup_staging(staged)

    assert_branch_head(repo, target, divergent_head)
    assert_object_missing(repo, target, f"{spec.prefix}/output.txt")
    log(f"fail-closed path ok target={target} head={divergent_head}")


def run_missing_staging_merge_failure(
    repo: Repository,
    runtime: LakeFSWorkspaceRuntime,
    base_branch: str,
    run_id: str,
    created_branches: list[str],
) -> None:
    scenario = "missingmerge"
    target = create_target_branch(repo, base_branch, run_id, scenario, created_branches)
    spec = WorkspaceSpec(prefix=f"perago-protocol/{run_id}/{scenario}")
    input_commit = commit_object(
        repo,
        target,
        f"{spec.prefix}/input.txt",
        b"input",
        f"{run_id} missing merge input",
    )
    staged = StagedWorkspace(repository=repo.id, branch=f"perago-missing-{run_id}-{scenario}", commit="missing")

    assert_publish_operation_fails(
        runtime,
        staged,
        workspace_input(repo, target, input_commit),
        spec,
        attempt(run_id, scenario),
    )
    assert_branch_head(repo, target, input_commit)
    assert_object_missing(repo, target, f"{spec.prefix}/output.txt")
    log(f"missing staging merge failure ok target={target} head={input_commit}")


def run_missing_staging_reset_failure(
    repo: Repository,
    runtime: LakeFSWorkspaceRuntime,
    base_branch: str,
    run_id: str,
    created_branches: list[str],
) -> None:
    scenario = "missingreset"
    target = create_target_branch(repo, base_branch, run_id, scenario, created_branches)
    spec = WorkspaceSpec(prefix=f"perago-protocol/{run_id}/{scenario}")
    input_commit = commit_object(
        repo,
        target,
        f"{spec.prefix}/input.txt",
        b"input",
        f"{run_id} missing reset input",
    )
    abandoned_head = commit_object(
        repo,
        target,
        f"{spec.prefix}/abandoned.txt",
        b"abandoned",
        f"{run_id} missing reset abandoned publish",
    )
    staged = StagedWorkspace(repository=repo.id, branch=f"perago-missing-{run_id}-{scenario}", commit="missing")

    assert_publish_operation_fails(
        runtime,
        staged,
        workspace_input(repo, target, input_commit),
        spec,
        attempt(run_id, scenario),
    )
    assert_branch_head(repo, target, abandoned_head)
    assert_object_missing(repo, target, f"{spec.prefix}/output.txt")
    log(f"missing staging reset failure ok target={target} head={abandoned_head}")


def assert_publish_operation_fails(
    runtime: LakeFSWorkspaceRuntime,
    staged: StagedWorkspace,
    workspace: WorkspaceInput,
    spec: WorkspaceSpec,
    protocol_attempt: ProtocolAttempt,
) -> None:
    try:
        runtime.publish_workspace(staged, workspace, spec, protocol_attempt)
    except Exception:
        return
    raise AssertionError("publish operation unexpectedly succeeded")


def create_target_branch(
    repo: Repository,
    base_branch: str,
    run_id: str,
    scenario: str,
    created_branches: list[str],
) -> str:
    target = f"perago-protocol-{run_id}-{scenario}"
    repo.branch(target).create(base_branch, exist_ok=False)
    created_branches.append(target)
    return target


def commit_object(repo: Repository, branch: str, path: str, content: bytes, message: str) -> str:
    target = repo.branch(branch)
    target.object(path).upload(content, mode="wb")
    return target.commit(message).id


def stage_output(
    repo: Repository,
    runtime: LakeFSWorkspaceRuntime,
    target: str,
    spec: WorkspaceSpec,
    input_commit: str,
    run_id: str,
    scenario: str,
    output_text: str,
) -> StagedWorkspace:
    with tempfile.TemporaryDirectory(prefix=f"perago-protocol-{scenario}-") as temp_dir:
        workspace_dir = Path(temp_dir)
        runtime.download_workspace(workspace_input(repo, target, input_commit), spec, workspace_dir)
        if not (workspace_dir / "input.txt").is_file():
            raise AssertionError("downloaded workspace is missing input.txt")
        (workspace_dir / "output.txt").write_bytes(output_text.encode("utf-8"))
        return runtime.stage_workspace(
            workspace_dir,
            workspace_input(repo, target, input_commit),
            spec,
            attempt(run_id, scenario),
        )


def workspace_input(repo: Repository, target: str, input_commit: str) -> WorkspaceInput:
    return WorkspaceInput(repository=repo.id, branch=target, ref_type="commit", ref=input_commit)


def attempt(run_id: str, scenario: str) -> ProtocolAttempt:
    return ProtocolAttempt(
        workflow_instance_id=f"wf-{run_id}",
        reference_task_name=f"protocol-{scenario}",
        seq=1,
        iteration=0,
        task_id=f"task-{scenario}-{run_id}",
        retry_count=0,
        execution_id=f"exec-{scenario}-{run_id}",
    )


def assert_branch_head(repo: Repository, branch: str, expected_ref: str) -> None:
    current = repo.branch(branch).get_commit().id
    if current != expected_ref:
        raise AssertionError(f"{branch} head is {current!r}, expected {expected_ref!r}")


def assert_object(repo: Repository, ref: str, path: str, expected: bytes) -> None:
    with repo.ref(ref).object(path).reader(mode="rb") as reader:
        actual = reader.read()
    if actual != expected:
        raise AssertionError(f"{path} content is {actual!r}, expected {expected!r}")


def assert_object_missing(repo: Repository, ref: str, path: str) -> None:
    try:
        with repo.ref(ref).object(path).reader(mode="rb") as reader:
            reader.read()
    except Exception:
        return
    raise AssertionError(f"{path} should be absent at {ref}")


def cleanup_branches(repo: Repository, branch_ids: list[str]) -> None:
    for branch_id in reversed(branch_ids):
        try:
            repo.branch(branch_id).delete()
            log(f"cleanup branch={branch_id}")
        except Exception as exc:
            log(f"cleanup branch={branch_id} failed: {exc}")


def log(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
