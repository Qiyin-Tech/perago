from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from perago.errors import RuntimeConfigError


LOG_SIZE_UNITS = {
    "KB": 1024,
    "MB": 1024 * 1024,
    "GB": 1024 * 1024 * 1024,
}


@dataclass(frozen=True)
class RuntimeConfig:
    workspace_root: Path
    log_root: Path
    log_file_max_size: int
    log_retention: timedelta
    worker_id_prefix: str


def load_runtime_config(
    module_target: str,
    *,
    cwd: Path | None = None,
    process_env: dict[str, str] | None = None,
    probe_roots: bool = True,
) -> RuntimeConfig:
    base = cwd or Path.cwd()
    env = load_runtime_env(process_env or dict(os.environ), read_dotenv(base / ".env"))
    temp_root = Path(tempfile.gettempdir()) / "perago"
    config = RuntimeConfig(
        workspace_root=Path(env.get("PERAGO_WORKSPACE_ROOT", temp_root / "workspaces")),
        log_root=Path(env.get("PERAGO_LOG_ROOT", temp_root / "logs")),
        log_file_max_size=parse_log_file_max_size(env.get("PERAGO_LOG_FILE_MAX_SIZE")),
        log_retention=parse_log_retention(env.get("PERAGO_LOG_RETENTION")),
        worker_id_prefix=resolve_worker_id_prefix(module_target, env),
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


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
