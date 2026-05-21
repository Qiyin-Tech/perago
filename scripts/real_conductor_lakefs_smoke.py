from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conductor.client.configuration.configuration import Configuration
from conductor.client.http.models.schema_def import SchemaDef
from conductor.client.http.models.task_def import TaskDef
from conductor.client.http.models.workflow_def import WorkflowDef
from conductor.client.http.models.workflow_task import WorkflowTask
from conductor.client.orkes.orkes_metadata_client import OrkesMetadataClient
from conductor.client.orkes.orkes_workflow_client import OrkesWorkflowClient
from lakefs import Client, Repository

from perago.config import load_runtime_config
from scripts.perago_smoke_worker import TASK_NAME, WORKSPACE_PREFIX


DEFAULT_REPO = "test-repo-3"
DEFAULT_BRANCH = "main"
IMPORT_TARGET = "scripts.perago_smoke_worker"


def main() -> int:
    args = parse_args()
    run_id = uuid.uuid4().hex[:10]
    task_name = TASK_NAME
    workflow_name = "perago_smoke_workspace"

    config = load_runtime_config("app.workers.metadata_validate", probe_roots=False)
    if config.conductor is None:
        raise RuntimeError("CONDUCTOR_SERVER_URL is required")
    if config.lakefs is None:
        raise RuntimeError("LakeFS config is required")

    conductor_config = Configuration(server_api_url=config.conductor.server_url)
    metadata = OrkesMetadataClient(conductor_config)
    workflows = OrkesWorkflowClient(conductor_config)
    lakefs_client = Client(
        host=config.lakefs.endpoint_url,
        username=config.lakefs.access_key_id,
        password=config.lakefs.secret_access_key,
    )
    repo = Repository(args.repo, client=lakefs_client)
    target_branch = repo.branch(args.branch)

    proc: subprocess.Popen[str] | None = None
    workflow_id: str | None = None
    workflow_completed = False
    published_ref: str | None = None

    try:
        taskdef = extract_task_def(IMPORT_TARGET)
        unregister_definitions(metadata, task_name, workflow_name)
        metadata.register_task_def(taskdef)
        metadata.register_workflow_def(workflow_def(workflow_name, taskdef), overwrite=True)
        log(f"registered taskdef={task_name} workflow={workflow_name}")

        input_object = f"{WORKSPACE_PREFIX}/{run_id}/input.txt"
        input_text = f"hello-from-lakefs-{run_id}"
        target_branch.object(input_object).upload(input_text.encode("utf-8"), mode="wb")
        input_commit = target_branch.commit(f"perago smoke input {run_id}").id
        log(f"lakefs input_commit={input_commit}")

        env = command_env()
        env["PERAGO_WORKER_ID_PREFIX"] = f"Smoke{run_id}"
        proc = subprocess.Popen(
            [perago_command(), "start", IMPORT_TARGET, "-j", "1"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log(f"started worker pid={proc.pid}")

        workflow_input = {
            "workspace": {
                "repository": args.repo,
                "branch": args.branch,
                "ref_type": "commit",
                "ref": input_commit,
            },
            "params": {"run_id": run_id, "greeting": "hello world"},
        }
        workflow_id = workflows.start_workflow_by_name(workflow_name, workflow_input, version=1)
        log(f"started workflow_id={workflow_id}")

        workflow = wait_workflow(workflows, workflow_id, proc, timeout_seconds=args.timeout_seconds)
        log(f"workflow status={workflow.status}")
        if workflow.status != "COMPLETED":
            dump_worker_output(proc)
            raise RuntimeError(f"workflow did not complete: {workflow.status}")
        workflow_completed = True

        output = getattr(workflow, "output", None) or {}
        result = output.get("result") or {}
        workspace_output = output.get("workspace") or {}
        expected_message = f"hello world, {input_text}"
        if result.get("message") != expected_message:
            raise AssertionError(f"unexpected Conductor result: {result!r}")
        published_ref = workspace_output.get("ref")
        if not published_ref:
            raise AssertionError(f"missing workspace output ref: {workspace_output!r}")

        output_path = f"{WORKSPACE_PREFIX}/{run_id}/output.txt"
        output_bytes = repo.ref(published_ref).object(output_path).reader(mode="rb").read()
        lakefs_output = output_bytes.decode("utf-8")
        if lakefs_output != expected_message:
            raise AssertionError(f"unexpected LakeFS output: {lakefs_output!r}")
        log(f"conductor output={result}")
        log(f"lakefs published_ref={published_ref} output_path={output_path}")
        log(f"lakefs staging_branch_remaining={find_staging_branch(repo, run_id)}")
        return 0
    finally:
        if proc is not None:
            stop_process(proc)
        if workflow_id is not None and not workflow_completed:
            try:
                workflows.terminate_workflow(workflow_id, "perago smoke cleanup")
            except Exception:
                pass
        unregister_definitions(metadata, task_name, workflow_name)
        log(f"cleanup taskdef={task_name} workflow={workflow_name} published_ref={published_ref}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real Conductor + LakeFS Perago smoke test.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="LakeFS repository to use.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="LakeFS branch to use.")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="Workflow completion timeout.")
    return parser.parse_args()


def extract_task_def(import_target: str) -> TaskDef:
    with tempfile.TemporaryDirectory(prefix="perago-taskdef-") as temp_dir:
        taskdef_path = Path(temp_dir) / "taskdef.json"
        completed = subprocess.run(
            [perago_command(), "extract", import_target, "--output", str(taskdef_path)],
            cwd=REPO_ROOT,
            env=command_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        taskdef_path = completed.stdout.strip().splitlines()[-1]
        log(f"extracted taskdef_path={taskdef_path}")
        data = json.loads(Path(taskdef_path).read_text(encoding="utf-8"))
    return task_def_from_data(data)


def perago_command() -> str:
    command = shutil.which("perago")
    if command is None:
        raise RuntimeError("perago CLI is not on PATH; run this script through the project environment")
    return command


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), env.get("PYTHONPATH", "")])
    return env


def task_def_from_data(data: dict[str, Any]) -> TaskDef:
    return TaskDef(
        name=data["name"],
        description=data.get("description"),
        owner_email=data["ownerEmail"],
        retry_count=data.get("retryCount"),
        retry_logic=data.get("retryLogic"),
        retry_delay_seconds=data.get("retryDelaySeconds"),
        timeout_seconds=data.get("timeoutSeconds"),
        timeout_policy=data.get("timeoutPolicy"),
        response_timeout_seconds=data.get("responseTimeoutSeconds"),
        poll_timeout_seconds=data.get("pollTimeoutSeconds"),
        concurrent_exec_limit=data.get("concurrentExecLimit"),
        rate_limit_frequency_in_seconds=data.get("rateLimitFrequencyInSeconds"),
        rate_limit_per_frequency=data.get("rateLimitPerFrequency"),
        total_timeout_seconds=data.get("totalTimeoutSeconds"),
        input_keys=data["inputKeys"],
        output_keys=data["outputKeys"],
        input_schema=schema_def(data["inputSchema"]),
        output_schema=schema_def(data["outputSchema"]),
    )


def schema_def(data: dict[str, Any]) -> SchemaDef:
    return SchemaDef(
        name=data["name"],
        version=data.get("version", 1),
        type=data["type"],
        data=data["data"],
    )


def unregister_definitions(metadata: OrkesMetadataClient, task_name: str, workflow_name: str) -> None:
    try:
        metadata.unregister_workflow_def(workflow_name, 1)
    except Exception:
        pass
    try:
        metadata.unregister_task_def(task_name)
    except Exception:
        pass


def workflow_def(workflow_name: str, taskdef: TaskDef) -> WorkflowDef:
    task_ref = "hello_workspace"
    task_name = taskdef.name
    return WorkflowDef(
        name=workflow_name,
        description="Perago real Conductor/LakeFS smoke workflow.",
        version=1,
        schema_version=2,
        owner_email="data@example.com",
        input_parameters=["workspace", "params"],
        tasks=[
            WorkflowTask(
                name=task_name,
                task_reference_name=task_ref,
                type="SIMPLE",
                input_parameters={
                    "workspace": "${workflow.input.workspace}",
                    "params": "${workflow.input.params}",
                },
            )
        ],
        output_parameters={
            "workspace": f"${{{task_ref}.output.workspace}}",
            "result": f"${{{task_ref}.output.result}}",
        },
        input_schema=SchemaDef(
            name=f"{workflow_name}.input",
            version=1,
            type="JSON",
            data=taskdef.input_schema.data,
        ),
        output_schema=SchemaDef(
            name=f"{workflow_name}.output",
            version=1,
            type="JSON",
            data=taskdef.output_schema.data,
        ),
        enforce_schema=True,
    )


def wait_workflow(
    workflows: OrkesWorkflowClient,
    workflow_id: str,
    proc: subprocess.Popen[str],
    *,
    timeout_seconds: int,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            dump_worker_output(proc)
            raise RuntimeError(f"worker exited early with code {proc.returncode}")
        workflow = workflows.get_workflow(workflow_id, include_tasks=True)
        status = getattr(workflow, "status", None)
        if status != last_status:
            log(f"workflow poll status={status}")
            last_status = status
        if status in {"COMPLETED", "FAILED", "TERMINATED", "TIMED_OUT"}:
            return workflow
        time.sleep(1)
    stop_process(proc)
    raise TimeoutError(f"workflow did not finish within {timeout_seconds}s")


def find_staging_branch(repo: Repository, run_id: str) -> str | None:
    try:
        for branch in repo.branches():
            branch_id = getattr(branch, "id", "")
            if run_id in branch_id and "perago" in branch_id:
                return branch_id
    except Exception as exc:
        return f"branch-list-failed: {exc}"
    return None


def stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        dump_worker_output(proc)
        return
    terminate_process_group(proc, signal.SIGTERM)
    try:
        output, _ = proc.communicate(timeout=25)
    except subprocess.TimeoutExpired:
        terminate_process_group(proc, signal.SIGKILL)
        output, _ = proc.communicate(timeout=5)
    print_worker_output(output)


def terminate_process_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return


def dump_worker_output(proc: subprocess.Popen[str]) -> None:
    output, _ = proc.communicate(timeout=1)
    print_worker_output(output)


def print_worker_output(output: str | None) -> None:
    if output:
        log("worker output begin")
        print(output.rstrip(), flush=True)
        log("worker output end")


def log(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
