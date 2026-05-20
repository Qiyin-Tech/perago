from __future__ import annotations

import re


def safe_segment(value: object) -> str:
    text = str(value)
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", text).strip("._") or "unknown"
