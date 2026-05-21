from __future__ import annotations

import re
from os import PathLike, fspath
from pathlib import Path, PurePath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from perago.errors import GuardrailViolation, TaskDefinitionError


GuardrailKind = Literal["require_file", "require_dir", "require_glob", "forbid_glob"]
_WINDOWS_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


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
    """Require one workspace-relative file to exist.

    ``require_file`` creates a guardrail object for ``WorkspaceSpec.pre`` or
    ``WorkspaceSpec.post``. Paths are interpreted inside the task's workspace
    prefix and must use portable POSIX-style relative syntax.

    Parameters
    ----------
    path : str or os.PathLike[str]
        Workspace-relative file path. Absolute paths, ``..`` segments,
        backslash-separated strings, and drive-qualified paths are rejected.

    Returns
    -------
    Workspace guardrail
        Guardrail consumed by :class:`perago.WorkspaceSpec`.

    Raises
    ------
    TaskDefinitionError
        If ``path`` is empty or is not a valid workspace-relative path.

    See Also
    --------
    require_dir : Require one directory.
    require_glob : Require files matching a glob.
    forbid_glob : Reject files matching a glob.

    Examples
    --------
    >>> WorkspaceSpec(prefix="/", pre=[require_file("input/data.csv")])
    WorkspaceSpec(...)
    """
    return _WorkspaceGuardrail(kind="require_file", path=_canonical_workspace_path(path))


def require_dir(path: str | PathLike[str]) -> _WorkspaceGuardrail:
    """Require one workspace-relative directory to exist.

    Parameters
    ----------
    path : str or os.PathLike[str]
        Workspace-relative directory path using ``/`` separators.

    Returns
    -------
    Workspace guardrail
        Guardrail consumed by :class:`perago.WorkspaceSpec`.

    Raises
    ------
    TaskDefinitionError
        If ``path`` is empty, absolute, contains ``..``, or uses unsupported
        separators.

    See Also
    --------
    require_file : Require one file.
    require_glob : Require files matching a glob.
    forbid_glob : Reject files matching a glob.

    Examples
    --------
    >>> WorkspaceSpec(prefix="audio/render", pre=[require_dir("raw")])
    WorkspaceSpec(...)
    """
    return _WorkspaceGuardrail(kind="require_dir", path=_canonical_workspace_path(path))


def require_glob(
    pattern: str | PathLike[str],
    *,
    min_count: int = 1,
    max_count: int | None = None,
) -> _WorkspaceGuardrail:
    """Require a bounded number of workspace files matching a glob pattern.

    The pattern is evaluated with :meth:`pathlib.Path.glob` inside the local
    attempt workspace when guardrails run. The default lower bound requires at
    least one match.

    Parameters
    ----------
    pattern : str or os.PathLike[str]
        Workspace-relative glob pattern using ``/`` separators.
    min_count : int, default=1
        Minimum number of matches required for the guardrail to pass.
    max_count : int or None, default=None
        Maximum number of matches allowed. ``None`` disables the upper bound.

    Returns
    -------
    Workspace guardrail
        Guardrail consumed by :class:`perago.WorkspaceSpec`.

    Raises
    ------
    TaskDefinitionError
        If ``pattern`` is not workspace-relative, count bounds are invalid, or
        ``min_count`` is greater than ``max_count``.

    See Also
    --------
    require_file : Require one file.
    require_dir : Require one directory.
    forbid_glob : Reject files matching a glob.

    Examples
    --------
    >>> WorkspaceSpec(pre=[require_glob("raw/**/*.parquet", min_count=1)])
    WorkspaceSpec(...)
    """
    return _WorkspaceGuardrail(
        kind="require_glob",
        path=_canonical_workspace_path(pattern),
        min_count=min_count,
        max_count=max_count,
    )


def forbid_glob(pattern: str | PathLike[str]) -> _WorkspaceGuardrail:
    """Reject workspace files matching a glob pattern.

    Parameters
    ----------
    pattern : str or os.PathLike[str]
        Workspace-relative glob pattern using ``/`` separators.

    Returns
    -------
    Workspace guardrail
        Guardrail consumed by :class:`perago.WorkspaceSpec`.

    Raises
    ------
    TaskDefinitionError
        If ``pattern`` is empty or is not a valid workspace-relative path.

    See Also
    --------
    require_file : Require one file.
    require_dir : Require one directory.
    require_glob : Require files matching a glob.

    Examples
    --------
    >>> WorkspaceSpec(post=[forbid_glob("tmp/**")])
    WorkspaceSpec(...)
    """
    return _WorkspaceGuardrail(kind="forbid_glob", path=_canonical_workspace_path(pattern))


def check_guardrails(root: Path, guardrails: list[_WorkspaceGuardrail], phase: str) -> None:
    """Evaluate workspace guardrails against a local attempt workspace.

    This function is used by the runtime before and after a workspace task body
    executes. ``phase`` is included in error messages so callers can map pre and
    post failures to the correct runtime result classification.

    Parameters
    ----------
    root : pathlib.Path
        Local attempt workspace directory to inspect.
    guardrails : list of workspace guardrails
        Guardrails produced by :func:`require_file`, :func:`require_dir`,
        :func:`require_glob`, or :func:`forbid_glob`.
    phase : str
        Human-readable phase label, usually ``"pre"`` or ``"post"``.

    Returns
    -------
    None
        The function returns ``None`` when all guardrails pass.

    Raises
    ------
    GuardrailViolation
        If a required file or directory is missing, a required glob has too few
        or too many matches, or a forbidden glob has any matches.

    Examples
    --------
    >>> check_guardrails(workspace_dir, [require_file("input/data.csv")], "pre")
    """
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
    if _WINDOWS_DRIVE_PREFIX_RE.match(text):
        raise TaskDefinitionError("drive-qualified guardrail paths are not allowed")
    return tuple(text.split("/"))
