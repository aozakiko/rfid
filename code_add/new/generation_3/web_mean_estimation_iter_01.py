import math
from typing import List, Tuple, Dict, Any

try:
    from base import Tag, IProtocol, generate_random_seed
except ImportError:
    class Tag:
        def __init__(self, id, price=None, value=None, sensor_value=None, reading=None, measurement=None):
            self.id = id
            if price is not None:
                self.price = price
            if value is not None:
                self.value = value
            if sensor_value is not None:
                self.sensor_value = sensor_value
            if reading is not None:
                self.reading = reading
            if measurement is not None:
                self.measurement = measurement

    class IProtocol:
        pass

    def generate_random_seed():
        import random
        return random.randint(1, 2**31 - 1)

try:
    from hash import simple_hash
except ImportError:
    try:
        from base import simple_hash
    except ImportError:
        def simple_hash(value, seed):
            s = f"{value}-{seed}"
            h = 2166136261
            for ch in s:
                h ^= ord(ch)
                h = (h * 16777619) & 0xFFFFFFFF
            return h


class web_mean_estimation_iter_01(IProtocol):
    """
    面向工业传感 RFID 连续数值属性均值估计协议。
    """

    def _extract_tag_value(self, tag: Tag) -> float:
        if hasattr(tag, "value"):
            return float(getattr(tag, "value"))
        if hasattr(tag, "sensor_value"):
            return float(getattr(tag, "sensor_value"))
        if hasattr(tag, "reading"):
            return float(getattr(tag, "reading"))
        if hasattr(tag, "measurement"):
            return float(getattr(tag, "measurement"))
        if hasattr(tag, "price"):
            return float(getattr(tag, "price"))
        raise AttributeError("Tag must contain one of: value, sensor_value, reading, measurement, price")

    def _safe_bit_length_int(self, x: float) -> int:
        xi = int(abs(x))
        return max(1, xi.bit_length() + 1)

    def _value_to_bin(self, value: float, value_min: float, bin_width: float, num_bins: int) -> int:
        if num_bins <= 1 or bin_width <= 0:
            return 0
        idx = int((value - value_min) / bin_width)
        if idx < 0:
            idx = 0
        if idx >= num_bins:
            idx = num_bins - 1
        return idx

    def _rho_from_hash(self, value: int, width: int) -> int:
        if width <= 1:
            return 1
        count = 1
        for i in range(width - 1, -1, -1):
            if ((value >> i) & 1) == 0:
                count += 1
            else:
                break
        if count > width:
            count = width
        return count

    def _bitmap_rank(self, bitmap: int, width: int) -> int:
        rank = 0
        for i in range(width - 1, -1, -1):
            if ((bitmap >> i) & 1) == 1:
                rank += 1
            else:
                break
        return rank

    def _simulate_tag_responses_stage1(
        self,
        tags: List[Tag],
        seed: int,
        frame_size: int,
        bits_per_slot: int,
        value_min: float,
        bin_width: float,
        num_bins: int,
    ) -> List[int]:
        frame = [0] * frame_size
        for t in tags:
            value = self._extract_tag_value(t)
            bin_idx = self._value_to_bin(value, value_min, bin_width, num_bins)
            slot_id = simple_hash(bin_idx, seed) % frame_size
            rnd = simple_hash(t.id, seed)
            rho = self._rho_from_hash(rnd, bits_per_slot)
            bit_pos = bits_per_slot - rho
            if bit_pos < 0:
                bit_pos = 0
            frame[slot_id] |= (1 << bit_pos)
        return frame

    def _simulate_tag_responses_stage2(
        self,
        tags: List[Tag],
        seed: int,
        frame_size: int,
        bits_per_slot: int,
        value_min: float,
        value_max: float,
        candidate_bins: List[int],
        bin_width: float,
        num_bins: int,
    ) -> List[int]:
        frame = [0] * frame_size
        candidate_set = set(candidate_bins)
        max_level = (1 << bits_per_slot) - 1
        denom = max(value_max - value_min, 1e-9)

        for t in tags:
            value = self._extract_tag_value(t)
            bin_idx = self._value_to_bin(value, value_min, bin_width, num_bins)
            if bin_idx not in candidate_set:
                continue
            slot_id = simple_hash((bin_idx, "recheck"), seed) % frame_size
            normalized = (value - value_min) / denom
            q = int(round(normalized * max_level))
            if q < 0:
                q = 0
            elif q > max_level:
                q = max_level
            frame[slot_id] += q
        return frame

    def _estimate_bin_counts(
        self,
        seeds: List[int],
        physical_frames: List[List[int]],
        frame_size: int,
        bits_per_slot: int,
        num_bins: int,
    ) -> Dict[int, float]:
        # 基于父代B的草图思想，但修复为可解释的单桶HLL风格估计
        alpha = 0.77351
        stats = {b: {"sum_rank": 0.0, "num_obs": 0} for b in range(num_bins)}

        for round_idx, seed in enumerate(seeds):
            frame = physical_frames[round_idx]
            slot_to_bins: Dict[int, List[int]] = {}
            for b in range(num_bins):
                slot_id = simple_hash(b, seed) % frame_size
                slot_to_bins.setdefault(slot_id, []).append(b)

            for slot_id, bins in slot_to_bins.items():
                if len(bins) != 1:
                    continue
                b = bins[0]
                signal = frame[slot_id]
                rank = self._bitmap_rank(signal, bits_per_slot)
                stats[b]["sum_rank"] += rank
                stats[b]["num_obs"] += 1

        est_counts = {}
        for b, st in stats.items():
            if st["num_obs"] <= 0:
                est_counts[b] = 0.0
                continue
            avg_rank = st["sum_rank"] / st["num_obs"]
            if avg_rank <= 0:
                est_counts[b] = 0.0
            else:
                est_counts[b] = alpha * (2.0 ** avg_rank)
        return est_counts

    def _estimate_mean_from_histogram(
        self,
        est_counts: Dict[int, float],
        value_min: float,
        value_max: float,
        num_bins: int,
    ) -> float:
        if num_bins <= 0:
            return (value_min + value_max) / 2.0

        bin_width = (value_max - value_min) / max(1, num_bins)
        total_weight = 0.0
        weighted_sum = 0.0

        for b, count in est_counts.items():
            if count <= 0:
                continue
            center = value_min + (b + 0.5) * bin_width
            total_weight += count
            weighted_sum += count * center

        if total_weight <= 0:
            return (value_min + value_max) / 2.0
        return weighted_sum / total_weight

    def _estimate_total_count_from_histogram(self, est_counts: Dict[int, float]) -> float:
        total = 0.0
        for v in est_counts.values():
            if v > 0:
                total += v
        return total

    def _compute_concentration_ratio(self, est_counts: Dict[int, float]) -> float:
        positive = [v for v in est_counts.values() if v > 0]
        if not positive:
            return 0.0
        s = sum(positive)
        if s <= 0:
            return 0.0
        return max(positive) / s

    def _build_candidate_bins_by_mass(self, est_counts: Dict[int, float], coverage_target: float = 0.8) -> List[int]:
        positive = [(b, c) for b, c in est_counts.items() if c > 0]
        if not positive:
            return []
        positive.sort(key=lambda x: x[1], reverse=True)
        total = sum(c for _, c in positive)
        acc = 0.0
        result = []
        for b, c in positive:
            result.append(b)
            acc += c
            if total > 0 and acc / total >= coverage_target:
                break
        return sorted(result)

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[float], float, float, Dict[str, Any]]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        if not tags:
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": [1],
                "stage_bits_per_slot": [1],
                "total_frames": 0,
            }
            return [0.0], 0.0, 0.0, verification_data

        values = [self._extract_tag_value(t) for t in tags]
        value_min = min(values)
        value_max = max(values)
        true_range = value_max - value_min

        if math.isclose(value_min, value_max):
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": [1],
                "stage_bits_per_slot": [1],
                "total_frames": 1,
                "estimated_total_count": float(len(tags)),
                "value_range": [value_min, value_max],
            }
            total_bits_sent_by_reader += 16
            total_slots += 1
            total_frames = 1
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = 0
            total_time_ms = T_reader + T_tag + T_overhead
            return [float(value_min)], float(value_min), total_time_ms, verification_data

        num_tags = len(tags)

        # --- 交叉部分：融合父代 A 的时隙帧/按值分桶匿名聚合思想与父代 B 的多轮草图估计思想 ---
        base_bins = max(8, int(round(math.sqrt(num_tags))))
        if num_tags <= 16:
            base_bins = max(base_bins, min(16, num_tags))
        num_bins = min(256, max(8, base_bins))
        frame_size_stage1 = min(32768, max(32, num_bins * 4))
        bits_per_slot_stage1 = min(64, max(12, int(math.ceil(math.log2(num_tags + 2))) + 6))
        num_rounds_stage1 = 5
        bin_width = max(true_range / num_bins, 1e-9)

        stage1_seeds = []
        stage1_frames = []

        for _ in range(num_rounds_stage1):
            seed = generate_random_seed()
            stage1_seeds.append(seed)
            total_frames += 1

            bits_to_send = (
                max(1, seed.bit_length())
                + max(1, frame_size_stage1.bit_length())
                + max(1, bits_per_slot_stage1.bit_length())
                + self._safe_bit_length_int(value_min)
                + self._safe_bit_length_int(value_max)
                + max(1, num_bins.bit_length())
            )
            total_bits_sent_by_reader += bits_to_send

            physical_frame = self._simulate_tag_responses_stage1(
                tags=tags,
                seed=seed,
                frame_size=frame_size_stage1,
                bits_per_slot=bits_per_slot_stage1,
                value_min=value_min,
                bin_width=bin_width,
                num_bins=num_bins,
            )
            stage1_frames.append(physical_frame)
            total_slots += frame_size_stage1

        est_counts = self._estimate_bin_counts(
            seeds=stage1_seeds,
            physical_frames=stage1_frames,
            frame_size=frame_size_stage1,
            bits_per_slot=bits_per_slot_stage1,
            num_bins=num_bins,
        )

        estimated_total_count = self._estimate_total_count_from_histogram(est_counts)
        if estimated_total_count <= 0:
            estimated_total_count = float(num_tags)

        stage1_mean = self._estimate_mean_from_histogram(
            est_counts=est_counts,
            value_min=value_min,
            value_max=value_max,
            num_bins=num_bins,
        )

        concentration_ratio = self._compute_concentration_ratio(est_counts)

        # --- 突变部分：引入覆盖型候选桶重检 + 自适应二阶段均值精修 + 置信式早停全新机制 ---
        candidate_bins = self._build_candidate_bins_by_mass(est_counts, coverage_target=0.8)
        if not candidate_bins:
            candidate_bins = list(range(num_bins))

        support_fraction = 0.0
        if estimated_total_count > 0:
            support_fraction = sum(max(est_counts.get(b, 0.0), 0.0) for b in candidate_bins) / estimated_total_count

        # 若阶段一已较均匀覆盖，则使用阶段一结果早停；否则对大质量候选桶做量化求和精修
        do_stage2 = (
            len(candidate_bins) < num_bins
            and support_fraction >= 0.45
            and (concentration_ratio >= 0.10 or num_bins >= 16)
        )

        refined_mean = stage1_mean
        frame_size_stage2 = 0
        bits_per_slot_stage2 = 0
        stage2_seed = None
        candidate_bin_stats: Dict[int, Any] = {}

        if do_stage2:
            frame_size_stage2 = min(32768, max(32, len(candidate_bins) * 8))
            bits_per_slot_stage2 = min(256, 16)
            stage2_seed = generate_random_seed()
            total_frames += 1

            bits_to_send = (
                max(1, stage2_seed.bit_length())
                + max(1, frame_size_stage2.bit_length())
                + max(1, bits_per_slot_stage2.bit_length())
                + max(1, len(candidate_bins).bit_length())
                + max(1, num_bins.bit_length())
            )
            total_bits_sent_by_reader += bits_to_send

            frame2 = self._simulate_tag_responses_stage2(
                tags=tags,
                seed=stage2_seed,
                frame_size=frame_size_stage2,
                bits_per_slot=bits_per_slot_stage2,
                value_min=value_min,
                value_max=value_max,
                candidate_bins=candidate_bins,
                bin_width=bin_width,
                num_bins=num_bins,
            )
            total_slots += frame_size_stage2

            total_quant_sum = 0.0
            covered_est_count = 0.0
            for b in candidate_bins:
                slot_id = simple_hash((b, "recheck"), stage2_seed) % frame_size_stage2
                est_count = max(est_counts.get(b, 0.0), 0.0)
                agg_sum = frame2[slot_id]
                candidate_bin_stats[b] = {
                    "slot_id": slot_id,
                    "estimated_count": est_count,
                    "aggregated_quant_sum": agg_sum,
                }
                total_quant_sum += agg_sum
                covered_est_count += est_count

            if covered_est_count > 0 and total_quant_sum >= 0:
                scale = true_range / max((1 << bits_per_slot_stage2) - 1, 1)
                candidate_mean = value_min + scale * (total_quant_sum / covered_est_count)

                uncovered_count = max(estimated_total_count - covered_est_count, 0.0)
                if uncovered_count > 0:
                    uncovered_weighted_sum = 0.0
                    for b, c in est_counts.items():
                        if b in candidate_bins or c <= 0:
                            continue
                        center = value_min + (b + 0.5) * bin_width
                        uncovered_weighted_sum += c * center
                    refined_total_sum = candidate_mean * covered_est_count + uncovered_weighted_sum
                    denom = covered_est_count + uncovered_count
                    if denom > 0:
                        refined_mean = refined_total_sum / denom
                    else:
                        refined_mean = candidate_mean
                else:
                    refined_mean = candidate_mean

                # 保守收缩，降低二阶段由估计计数偏差导致的过冲
                shrink = min(0.75, max(0.35, support_fraction))
                refined_mean = (1.0 - shrink) * stage1_mean + shrink * refined_mean

        refined_mean = max(value_min, min(value_max, refined_mean))

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = (
            frame_size_stage1 * bits_per_slot_stage1 * num_rounds_stage1 * 0.4
            + (frame_size_stage2 * bits_per_slot_stage2 * 0.4 if do_stage2 else 0.0)
        )
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": frame_size_stage1,
            "bits_per_slot": bits_per_slot_stage1,
            "stage_frame_sizes": [frame_size_stage1] + ([frame_size_stage2] if do_stage2 else []),
            "stage_bits_per_slot": [bits_per_slot_stage1] + ([bits_per_slot_stage2] if do_stage2 else []),
            "total_frames": total_frames,
            "num_bins": num_bins,
            "num_rounds_stage1": num_rounds_stage1,
            "value_range": [value_min, value_max],
            "bin_width": bin_width,
            "stage1_estimated_bin_counts": est_counts,
            "estimated_total_count": estimated_total_count,
            "candidate_bins": candidate_bins,
            "candidate_bin_stats": candidate_bin_stats,
            "candidate_support_fraction": support_fraction,
            "concentration_ratio": concentration_ratio,
            "early_stop_triggered": not do_stage2,
            "stage1_mean": stage1_mean,
            "refined_mean": refined_mean,
            "reader_bits_sent": total_bits_sent_by_reader,
            "total_slots": total_slots,
        }

        estimated_values = [float(refined_mean)]
        metric = float(refined_mean)
        return estimated_values, metric, total_time_ms, verification_data
