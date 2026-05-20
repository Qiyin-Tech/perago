from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from perago._segments import safe_segment


PERAGO_LOG_TIMEZONE = timezone(timedelta(hours=8), name="UTC+08:00")


def patch_log_record(record: dict[str, Any]) -> None:
    record["time"] = record["time"].astimezone(PERAGO_LOG_TIMEZONE)


def configure_worker_logging(
    *,
    log_root: Path,
    module_target: str,
    worker_id: str,
    max_bytes: int,
    retention: timedelta,
) -> Path:
    started_at = datetime.now(PERAGO_LOG_TIMEZONE).strftime("%Y%m%dT%H%M%S%z")
    log_file = (
        log_root
        / safe_segment(module_target)
        / f"worker_id={safe_segment(worker_id)}"
        / f"pid={os.getpid()}__started={started_at}.jsonl"
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.configure(patcher=patch_log_record)
    logger.add(
        log_file,
        serialize=True,
        rotation=max_bytes,
        retention=retention,
        enqueue=True,
    )
    return log_file
