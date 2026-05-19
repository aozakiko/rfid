from typing import List, Tuple, Dict, Any
import math
from base import Tag, IProtocol


class web_mean_estimation_candidate(IProtocol):
    """
    面向工业物联网多维统计场景的连续数值均值估计协议：
    - 阶段1：基于价格/数值桶时隙帧进行匿名聚合，获得粗粒度分布草图
    - 阶段2：对高不确定性桶执行自适应细化重检
    - 阶段3：对最终桶分布执行保守均值估计，并基于区间宽度给出误差指标

    返回:
        estimated_values: [estimated_mean]
        metric: 置信半径风格误差指标（越小越好）
        total_time_ms: 总执行时间
        verification_data: 协议过程数据
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[float], float, float, Dict]:
        value_range = max(0, price_max - price_min + 1)
        num_tags = len(tags)

        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []

        if value_range <= 0 or num_tags <= 0:
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "bucket_ranges": [],
                "bucket_counts": {},
                "refined_bucket_counts": {},
                "task_type": "continuous_mean_estimation",
                "estimation_method": "bucket_midpoint_weighted_mean",
                "early_stop_triggered": False
            }
            return [0.0], 0.0, 0.0, verification_data

        # --- 交叉部分：融合父代 A 的“匿名桶聚合 + 自适应重检 + 早停”思想与父代 B 的“精确逻辑正确性导向/逐阶段缩小不确定区间”思想 ---
        # 读写器不直接访问单标签私有属性，而是先通过匿名桶计数得到粗略分布，再对不确定桶进行局部细化。
        # 最终采用加权桶中点估计总体均值，兼顾通信开销与估计精度。

        # --- 突变部分：引入“面向均值估计的方差感知细化 + 误差界驱动早停”全新机制 ---
        # 1) 用桶宽 * 桶载荷 作为不确定性评分，优先细化对均值误差贡献大的桶
        # 2) 每次细化后评估理论误差上界，若已低于阈值则提前终止
        # 3) 同一协议骨架可扩展到方差/分位数等统计任务

        frame_size_stage1 = self._choose_frame_size(value_range=value_range, num_tags=num_tags)
        bits_per_slot_stage1 = self._choose_bits_per_slot(value_range=value_range, frame_size=frame_size_stage1, expected_density=max(1, num_tags // max(1, frame_size_stage1)))
        stage_frame_sizes.append(frame_size_stage1)
        stage_bits_per_slot.append(bits_per_slot_stage1)

        total_frames += 1
        total_bits_sent_by_reader += self._reader_bits_for_bucket_frame(
            frame_size_stage1, bits_per_slot_stage1, price_min, price_max, stage_id=1
        )
        total_slots += frame_size_stage1

        stage1_signal = self._simulate_tag_bucket_bitmap_response(
            tags=tags,
            value_attr="price",
            range_min=price_min,
            range_max=price_max,
            frame_size=frame_size_stage1,
            bits_per_slot=bits_per_slot_stage1
        )
        stage1_counts = self._decode_bucket_loads(stage1_signal)

        bucket_ranges = self._build_bucket_ranges(price_min, price_max, frame_size_stage1)
        final_buckets = [{"low": low, "high": high, "count": stage1_counts.get(idx, 0), "stage": 1, "bucket_index": idx}
                         for idx, (low, high) in enumerate(bucket_ranges)]

        refined_bucket_counts: Dict[str, int] = {}
        early_stop_triggered = False

        max_refine_rounds = 2
        target_error_ratio = 0.03 if value_range > 0 else 0.0
        target_abs_error = max(1.0, value_range * target_error_ratio)

        for refine_round in range(max_refine_rounds):
            current_error_bound = self._estimate_mean_error_bound(final_buckets, num_tags)
            if current_error_bound <= target_abs_error:
                early_stop_triggered = True
                break

            refine_candidates = self._select_refine_buckets_for_mean(final_buckets, top_k=min(4, max(1, len(final_buckets) // 4)))
            refine_candidates = [b for b in refine_candidates if b["count"] > 0 and b["low"] < b["high"]]

            if not refine_candidates:
                early_stop_triggered = True
                break

            new_final_buckets = []
            refined_any = False

            for b in final_buckets:
                candidate_match = None
                for c in refine_candidates:
                    if b["low"] == c["low"] and b["high"] == c["high"] and b["count"] == c["count"]:
                        candidate_match = c
                        break

                if candidate_match is None:
                    new_final_buckets.append(b)
                    continue

                sub_min = b["low"]
                sub_max = b["high"]
                sub_range = max(1, sub_max - sub_min + 1)
                sub_frame_size = self._choose_refine_frame_size(sub_range=sub_range, parent_count=b["count"])
                sub_bits_per_slot = self._choose_bits_per_slot(value_range=sub_range, frame_size=sub_frame_size, expected_density=max(1, b["count"] // max(1, sub_frame_size)))

                stage_frame_sizes.append(sub_frame_size)
                stage_bits_per_slot.append(sub_bits_per_slot)

                total_frames += 1
                total_bits_sent_by_reader += self._reader_bits_for_bucket_frame(
                    sub_frame_size, sub_bits_per_slot, sub_min, sub_max, stage_id=refine_round + 2
                )
                total_slots += sub_frame_size

                sub_signal = self._simulate_tag_bucket_bitmap_response_filtered_range(
                    tags=tags,
                    value_attr="price",
                    range_min=price_min,
                    range_max=price_max,
                    active_min=sub_min,
                    active_max=sub_max,
                    frame_size=sub_frame_size,
                    bits_per_slot=sub_bits_per_slot
                )
                sub_counts = self._decode_bucket_loads(sub_signal)
                sub_ranges = self._build_bucket_ranges(sub_min, sub_max, sub_frame_size)

                local_sum = 0
                for idx, (low, high) in enumerate(sub_ranges):
                    cnt = sub_counts.get(idx, 0)
                    local_sum += cnt
                    refined_bucket_counts[f"{low}-{high}"] = int(cnt)
                    if cnt > 0:
                        new_final_buckets.append({
                            "low": low,
                            "high": high,
                            "count": cnt,
                            "stage": refine_round + 2,
                            "bucket_index": idx
                        })

                # 饱和修正：若由于 bits_per_slot 截断导致子桶总数不足，则保守保留残差在父桶中
                if local_sum < b["count"]:
                    residual = b["count"] - local_sum
                    new_final_buckets.append({
                        "low": sub_min,
                        "high": sub_max,
                        "count": residual,
                        "stage": refine_round + 2,
                        "bucket_index": -1
                    })

                refined_any = True

            final_buckets = self._merge_same_ranges(new_final_buckets)

            if not refined_any:
                early_stop_triggered = True
                break

        estimated_mean = self._estimate_mean_from_buckets(final_buckets, fallback=(price_min + price_max) / 2.0)
        metric = self._estimate_mean_error_bound(final_buckets, num_tags)

        verification_data = {
            "frame_size": frame_size_stage1,
            "bits_per_slot": bits_per_slot_stage1,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "total_frames": total_frames,
            "bucket_ranges": [
                {
                    "low": int(b["low"]),
                    "high": int(b["high"]),
                    "count": int(b["count"]),
                    "stage": int(b["stage"])
                }
                for b in final_buckets
            ],
            "bucket_counts": {str(i): int(stage1_counts.get(i, 0)) for i in range(frame_size_stage1)},
            "refined_bucket_counts": refined_bucket_counts,
            "task_type": "continuous_mean_estimation",
            "estimation_method": "bucket_midpoint_weighted_mean",
            "estimated_mean_error_bound": float(metric),
            "early_stop_triggered": early_stop_triggered
        }

        total_time_ms = self._compute_total_time(
            total_bits_sent_by_reader=total_bits_sent_by_reader,
            total_slots=total_slots,
            bits_per_slot=max(stage_bits_per_slot) if stage_bits_per_slot else 1,
            total_frames=total_frames
        )

        return [float(estimated_mean)], float(metric), float(total_time_ms), verification_data

    def _choose_frame_size(self, value_range: int, num_tags: int) -> int:
        if value_range <= 1:
            return 1
        target = min(32768, max(16, min(value_range, max(32, num_tags * 2))))
        return self._next_power_of_two(target)

    def _choose_refine_frame_size(self, sub_range: int, parent_count: int) -> int:
        if sub_range <= 1:
            return 1
        target = min(32768, max(8, min(sub_range, max(16, parent_count * 2))))
        return self._next_power_of_two(target)

    def _choose_bits_per_slot(self, value_range: int, frame_size: int, expected_density: int) -> int:
        if frame_size <= 1:
            return 1
        density = float(max(1, value_range)) / float(max(1, frame_size))
        score = max(density, float(expected_density))
        if score <= 1.5:
            return 2
        if score <= 4:
            return 3
        if score <= 8:
            return 4
        if score <= 16:
            return 5
        return min(8, 256)

    def _next_power_of_two(self, x: int) -> int:
        x = max(1, min(32768, int(x)))
        p = 1
        while p < x:
            p <<= 1
        return min(32768, p)

    def _reader_bits_for_bucket_frame(self, frame_size: int, bits_per_slot: int, range_min: int, range_max: int, stage_id: int) -> int:
        return int(
            64 + 16 + 8 + 8 +
            frame_size.bit_length() +
            bits_per_slot.bit_length() +
            abs(range_min).bit_length() + 1 +
            abs(range_max).bit_length() + 1 +
            max(1, stage_id).bit_length()
        )

    def _build_bucket_ranges(self, range_min: int, range_max: int, frame_size: int) -> List[Tuple[int, int]]:
        ranges = []
        value_range = max(1, range_max - range_min + 1)
        for b in range(frame_size):
            low = range_min + (b * value_range) // frame_size
            high = range_min + ((b + 1) * value_range) // frame_size - 1
            if b == frame_size - 1:
                high = range_max
            low = max(low, range_min)
            high = min(high, range_max)
            if low > high:
                low = high = max(range_min, min(range_max, low))
            ranges.append((low, high))
        return ranges

    def _bucket_index_for_value(self, value: int, range_min: int, range_max: int, frame_size: int) -> int:
        value_range = max(1, range_max - range_min + 1)
        relative = value - range_min
        idx = int((relative * frame_size) / value_range)
        if idx < 0:
            idx = 0
        if idx >= frame_size:
            idx = frame_size - 1
        return idx

    def _simulate_tag_bucket_bitmap_response(
        self,
        tags: List[Tag],
        value_attr: str,
        range_min: int,
        range_max: int,
        frame_size: int,
        bits_per_slot: int
    ) -> List[int]:
        slot_counts = [0] * frame_size
        max_encodable = (1 << bits_per_slot) - 1

        for tag in tags:
            value = getattr(tag, value_attr, None)
            if value is None:
                continue
            try:
                v = int(round(float(value)))
            except Exception:
                continue
            if range_min <= v <= range_max:
                idx = self._bucket_index_for_value(v, range_min, range_max, frame_size)
                if slot_counts[idx] < max_encodable:
                    slot_counts[idx] += 1
        return slot_counts

    def _simulate_tag_bucket_bitmap_response_filtered_range(
        self,
        tags: List[Tag],
        value_attr: str,
        range_min: int,
        range_max: int,
        active_min: int,
        active_max: int,
        frame_size: int,
        bits_per_slot: int
    ) -> List[int]:
        slot_counts = [0] * frame_size
        max_encodable = (1 << bits_per_slot) - 1

        for tag in tags:
            value = getattr(tag, value_attr, None)
            if value is None:
                continue
            try:
                v = int(round(float(value)))
            except Exception:
                continue
            if range_min <= v <= range_max and active_min <= v <= active_max:
                idx = self._bucket_index_for_value(v, active_min, active_max, frame_size)
                if slot_counts[idx] < max_encodable:
                    slot_counts[idx] += 1
        return slot_counts

    def _decode_bucket_loads(self, bucket_signal: List[int]) -> Dict[int, int]:
        return {i: int(v) for i, v in enumerate(bucket_signal) if v > 0}

    def _select_refine_buckets_for_mean(self, buckets: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        if not buckets:
            return []
        scored = []
        for b in buckets:
            width = max(0, b["high"] - b["low"])
            score = (width + 1) * max(0, b["count"])
            if b["count"] > 0 and width > 0:
                scored.append((score, b))
        if not scored:
            return []
        scored.sort(key=lambda x: (-x[0], x[1]["low"], x[1]["high"]))
        cutoff_idx = min(len(scored), max(1, top_k)) - 1
        cutoff = scored[cutoff_idx][0]
        selected = [b for s, b in scored if s >= cutoff]
        return selected[:max(top_k, len(selected))]

    def _merge_same_ranges(self, buckets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for b in buckets:
            key = (int(b["low"]), int(b["high"]))
            if key not in merged:
                merged[key] = {
                    "low": int(b["low"]),
                    "high": int(b["high"]),
                    "count": int(b["count"]),
                    "stage": int(b["stage"]),
                    "bucket_index": int(b.get("bucket_index", -1))
                }
            else:
                merged[key]["count"] += int(b["count"])
                merged[key]["stage"] = max(int(merged[key]["stage"]), int(b["stage"]))
        result = list(merged.values())
        result.sort(key=lambda x: (x["low"], x["high"]))
        return result

    def _estimate_mean_from_buckets(self, buckets: List[Dict[str, Any]], fallback: float) -> float:
        total_count = 0
        weighted_sum = 0.0
        for b in buckets:
            cnt = max(0, int(b["count"]))
            if cnt <= 0:
                continue
            midpoint = (float(b["low"]) + float(b["high"])) / 2.0
            weighted_sum += midpoint * cnt
            total_count += cnt
        if total_count <= 0:
            return float(fallback)
        return float(weighted_sum / total_count)

    def _estimate_mean_error_bound(self, buckets: List[Dict[str, Any]], total_count: int) -> float:
        if total_count <= 0:
            return 0.0
        bound_sum = 0.0
        for b in buckets:
            cnt = max(0, int(b["count"]))
            width = max(0.0, float(b["high"]) - float(b["low"]))
            bound_sum += cnt * (width / 2.0)
        return float(bound_sum / float(total_count))

    def _compute_total_time(self, total_bits_sent_by_reader: int, total_slots: int, bits_per_slot: int, total_frames: int) -> float:
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        return float(T_reader + T_tag + T_overhead)
