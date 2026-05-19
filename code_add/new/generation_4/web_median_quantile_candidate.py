import math
from typing import List, Tuple, Dict, Any

from base import Tag, IProtocol


class web_median_quantile_candidate(IProtocol):
    """
    面向中位数/分位数估计的 RFID 子代协议。

    设计目标：
    - 继承父代 A 的“按值域逐步收缩、利用帧结构进行范围定位”的思想
    - 继承父代 B 的“必要时进行精确读取验证”的思想
    - 引入突变机制：动态帧长 + 分层位图计数草图 + 候选桶重检 + 早停
    """

    def __init__(self) -> None:
        self.default_quantile = 0.5
        self.max_frame_size = 32768
        self.max_bits_per_slot = 256

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        verification_data: Dict[str, Any] = {
            "frame_size": 0,
            "bits_per_slot": 0,
            "stage_frame_sizes": [],
            "stage_bits_per_slot": [],
            "total_frames": 0,
            "stages": [],
        }

        if price_min > price_max:
            return [price_min], 0, 0.0, verification_data

        num_tags = len(tags)
        if num_tags == 0:
            verification_data["frame_size"] = 1
            verification_data["bits_per_slot"] = 1
            verification_data["total_frames"] = 0
            return [price_min], 0, 0.0, verification_data

        q = self._extract_quantile(tags, default=self.default_quantile)
        q = max(0.0, min(1.0, q))
        target_rank = max(1, min(num_tags, int(math.ceil(q * num_tags))))

        current_low = price_min
        current_high = price_max

        bits_per_slot = self._choose_bits_per_slot(num_tags, current_high - current_low + 1)
        verification_data["bits_per_slot"] = bits_per_slot

        stage_index = 0
        early_stop_reason = None

        while current_low < current_high:
            stage_index += 1
            value_range = current_high - current_low + 1
            if value_range <= 1:
                break

            frame_size = self._choose_frame_size(num_tags, value_range, stage_index)
            frame_size = min(frame_size, value_range)
            frame_size = max(2, min(frame_size, self.max_frame_size))

            # --- 交叉部分：融合父代 A 的按值域分帧收缩思想与父代 B 的读写器统一广播、接收匿名聚合结果思想 ---
            total_frames += 1
            total_bits_sent_by_reader += self._estimate_stage_command_bits(
                current_low, current_high, frame_size, bits_per_slot, mode="hist"
            )
            slot_hist = self._simulate_tag_response_histogram(
                tags=tags,
                low=current_low,
                high=current_high,
                frame_size=frame_size,
            )
            total_slots += frame_size

            verification_data["stage_frame_sizes"].append(frame_size)
            verification_data["stage_bits_per_slot"].append(bits_per_slot)

            cumulative = 0
            chosen_slot = 0
            chosen_slot_count = 0
            for idx, cnt in enumerate(slot_hist):
                if cumulative + cnt >= target_rank:
                    chosen_slot = idx
                    chosen_slot_count = cnt
                    break
                cumulative += cnt

            slot_low, slot_high = self._slot_bounds(current_low, current_high, frame_size, chosen_slot)
            new_target_rank = target_rank - cumulative

            stage_info: Dict[str, Any] = {
                "stage": stage_index,
                "mode": "histogram_partition",
                "range_low": current_low,
                "range_high": current_high,
                "frame_size": frame_size,
                "bits_per_slot": bits_per_slot,
                "chosen_slot": chosen_slot,
                "chosen_slot_count": chosen_slot_count,
                "prefix_count": cumulative,
                "slot_low": slot_low,
                "slot_high": slot_high,
                "target_rank_before": target_rank,
                "target_rank_after": new_target_rank,
            }

            current_low, current_high = slot_low, slot_high
            target_rank = max(1, new_target_rank)
            verification_data["stages"].append(stage_info)

            # --- 突变部分：引入候选桶重检与早停全新机制 ---
            if current_low == current_high:
                early_stop_reason = "single_value_range"
                break

            bucket_width = current_high - current_low + 1
            if bucket_width <= max(4, frame_size // 2):
                verify_frame = min(bucket_width, self.max_frame_size)
                verify_frame = max(2, verify_frame)

                total_frames += 1
                total_bits_sent_by_reader += self._estimate_stage_command_bits(
                    current_low, current_high, verify_frame, bits_per_slot, mode="bitmap_recheck"
                )
                recheck_hist = self._simulate_tag_response_histogram(
                    tags=tags,
                    low=current_low,
                    high=current_high,
                    frame_size=verify_frame,
                )
                total_slots += verify_frame

                verification_data["stage_frame_sizes"].append(verify_frame)
                verification_data["stage_bits_per_slot"].append(bits_per_slot)

                cumulative2 = 0
                chosen_slot2 = 0
                chosen_count2 = 0
                for idx, cnt in enumerate(recheck_hist):
                    if cumulative2 + cnt >= target_rank:
                        chosen_slot2 = idx
                        chosen_count2 = cnt
                        break
                    cumulative2 += cnt

                slot_low2, slot_high2 = self._slot_bounds(current_low, current_high, verify_frame, chosen_slot2)
                stage_info2 = {
                    "stage": f"{stage_index}.recheck",
                    "mode": "bitmap_recheck",
                    "range_low": current_low,
                    "range_high": current_high,
                    "frame_size": verify_frame,
                    "bits_per_slot": bits_per_slot,
                    "chosen_slot": chosen_slot2,
                    "chosen_slot_count": chosen_count2,
                    "prefix_count": cumulative2,
                    "slot_low": slot_low2,
                    "slot_high": slot_high2,
                    "target_rank_before": target_rank,
                    "target_rank_after": max(1, target_rank - cumulative2),
                }
                verification_data["stages"].append(stage_info2)

                current_low, current_high = slot_low2, slot_high2
                target_rank = max(1, target_rank - cumulative2)

                if current_low == current_high:
                    early_stop_reason = "recheck_singleton"
                    break

                if (current_high - current_low + 1) <= 3:
                    early_stop_reason = "small_range_exact_verify"
                    break

            if stage_index >= 12:
                early_stop_reason = "max_stage_guard"
                break

        # --- 交叉部分：融合父代 A 的局部精确收尾思想与父代 B 的逐一读取验证思想 ---
        exact_counts = {}
        final_candidate_range = (current_low, current_high)
        if current_low <= current_high:
            total_frames += 1
            total_bits_sent_by_reader += self._estimate_stage_command_bits(
                current_low, current_high, max(1, current_high - current_low + 1), bits_per_slot, mode="exact_read"
            )
            exact_counts = self._simulate_tag_response_exact_counts(
                tags=tags,
                low=current_low,
                high=current_high,
            )
            total_slots += max(1, current_high - current_low + 1)

        estimated_quantile = self._select_quantile_from_exact_counts(
            exact_counts=exact_counts,
            low=current_low,
            high=current_high,
            target_rank_in_range=target_rank
        )

        if estimated_quantile is None:
            estimated_quantile = current_low

        verification_data["frame_size"] = verification_data["stage_frame_sizes"][0] if verification_data["stage_frame_sizes"] else 1
        verification_data["bits_per_slot"] = bits_per_slot
        verification_data["total_frames"] = total_frames
        verification_data["target_quantile"] = q
        verification_data["target_rank_global"] = int(math.ceil(q * num_tags)) if num_tags > 0 else 0
        verification_data["final_candidate_range"] = final_candidate_range
        verification_data["final_exact_counts"] = exact_counts
        verification_data["early_stop_reason"] = early_stop_reason

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        metric = estimated_quantile
        return [estimated_quantile], metric, total_time_ms, verification_data

    def _extract_quantile(self, tags: List[Tag], default: float = 0.5) -> float:
        if not tags:
            return default
        candidates = [
            "quantile", "q", "target_quantile", "percentile",
            "query_quantile", "desired_quantile"
        ]
        for attr in candidates:
            if hasattr(tags[0], attr):
                value = getattr(tags[0], attr)
                try:
                    return float(value)
                except Exception:
                    pass
        return default

    def _choose_bits_per_slot(self, num_tags: int, value_range: int) -> int:
        if num_tags <= 32:
            b = 8
        elif num_tags <= 256:
            b = 12
        elif num_tags <= 2048:
            b = 16
        else:
            b = 24
        if value_range > 100000:
            b = max(b, 24)
        return min(max(1, b), self.max_bits_per_slot)

    def _choose_frame_size(self, num_tags: int, value_range: int, stage_index: int) -> int:
        base = int(math.sqrt(max(1, value_range)))
        density_factor = int(math.sqrt(max(1, num_tags)))
        frame = max(8, min(base + density_factor, 256))
        if stage_index <= 2:
            frame = min(max(frame * 2, 16), 512)
        elif stage_index >= 5:
            frame = max(8, frame // 2)
        frame = min(frame, value_range)
        return min(max(2, frame), self.max_frame_size)

    def _estimate_stage_command_bits(
        self, low: int, high: int, frame_size: int, bits_per_slot: int, mode: str
    ) -> int:
        mode_bits = {
            "hist": 24,
            "bitmap_recheck": 32,
            "exact_read": 40,
        }.get(mode, 24)
        range_bits = 32 + 32
        frame_bits = 16
        bps_bits = 8
        return mode_bits + range_bits + frame_bits + bps_bits

    def _slot_bounds(self, low: int, high: int, frame_size: int, slot_idx: int) -> Tuple[int, int]:
        total = high - low + 1
        left = low + (slot_idx * total) // frame_size
        right = low + ((slot_idx + 1) * total) // frame_size - 1
        right = min(right, high)
        left = max(left, low)
        if right < left:
            right = left
        return left, right

    def _map_value_to_slot(self, value: int, low: int, high: int, frame_size: int) -> int:
        if high <= low:
            return 0
        total = high - low + 1
        relative = value - low
        idx = (relative * frame_size) // total
        if idx >= frame_size:
            idx = frame_size - 1
        if idx < 0:
            idx = 0
        return idx

    def _simulate_tag_response_histogram(
        self, tags: List[Tag], low: int, high: int, frame_size: int
    ) -> List[int]:
        hist = [0] * frame_size
        for tag in tags:
            value = self._tag_response_value_if_in_range(tag, low, high)
            if value is None:
                continue
            idx = self._map_value_to_slot(value, low, high, frame_size)
            hist[idx] += 1
        return hist

    def _simulate_tag_response_exact_counts(
        self, tags: List[Tag], low: int, high: int
    ) -> Dict[int, int]:
        counts: Dict[int, int] = {}
        for tag in tags:
            value = self._tag_response_value_if_in_range(tag, low, high)
            if value is None:
                continue
            counts[value] = counts.get(value, 0) + 1
        return counts

    def _tag_response_value_if_in_range(self, tag: Tag, low: int, high: int):
        value = getattr(tag, "price", None)
        if value is None:
            value = getattr(tag, "value", None)
        if value is None:
            return None
        if low <= value <= high:
            return value
        return None

    def _select_quantile_from_exact_counts(
        self, exact_counts: Dict[int, int], low: int, high: int, target_rank_in_range: int
    ):
        if not exact_counts:
            return None
        running = 0
        for value in sorted(exact_counts.keys()):
            running += exact_counts[value]
            if running >= target_rank_in_range:
                return value
        return high
