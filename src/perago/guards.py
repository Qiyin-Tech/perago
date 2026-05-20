from __future__ import annotations

from os import PathLike, fspath
from pathlib import Path, PurePath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from perago.errors import GuardrailViolation, TaskDefinitionError


GuardrailKind = Literal["require_file", "require_dir", "require_glob", "forbid_glob"]


class _WorkspaceGuardrail(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: GuardrailKind
    path: str
    min_count: int | None = Field(default=None, ge=0)
    max_count: int | None = Field(default=None, ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _canonical_workspace_path(value)

    @model_validator(mode="after")
    def validate_count_bounds(self) -> _WorkspaceGuardrail:
        if self.kind in {"require_file", "require_dir"}:
            if self.min_count is not None or self.max_count is not None:
                raise TaskDefinitionError(f"{self.kind} does not accept count bounds")
            return self

        if self.kind == "require_glob" and self.min_count is None:
            object.__setattr__(self, "min_count", 1)

        if (
            self.min_count is not None
            and self.max_count is not None
            and self.min_count > self.max_count
        ):
            raise TaskDefinitionError("guardrail min_count must be <= max_count")
        return self

    def label(self) -> str:
        return f"{self.kind}('{self.path}')"


def require_file(path: str | PathLike[str]) -> _WorkspaceGuardrail:
    return _WorkspaceGuardrail(kind="require_file", path=_canonical_workspace_path(path))


def require_dir(path: str | PathLike[str]) -> _WorkspaceGuardrail:
    return _WorkspaceGuardrail(kind="require_dir", path=_canonical_workspace_path(path))


def require_glob(
    pattern: str | PathLike[str],
    *,
    min_count: int = 1,
    max_count: int | None = None,
) -> _WorkspaceGuardrail:
    return _WorkspaceGuardrail(
        kind="require_glob",
        path=_canonical_workspace_path(pattern),
        min_count=min_count,
        max_count=max_count,
    )


def forbid_glob(pattern: str | PathLike[str]) -> _WorkspaceGuardrail:
    return _WorkspaceGuardrail(kind="forbid_glob", path=_canonical_workspace_path(pattern))


def check_guardrails(root: Path, guardrails: list[_WorkspaceGuardrail], phase: str) -> None:
    for guardrail in guardrails:
        if guardrail.kind == "require_file":
            candidate = root / guardrail.path
            if not candidate.is_file():
                raise GuardrailViolation(f"{phase} guardrail {guardrail.label()} did not find a file")
        elif guardrail.kind == "require_dir":
            candidate = root / guardrail.path
            if not candidate.is_dir():
                raise GuardrailViolation(f"{phase} guardrail {guardrail.label()} did not find a directory")
        else:
            count = len(list(root.glob(guardrail.path)))
            if guardrail.kind == "forbid_glob":
                if count:
                    raise GuardrailViolation(
                        f"{phase} guardrail {guardrail.label()} matched {count} files"
                    )
                continue

            if guardrail.min_count is not None and count < guardrail.min_count:
                raise GuardrailViolation(
                    f"{phase} guardrail {guardrail.label()} matched {count} files; "
                    f"min_count={guardrail.min_count}"
                )
            if guardrail.max_count is not None and count > guardrail.max_count:
                raise GuardrailViolation(
                    f"{phase} guardrail {guardrail.label()} matched {count} files; "
                    f"max_count={guardrail.max_count}"
                )


def _canonical_workspace_path(value: str | PathLike[str]) -> str:
    parts = _path_parts(value)
    if not parts:
        raise TaskDefinitionError("workspace guardrail paths must not be empty")
    if any(part in {"", ".", ".."} for part in parts):
        raise TaskDefinitionError("workspace guardrail paths must be relative and must not contain '..'")
    return "/".join(parts)


def _path_parts(value: str | PathLike[str]) -> tuple[str, ...]:
    if isinstance(value, PureWindowsPath):
        if value.drive or value.root:
            raise TaskDefinitionError("drive-qualified or absolute guardrail paths are not allowed")
        return tuple(value.parts)

    if isinstance(value, PurePath):
        if value.is_absolute():
            raise TaskDefinitionError("absolute guardrail paths are not allowed")
        return tuple(value.parts)

    text = fspath(value)
    if "\\" in text:
        raise TaskDefinitionError("string guardrail paths must use '/' separators")
    if text.startswith("/"):
        raise TaskDefinitionError("guardrail paths must be relative to WorkspaceSpec(prefix=...)")
    return tuple(text.split("/"))
