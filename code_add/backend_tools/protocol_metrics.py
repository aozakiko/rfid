from __future__ import annotations

from typing import Any, Iterable


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if value > 0 and value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            parsed = int(stripped)
            return parsed if parsed > 0 else None
    return None


def _extend_with_numeric_values(bucket: list[int], values: Iterable[Any]) -> None:
    for value in values:
        parsed = _coerce_positive_int(value)
        if parsed is not None:
            bucket.append(parsed)


def normalize_protocol_metrics(verification_data: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(verification_data or {})
    frame_candidates: list[int] = []
    bit_candidates: list[int] = []
    notes: list[str] = []

    for key in (
        "frame_size",
        "frame_size_per_batch",
        "initial_frame_size",
        "verification_frame_size",
        "ee_frame_slots",
        "max_frame_size",
    ):
        parsed = _coerce_positive_int(normalized.get(key))
        if parsed is not None:
            frame_candidates.append(parsed)

    for key in (
        "bits_per_slot",
        "batch_size",
        "verification_bits_per_reply",
        "max_bits_per_slot",
    ):
        parsed = _coerce_positive_int(normalized.get(key))
        if parsed is not None:
            bit_candidates.append(parsed)

    bucket_sizes = normalized.get("bucket_final_frame_sizes")
    if isinstance(bucket_sizes, dict):
        _extend_with_numeric_values(frame_candidates, bucket_sizes.values())

    stage_frame_sizes = normalized.get("stage_frame_sizes")
    if isinstance(stage_frame_sizes, dict):
        _extend_with_numeric_values(frame_candidates, stage_frame_sizes.values())
    elif isinstance(stage_frame_sizes, (list, tuple, set)):
        _extend_with_numeric_values(frame_candidates, stage_frame_sizes)

    stage_bits = normalized.get("stage_bits_per_slot")
    if isinstance(stage_bits, dict):
        _extend_with_numeric_values(bit_candidates, stage_bits.values())
    elif isinstance(stage_bits, (list, tuple, set)):
        _extend_with_numeric_values(bit_candidates, stage_bits)

    if not bit_candidates:
        if normalized.get("method") == "Full Singulation and Read":
            bit_candidates.append(1)
            notes.append("bits_per_slot 由 method=Full Singulation and Read 推断为 1")
        elif any(
            key in normalized
            for key in ("final_exact_counts", "phase2_exact_counts", "final_candidates")
        ):
            bit_candidates.append(1)
            notes.append("bits_per_slot 由精确计数阶段结果推断为 1")

    if not bit_candidates:
        phase1_candidates = normalized.get("phase1_top_candidates")
        if isinstance(phase1_candidates, dict) and phase1_candidates:
            bit_candidates.append(len(phase1_candidates))
            notes.append("bits_per_slot 由候选并行估算规模推断")

    normalized["frame_size"] = max(frame_candidates) if frame_candidates else 0
    normalized["bits_per_slot"] = max(bit_candidates) if bit_candidates else 0

    if not frame_candidates:
        notes.append("缺少可用于验证的 frame_size 指标")
    if not bit_candidates:
        notes.append("缺少可用于验证的 bits_per_slot 指标")

    normalized["validation_notes"] = notes
    return normalized