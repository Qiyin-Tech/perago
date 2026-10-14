"""Validate and snapshot task-authored JSON failure metadata without business coercion."""

from __future__ import annotations

import math
from typing import Any


def snapshot_failure_output(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copy a JSON object so caller mutations cannot change a reported failure."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("output_data must be a JSON object")
    try:
        return _copy_json_value(value)
    except RecursionError:
        # Cycles and excessively nested objects cannot cross the JSON/IPC boundary safely.
        raise TypeError("output_data must not contain cycles or excessive nesting") from None


def _copy_json_value(value: Any) -> Any:
    """Accept only finite JSON values and string object keys, without echoing values."""

    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    raise TypeError("output_data must contain only finite JSON values and string object keys")
