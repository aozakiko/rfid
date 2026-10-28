"""Shared validation for numeric and boolean request fields."""
from __future__ import annotations

import math
import re
from typing import Any


def bounded_int(payload: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    raw = payload.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError(f"{key} 必须是整数")
    if isinstance(raw, str) and not re.fullmatch(r"[+-]?\d+", raw.strip()):
        raise ValueError(f"{key} 必须是整数")
    if isinstance(raw, float) and (not math.isfinite(raw) or not raw.is_integer()):
        raise ValueError(f"{key} 必须是整数")
    value = int(raw)
    if not low <= value <= high:
        raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
    return value


def bounded_float(payload: dict[str, Any], key: str, default: float, low: float, high: float) -> float:
    raw = payload.get(key, default)
    if isinstance(raw, bool):
        raise ValueError(f"{key} 必须是数值")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{key} 必须是数值") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
    return value


def boolean_value(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是 JSON 布尔值 true 或 false")
    return value
