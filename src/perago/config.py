from __future__ import annotations

import os
import re
import tempfile
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from perago.errors import RuntimeConfigError


LOG_SIZE_UNITS = {
    "KB": 1024,
    "MB": 1024 * 1024,
    "GB": 1024 * 1024 * 1024,
}
ExecutionMode = Literal["process", "thread"]


class ConductorConfig(BaseModel):
    """
    Worker-local Conductor connection settings.

    ``ConductorConfig`` is loaded from process environment variables and local
    ``.env`` files by :func:`load_runtime_config`. It is runtime-only
    configuration: the server URL is not written into generated TaskDefs and is
    not passed through Conductor task input.

    Parameters
    ----------
    server_url : str
        Conductor API endpoint read from ``CONDUCTOR_SERVER_URL``. Surrounding
        whitespace is stripped during environment parsing, empty values are
        treated as not configured, and the placeholder value ``"replace-me"``
        is rejected before model construction.

    See Also
    --------
    load_runtime_config : Load this model from worker environment settings.
    RuntimeConfig : Full runtime configuration containing this model.

    Notes
    -----
    The model is frozen and rejects unknown fields. ``perago check`` and
    ``perago extract`` can run without this config, but ``perago start`` requires
    it before starting worker child processes.

    Examples
    --------
    >>> ConductorConfig(server_url="http://localhost:8080/api")
    ConductorConfig(...)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    server_url: str


class LakeFSConfig(BaseModel):
    """
    Worker-local LakeFS connection settings.

    ``LakeFSConfig`` is assembled from the LakeFS environment variables used by
    the worker runtime. The values stay local to the worker process and are not
    serialized into Conductor task input, task output, or generated TaskDefs.

    Parameters
    ----------
    endpoint_url : str
        LakeFS endpoint read from ``LAKECTL_SERVER_ENDPOINT_URL``.
    access_key_id : str
        LakeFS access key id read from
        ``LAKECTL_CREDENTIALS_ACCESS_KEY_ID``.
    secret_access_key : str
        LakeFS secret access key read from
        ``LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY``.

    Raises
    ------
    RuntimeConfigError
        Raised by :func:`load_runtime_config` when only part of the LakeFS
        environment variable set is present or when a value is still
        ``"replace-me"``.

    See Also
    --------
    load_runtime_config : Load this model from worker environment settings.
    RuntimeConfig : Full runtime configuration containing this model.

    Notes
    -----
    The model is frozen and rejects unknown fields. The three LakeFS variables
    must be configured together for ``perago start``; ``perago check`` and
    ``perago extract`` may omit all three.

    Examples
    --------
    >>> LakeFSConfig(
    ...     endpoint_url="http://localhost:8000",
    ...     access_key_id="key",
    ...     secret_access_key="secret",
    ... )
    LakeFSConfig(...)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_url: str
    access_key_id: str
    secret_access_key: str


class RuntimeConfig(BaseModel):
    """
    Complete worker-local runtime configuration.

    ``RuntimeConfig`` describes local workspace storage, worker logging,
    process identity, and optional Conductor and LakeFS connection settings. It
    is loaded before task module import by the CLI and stays outside the task
    author contract.

    Parameters
    ----------
    workspace_root : pathlib.Path
        Root directory for attempt-local workspaces. Defaults to
        ``<tempdir>/perago/workspaces`` when ``PERAGO_WORKSPACE_ROOT`` is not
        configured.
    log_root : pathlib.Path
        Root directory for worker JSONL logs. Defaults to
        ``<tempdir>/perago/logs`` when ``PERAGO_LOG_ROOT`` is not configured.
    log_file_max_size : int
        Log rotation threshold in bytes, parsed from
        ``PERAGO_LOG_FILE_MAX_SIZE``. The default is ``100MB``.
    log_retention : datetime.timedelta
        Log retention period parsed from ``PERAGO_LOG_RETENTION``. The default
        is ``30d``.
    worker_id_prefix : str
        ASCII alphanumeric prefix used by the supervisor to generate child
        ``PERAGO_WORKER_ID`` values.
    execution_mode : "process" or "thread"
        Worker execution model. Defaults to ``"process"`` and may be
        overridden by ``PERAGO_EXECUTION_MODE`` or the ``perago start`` CLI.
    workspace_gc_ttl : datetime.timedelta, default=24h
        Minimum age before supervisor periodic GC removes an abandoned
        attempt-local workspace. Parsed from ``PERAGO_WORKSPACE_GC_TTL``.
    workspace_gc_interval : datetime.timedelta, default=1h
        Interval for the supervisor workspace GC loop. Parsed from
        ``PERAGO_WORKSPACE_GC_INTERVAL``.
    shutdown_force_kill_after : datetime.timedelta or None, default=None
        Optional shutdown drain deadline. When configured through
        ``PERAGO_SHUTDOWN_FORCE_KILL_AFTER``, child processes still alive after
        the deadline are force-killed.
    conductor : ConductorConfig or None, default=None
        Optional Conductor connection config. ``perago start`` requires it.
    lakefs : LakeFSConfig or None, default=None
        Optional LakeFS connection config. ``perago start`` requires it.

    See Also
    --------
    load_runtime_config : Build a runtime config from ``.env`` and process
        environment values.
    WorkerRuntime : Prepared runtime values for a running worker process.

    Notes
    -----
    The model is frozen and rejects unknown fields. None of these values are
    embedded in generated Conductor TaskDefs or task payloads.

    Examples
    --------
    >>> from datetime import timedelta
    >>> from pathlib import Path
    >>> RuntimeConfig(
    ...     workspace_root=Path("/tmp/perago/workspaces"),
    ...     log_root=Path("/tmp/perago/logs"),
    ...     log_file_max_size=104857600,
    ...     log_retention=timedelta(days=30),
    ...     worker_id_prefix="appworkersfeaturesbuild",
    ... )
    RuntimeConfig(...)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_root: Path
    log_root: Path
    log_file_max_size: int
    log_retention: timedelta
    worker_id_prefix: str
    execution_mode: ExecutionMode = "process"
    workspace_gc_ttl: timedelta = timedelta(hours=24)
    workspace_gc_interval: timedelta = timedelta(hours=1)
    shutdown_force_kill_after: timedelta | None = None
    conductor: ConductorConfig | None = None
    lakefs: LakeFSConfig | None = None


def load_runtime_config(
    module_target: str,
    *,
    cwd: Path | None = None,
    process_env: dict[str, str] | None = None,
    probe_roots: bool = True,
) -> RuntimeConfig:
    """
    Load worker-local runtime configuration.

    ``load_runtime_config`` reads a simple ``.env`` file from ``cwd`` and then
    overlays process environment variables. It parses Perago local directory
    settings, worker identity settings, and optional Conductor and LakeFS
    connection settings into a frozen :class:`RuntimeConfig`.

    Parameters
    ----------
    module_target : str
        Python module import path for the single task module. It is used to
        derive the default worker id prefix when
        ``PERAGO_WORKER_ID_PREFIX`` is not configured.
    cwd : pathlib.Path or None, default=None
        Directory used to locate ``.env``. ``None`` uses the current working
        directory.
    process_env : dict of str to str or None, default=None
        Environment mapping that overrides ``.env`` values. ``None`` reads
        :data:`os.environ`; an empty dictionary intentionally prevents reading
        the real process environment.
    probe_roots : bool, default=True
        Whether to create and remove temporary probe files under the resolved
        workspace and log roots to verify that both directories are writable.

    Returns
    -------
    RuntimeConfig
        Parsed runtime configuration for CLI commands and worker processes.

    Raises
    ------
    RuntimeConfigError
        If a configured value is malformed, a required LakeFS variable is
        missing from a partial LakeFS configuration, a connection placeholder is
        still set to ``"replace-me"``, or a probed root directory is not
        writable.

    See Also
    --------
    RuntimeConfig : Parsed configuration returned by this loader.
    prepare_worker_runtime : Prepare runtime identity and logging for a worker
        process.

    Notes
    -----
    ``perago check`` and ``perago extract`` use this loader but do not require
    Conductor or LakeFS config to be present. ``perago start`` performs
    additional checks after loading and requires both external service configs.

    Examples
    --------
    >>> load_runtime_config(
    ...     "app.workers.features_build",
    ...     process_env={"PERAGO_WORKER_ID_PREFIX": "featuresBuild"},
    ...     probe_roots=False,
    ... )
    RuntimeConfig(...)
    """
    base = cwd or Path.cwd()
    current_env = dict(os.environ) if process_env is None else process_env
    env = load_runtime_env(current_env, read_dotenv(base / ".env"))
    temp_root = Path(tempfile.gettempdir()) / "perago"
    config = RuntimeConfig(
        workspace_root=Path(env.get("PERAGO_WORKSPACE_ROOT", temp_root / "workspaces")),
        log_root=Path(env.get("PERAGO_LOG_ROOT", temp_root / "logs")),
        log_file_max_size=parse_log_file_max_size(env.get("PERAGO_LOG_FILE_MAX_SIZE")),
        log_retention=parse_log_retention(env.get("PERAGO_LOG_RETENTION")),
        worker_id_prefix=resolve_worker_id_prefix(module_target, env),
        execution_mode=parse_execution_mode(env.get("PERAGO_EXECUTION_MODE")),
        workspace_gc_ttl=parse_duration(
            env.get("PERAGO_WORKSPACE_GC_TTL"),
            default=timedelta(hours=24),
            name="PERAGO_WORKSPACE_GC_TTL",
        ),
        workspace_gc_interval=parse_duration(
            env.get("PERAGO_WORKSPACE_GC_INTERVAL"),
            default=timedelta(hours=1),
            name="PERAGO_WORKSPACE_GC_INTERVAL",
        ),
        shutdown_force_kill_after=parse_optional_duration(
            env.get("PERAGO_SHUTDOWN_FORCE_KILL_AFTER"),
            name="PERAGO_SHUTDOWN_FORCE_KILL_AFTER",
        ),
        conductor=parse_conductor_config(env),
        lakefs=parse_lakefs_config(env),
    )
    if probe_roots:
        check_writable_root(config.workspace_root)
        check_writable_root(config.log_root)
    return config


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_env_value(value.strip())
    return values


def load_runtime_env(process_env: dict[str, str], dotenv_env: dict[str, str]) -> dict[str, str]:
    merged = dict(dotenv_env)
    merged.update(process_env)
    return merged


def check_writable_root(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".perago-check-", dir=path) as probe:
            probe_file = Path(probe) / "write-test"
            probe_file.write_text("ok", encoding="utf-8")
            probe_file.unlink()
    except OSError as exc:
        raise RuntimeConfigError(f"{path} is not writable: {exc}") from exc


def parse_log_file_max_size(value: str | None) -> int:
    if value is None or value.strip() == "":
        return 100 * 1024 * 1024

    match = re.fullmatch(
        r"((?:0|[1-9][0-9]*)(?:\.[0-9]+)?)\s*(KB|MB|GB)",
        value.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        raise RuntimeConfigError(
            "PERAGO_LOG_FILE_MAX_SIZE must be a positive size such as '512KB', '100MB', or '1.5GB'"
        )

    amount = Decimal(match.group(1))
    if amount <= 0:
        raise RuntimeConfigError("PERAGO_LOG_FILE_MAX_SIZE must be greater than zero")
    unit = match.group(2).upper()
    return int((amount * LOG_SIZE_UNITS[unit]).to_integral_value(rounding=ROUND_CEILING))


def parse_log_retention(value: str | None) -> timedelta:
    if value is None or value.strip() == "":
        return timedelta(days=30)
    match = re.fullmatch(r"([1-9][0-9]*)d", value.strip(), flags=re.IGNORECASE)
    if not match:
        raise RuntimeConfigError("PERAGO_LOG_RETENTION must be a positive day count such as '7d' or '30d'")
    return timedelta(days=int(match.group(1)))


def parse_execution_mode(value: str | None) -> ExecutionMode:
    if value is None or value.strip() == "":
        return "process"
    mode = value.strip().lower()
    if mode not in {"process", "thread"}:
        raise RuntimeConfigError("PERAGO_EXECUTION_MODE must be either 'process' or 'thread'")
    return mode


def parse_duration(value: str | None, *, default: timedelta, name: str) -> timedelta:
    if value is None or value.strip() == "":
        return default
    match = re.fullmatch(r"([1-9][0-9]*)([smhd])", value.strip(), flags=re.IGNORECASE)
    if not match:
        raise RuntimeConfigError(f"{name} must be a positive duration such as '30s', '5m', '1h', or '24h'")
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def parse_optional_duration(value: str | None, *, name: str) -> timedelta | None:
    if value is None or value.strip() == "":
        return None
    return parse_duration(value, default=timedelta(seconds=1), name=name)


def parse_conductor_config(env: dict[str, str]) -> ConductorConfig | None:
    server_url = _env_optional(env, "CONDUCTOR_SERVER_URL")
    if server_url is None:
        return None
    return ConductorConfig(server_url=server_url)


def parse_lakefs_config(env: dict[str, str]) -> LakeFSConfig | None:
    endpoint_url = _env_optional(env, "LAKECTL_SERVER_ENDPOINT_URL")
    access_key_id = _env_optional(env, "LAKECTL_CREDENTIALS_ACCESS_KEY_ID")
    secret_access_key = _env_optional(env, "LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY")
    if endpoint_url is None and access_key_id is None and secret_access_key is None:
        return None
    missing = [
        name
        for name, value in [
            ("LAKECTL_SERVER_ENDPOINT_URL", endpoint_url),
            ("LAKECTL_CREDENTIALS_ACCESS_KEY_ID", access_key_id),
            ("LAKECTL_CREDENTIALS_SECRET_ACCESS_KEY", secret_access_key),
        ]
        if value is None
    ]
    if missing:
        raise RuntimeConfigError(f"LakeFS config is incomplete; missing {', '.join(missing)}")
    return LakeFSConfig(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def validate_worker_id_prefix(value: str) -> str:
    if not value:
        raise RuntimeConfigError("PERAGO_WORKER_ID_PREFIX must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise RuntimeConfigError("PERAGO_WORKER_ID_PREFIX must contain only ASCII letters and digits")
    return value


def default_worker_id_prefix(module_target: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9]+", "", module_target)
    return validate_worker_id_prefix(candidate)


def resolve_worker_id_prefix(module_target: str, env: dict[str, str]) -> str:
    configured = env.get("PERAGO_WORKER_ID_PREFIX")
    if configured is not None:
        return validate_worker_id_prefix(configured.strip())
    return default_worker_id_prefix(module_target)


def worker_id_for_child(prefix: str, index: int) -> str:
    return f"{prefix}{index:04d}"


def child_environment(base_env: dict[str, str], module_target: str, index: int) -> dict[str, str]:
    env = dict(base_env)
    prefix = resolve_worker_id_prefix(module_target, env)
    env["PERAGO_WORKER_ID_PREFIX"] = prefix
    env["PERAGO_WORKER_ID"] = worker_id_for_child(prefix, index)
    return env


def resolve_worker_id(module_target: str, env: dict[str, str]) -> str:
    configured = env.get("PERAGO_WORKER_ID")
    if configured:
        return configured
    return f"{default_worker_id_prefix(module_target)}-pid-{os.getpid()}"


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _env_optional(env: dict[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None or value.strip() == "":
        return None
    stripped = value.strip()
    if stripped == "replace-me":
        raise RuntimeConfigError(f"{name} must be replaced with a real value")
    return stripped
