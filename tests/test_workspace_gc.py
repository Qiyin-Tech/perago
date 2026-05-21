import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from perago.workspace import (
    ATTEMPT_WORKSPACE_MARKER,
    garbage_collect_attempt_workspaces,
    garbage_collect_workspace_owner,
    sweep_abandoned_attempt_workspaces,
)


def test_sweep_removes_only_marked_attempt_workspaces(tmp_path) -> None:
    marked = tmp_path / "task_id=1"
    marked.mkdir(parents=True)
    (marked / ATTEMPT_WORKSPACE_MARKER).write_text(
        json.dumps(
            {
                "workflow_instance_id": "wf",
                "task_id": "1",
                "execution_id": "exec-1",
                "retry_count": 0,
                "task_def_name": "task",
                "owner_worker_id": "worker0001",
                "owner_pid": 1234,
                "owner_token": "dead-token",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "file.txt").write_text("keep", encoding="utf-8")

    removed = sweep_abandoned_attempt_workspaces(tmp_path)

    assert removed == [marked]
    assert not marked.exists()
    assert keep.exists()


def test_gc_keeps_active_owners_and_removes_old_dead_owners(tmp_path) -> None:
    old_started_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    active_process = _marked_workspace(
        tmp_path,
        "active-process",
        owner_worker_id="worker0001",
        owner_pid=111,
        owner_token="process-token",
        started_at=old_started_at,
    )
    active_token = _marked_workspace(
        tmp_path,
        "active-token",
        owner_worker_id="worker0002",
        owner_pid=222,
        owner_token="token-live",
        started_at=old_started_at,
    )
    dead = _marked_workspace(
        tmp_path,
        "dead",
        owner_worker_id="worker0003",
        owner_pid=333,
        owner_token="token-dead",
        started_at=old_started_at,
    )
    young = _marked_workspace(
        tmp_path,
        "young",
        owner_worker_id="worker0004",
        owner_pid=444,
        owner_token="token-young",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    removed = garbage_collect_attempt_workspaces(
        tmp_path,
        ttl=timedelta(hours=1),
        active_process_owners={("worker0001", 111)},
        active_owner_tokens={"token-live"},
    )

    assert removed == [dead]
    assert active_process.exists()
    assert active_token.exists()
    assert not dead.exists()
    assert young.exists()


def test_targeted_gc_removes_only_dead_owner_workspaces(tmp_path) -> None:
    started_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    target = _marked_workspace(
        tmp_path,
        "target",
        owner_worker_id="worker0001",
        owner_pid=111,
        owner_token="target-token",
        started_at=started_at,
    )
    other = _marked_workspace(
        tmp_path,
        "other",
        owner_worker_id="worker0002",
        owner_pid=222,
        owner_token="other-token",
        started_at=started_at,
    )

    removed = garbage_collect_workspace_owner(tmp_path, owner_worker_id="worker0001", owner_pid=111)

    assert removed == [target]
    assert not target.exists()
    assert other.exists()


def test_gc_skips_legacy_and_bad_markers(tmp_path) -> None:
    legacy = tmp_path / "task_id=legacy"
    legacy.mkdir()
    (legacy / ATTEMPT_WORKSPACE_MARKER).write_text("{}", encoding="utf-8")
    bad = tmp_path / "task_id=bad"
    bad.mkdir()
    (bad / ATTEMPT_WORKSPACE_MARKER).write_text("{bad-json", encoding="utf-8")

    removed = garbage_collect_attempt_workspaces(
        tmp_path,
        ttl=timedelta(seconds=0),
        active_process_owners=set(),
        active_owner_tokens=set(),
    )

    assert removed == []
    assert legacy.exists()
    assert bad.exists()


def _marked_workspace(
    root: Path,
    task_id: str,
    *,
    owner_worker_id: str,
    owner_pid: int,
    owner_token: str,
    started_at: str,
) -> Path:
    workspace = root / f"task_id={task_id}"
    workspace.mkdir()
    (workspace / ATTEMPT_WORKSPACE_MARKER).write_text(
        json.dumps(
            {
                "workflow_instance_id": "wf",
                "task_id": task_id,
                "execution_id": f"exec-{task_id}",
                "retry_count": 0,
                "task_def_name": "task",
                "owner_worker_id": owner_worker_id,
                "owner_pid": owner_pid,
                "owner_token": owner_token,
                "started_at": started_at,
            }
        ),
        encoding="utf-8",
    )
    return workspace
