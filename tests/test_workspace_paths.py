from pathlib import Path

import pytest

from perago import TaskDefinitionError, WorkspaceSpec
from perago.workspace import (
    ATTEMPT_WORKSPACE_MARKER,
    workspace_local_path,
    workspace_object_path,
    workspace_object_prefix,
)


def test_workspace_object_prefix_maps_root_prefix_to_empty_object_prefix() -> None:
    assert workspace_object_prefix(WorkspaceSpec(prefix="/")) == ""
    assert workspace_object_prefix(WorkspaceSpec(prefix="/audio/render")) == "audio/render"


def test_workspace_object_path_maps_local_paths_under_workspace_prefix() -> None:
    spec = WorkspaceSpec(prefix="audio/render")

    assert workspace_object_path(spec, "raw/input.wav") == "audio/render/raw/input.wav"
    assert workspace_object_path(spec, Path("stems") / "voice.wav") == "audio/render/stems/voice.wav"


def test_workspace_object_path_keeps_root_prefix_at_repository_root() -> None:
    assert workspace_object_path(WorkspaceSpec(prefix="/"), "manifest.json") == "manifest.json"


def test_workspace_local_path_maps_object_paths_under_prefix() -> None:
    spec = WorkspaceSpec(prefix="audio/render")

    assert workspace_local_path(spec, "audio/render/raw/input.wav") == Path("raw/input.wav")
    assert workspace_local_path(spec, "other/raw/input.wav") is None


def test_workspace_local_path_keeps_root_prefix_at_repository_root() -> None:
    assert workspace_local_path(WorkspaceSpec(prefix="/"), "manifest.json") == Path("manifest.json")


def test_workspace_local_path_skips_attempt_marker() -> None:
    assert workspace_local_path(WorkspaceSpec(prefix="/"), ATTEMPT_WORKSPACE_MARKER) is None
    assert workspace_local_path(WorkspaceSpec(prefix="/audio/render"), f"audio/render/{ATTEMPT_WORKSPACE_MARKER}") is None


@pytest.mark.parametrize(
    ("prefix", "object_path"),
    [
        ("/", "C:/Users/Public/payload.py"),
        ("/", "C:evil/file.txt"),
        ("audio/render", "audio/render/C:/Users/Public/payload.py"),
        ("audio/render", "audio/render/C:evil/file.txt"),
    ],
)
def test_workspace_local_path_rejects_drive_qualified_strings(
    prefix: str, object_path: str
) -> None:
    with pytest.raises(TaskDefinitionError, match="drive-qualified"):
        workspace_local_path(WorkspaceSpec(prefix=prefix), object_path)


def test_workspace_object_path_rejects_paths_that_escape_workspace_root() -> None:
    with pytest.raises(TaskDefinitionError, match="relative"):
        workspace_object_path(WorkspaceSpec(prefix="/"), "../manifest.json")
