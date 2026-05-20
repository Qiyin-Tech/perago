from __future__ import annotations

import os
import re
import tempfile
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from perago.errors import RuntimeConfigError


LOG_SIZE_UNITS = {
    "KB": 1024,
    "MB": 1024 * 1024,
    "GB": 1024 * 1024 * 1024,
}


class ConductorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    server_url: str
    auth_key: str | None = None
    auth_secret: str | None = None


class LakeFSConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_url: str
    access_key_id: str
    secret_access_key: str


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_root: Path
    log_root: Path
    log_file_max_size: int
    log_retention: timedelta
    worker_id_prefix: str
    conductor: ConductorConfig | None = None
    lakefs: LakeFSConfig | None = None


def load_runtime_config(
    module_target: str,
    *,
    cwd: Path | None = None,
    process_env: dict[str, str] | None = None,
    probe_roots: bool = True,
) -> RuntimeConfig:
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


def parse_conductor_config(env: dict[str, str]) -> ConductorConfig | None:
    server_url = _env_optional(env, "CONDUCTOR_SERVER_URL")
    auth_key = _env_optional(env, "CONDUCTOR_AUTH_KEY")
    auth_secret = _env_optional(env, "CONDUCTOR_AUTH_SECRET")
    if server_url is None and auth_key is None and auth_secret is None:
        return None
    if server_url is None:
        raise RuntimeConfigError("CONDUCTOR_SERVER_URL is required when Conductor auth is configured")
    if (auth_key is None) != (auth_secret is None):
        raise RuntimeConfigError("CONDUCTOR_AUTH_KEY and CONDUCTOR_AUTH_SECRET must be configured together")
    return ConductorConfig(server_url=server_url, auth_key=auth_key, auth_secret=auth_secret)


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
    return value.strip()
