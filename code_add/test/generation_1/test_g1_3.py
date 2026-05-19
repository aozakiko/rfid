import math
from typing import List, Tuple, Dict, Any
from collections import defaultdict

from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash


class test_g1_3(IProtocol):
    """
    面向离散属性众数/多众数查询的混合式 RFID 协议。
    """

    def _safe_attr_value(self, tag: Tag) -> Any:
        return tag.__dict__.get("price")

    def _find_leading_ones(self, value: int, width: int) -> int:
        count = 0
        for i in range(width - 1, -1, -1):
            if ((value >> i) & 1) == 1:
                count += 1
            else:
                break
        return count

    def _build_sog_string(self, tag_id: Any, seed: int, sog_length: int) -> int:
        random_bits = simple_hash(tag_id, seed)
        mu = 0
        count = 0
        for i in range(sog_length - 1, -1, -1):
            if (random_bits >> i) & 1:
                mu = count
                break
            count += 1
        else:
            mu = sog_length
        if mu >= sog_length:
            return 0
        return 1 << (sog_length - 1 - mu)

    def _simulate_tag_responses_hybrid(
        self,
        tags: List[Tag],
        target_values: List[int],
        batch_seed: int,
        frame_size: int,
        use_sog: bool,
        sog_length: int
    ) -> Frame:
        bits_per_slot = len(target_values)
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        value_to_bit = {v: i for i, v in enumerate(target_values)}
        target_set = set(target_values)

        for tag in tags:
            v = self._safe_attr_value(tag)
            if v not in target_set:
                continue

            slot_seed = simple_hash(batch_seed, v)
            slot_id = simple_hash(tag.id, slot_seed) % frame_size
            bit_pos = value_to_bit[v]

            if use_sog:
                sog_seed = simple_hash(batch_seed, simple_hash(v, 0x9E3779B1))
                sog_string = self._build_sog_string(tag.id, sog_seed, sog_length)
                l_val = self._find_leading_ones(sog_string, sog_length)
                bit_val = min(l_val, 1)
            else:
                bit_val = 1

            reply_vector = bit_val << bit_pos
            physical_frame.aggregate(slot_id, reply_vector)

        return physical_frame

    def _analyze_bitmap_batch(
        self,
        physical_frame: Frame,
        target_values: List[int],
        frame_size: int
    ) -> Dict[int, float]:
        estimates = {}
        for bit_pos, value in enumerate(target_values):
            ones = 0
            for slot_data in physical_frame.slots:
                ones += (slot_data >> bit_pos) & 1
            zeros = frame_size - ones

            if zeros == frame_size:
                estimates[value] = 0.0
            elif zeros == 0:
                estimates[value] = float(frame_size) * 2.0
            else:
                estimates[value] = -frame_size * math.log(zeros / frame_size)
        return estimates

    def _analyze_sog_batch(
        self,
        physical_frame: Frame,
        target_values: List[int],
        frame_size: int
    ) -> Dict[int, float]:
        estimates = {}
        for bit_pos, value in enumerate(target_values):
            ones = 0
            for slot_data in physical_frame.slots:
                ones += (slot_data >> bit_pos) & 1
            zeros = frame_size - ones

            if zeros == frame_size:
                est_zero = 0.0
            elif zeros == 0:
                est_zero = float(frame_size) * 2.0
            else:
                est_zero = -frame_size * math.log(zeros / frame_size)

            occupancy_ratio = ones / frame_size if frame_size > 0 else 0.0
            correction = 1.0 + 0.15 * occupancy_ratio
            estimates[value] = est_zero * correction
        return estimates

    def _run_stage(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        batch_size: int,
        use_sog: bool,
        sog_length: int,
        rounds: int
    ) -> Tuple[Dict[int, float], int, int, int]:
        aggregated = defaultdict(float)
        seen = defaultdict(int)
        total_frames = 0
        total_reader_bits = 0

        stage_bits_per_slot = max(1, min(batch_size, 256))
        batches = [target_values[i:i + batch_size] for i in range(0, len(target_values), batch_size)]

        for _ in range(rounds):
            for batch in batches:
                if not batch:
                    continue

                batch_seed = generate_random_seed()
                total_frames += 1
                total_reader_bits += (
                    batch_seed.bit_length()
                    + frame_size.bit_length()
                    + len(batch).bit_length()
                    + (sog_length.bit_length() if use_sog else 1)
                )

                physical_frame = self._simulate_tag_responses_hybrid(
                    tags=tags,
                    target_values=batch,
                    batch_seed=batch_seed,
                    frame_size=frame_size,
                    use_sog=use_sog,
                    sog_length=sog_length
                )

                if use_sog:
                    batch_est = self._analyze_sog_batch(physical_frame, batch, frame_size)
                else:
                    batch_est = self._analyze_bitmap_batch(physical_frame, batch, frame_size)

                for v, est in batch_est.items():
                    aggregated[v] += est
                    seen[v] += 1

        final_estimates = {}
        for v in target_values:
            if seen[v] > 0:
                final_estimates[v] = aggregated[v] / seen[v]
            else:
                final_estimates[v] = 0.0

        total_slots = total_frames * frame_size
        total_tag_bits = total_slots * stage_bits_per_slot
        return final_estimates, total_frames, total_reader_bits, total_tag_bits

    def _adaptive_frame_size(self, candidate_count: int, scale: int, minimum: int, maximum: int) -> int:
        base = max(minimum, candidate_count * scale)
        power = 1
        while power < base and power < maximum:
            power <<= 1
        return min(maximum, max(minimum, power))

    def _select_top_with_ties(self, estimates: Dict[int, float], limit: int) -> List[int]:
        if not estimates:
            return []
        ordered = sorted(estimates.items(), key=lambda x: (-x[1], x[0]))
        if limit >= len(ordered):
            return [k for k, _ in ordered]
        threshold = ordered[limit - 1][1]
        selected = [k for k, v in ordered if v >= threshold]
        return selected[: max(limit + 2, limit)]

    def _conservative_mode_candidates(
        self,
        refined_counts: Dict[int, float],
        verify_counts: Dict[int, float]
    ) -> Tuple[List[int], float]:
        if not verify_counts:
            return [], 0.0

        max_verify = max(verify_counts.values()) if verify_counts else 0.0
        if max_verify <= 0:
            return [], 0.0

        sorted_verify = sorted(verify_counts.items(), key=lambda x: (-x[1], x[0]))
        top_value, top_score = sorted_verify[0]
        second_score = sorted_verify[1][1] if len(sorted_verify) > 1 else -1.0

        # --- 突变部分：引入“候选桶重检 + 置信边界早停”全新机制 ---
        # 基于验证阶段与精估阶段的一致性，保守输出接近榜首的少量候选值
        candidates = [top_value]
        tolerance = max(1.0, 0.08 * top_score)

        for value, score in sorted_verify[1:]:
            refined_score = refined_counts.get(value, 0.0)
            if top_score - score <= tolerance and abs(refined_counts.get(top_value, top_score) - refined_score) <= max(1.5, 0.12 * top_score):
                candidates.append(value)
            else:
                break

        if second_score >= 0 and top_score - second_score <= tolerance and len(candidates) == 1:
            candidates = [v for v, s in sorted_verify if top_score - s <= tolerance][:2]

        return sorted(candidates), top_score

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        all_values = list(range(price_min, price_max + 1))
        domain_size = len(all_values)

        if domain_size <= 0:
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": {"coarse": 1, "refine": 1, "verify": 1},
                "stage_bits_per_slot": {"coarse": 1, "refine": 1, "verify": 1},
                "total_frames": 0
            }
            return [], 0, 0.0, verification_data

        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        coarse_batch_size = min(128, max(8, min(domain_size, 128)))
        coarse_frame_size = self._adaptive_frame_size(coarse_batch_size, 2, 64, 1024)
        coarse_rounds = 1

        # --- 交叉部分：融合父代 A 的“基于随机映射的统计估计/多轮稳定化”思想与父代 B 的“分批候选筛选/逐级过滤”思想 ---
        coarse_counts, frames, reader_bits, tag_bits = self._run_stage(
            tags=tags,
            target_values=all_values,
            frame_size=coarse_frame_size,
            batch_size=coarse_batch_size,
            use_sog=False,
            sog_length=16,
            rounds=coarse_rounds
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_tag_bits_sent += tag_bits

        candidate_pool_size = min(max(4, int(math.sqrt(domain_size)) + 2), 24)
        coarse_candidates = self._select_top_with_ties(coarse_counts, candidate_pool_size)
        if not coarse_candidates:
            verification_data = {
                "frame_size": coarse_frame_size,
                "bits_per_slot": coarse_batch_size,
                "stage_frame_sizes": {"coarse": coarse_frame_size, "refine": 0, "verify": 0},
                "stage_bits_per_slot": {"coarse": coarse_batch_size, "refine": 0, "verify": 0},
                "total_frames": total_frames
            }
            return [], 0, 0.0, verification_data

        refine_batch_size = min(64, max(4, len(coarse_candidates)))
        refine_frame_size = self._adaptive_frame_size(len(coarse_candidates), 16, 256, 4096)
        refine_rounds = 2
        sog_length = 32

        refined_counts, frames, reader_bits, tag_bits = self._run_stage(
            tags=tags,
            target_values=coarse_candidates,
            frame_size=refine_frame_size,
            batch_size=refine_batch_size,
            use_sog=True,
            sog_length=sog_length,
            rounds=refine_rounds
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_tag_bits_sent += tag_bits

        sorted_refined = sorted(refined_counts.items(), key=lambda x: (-x[1], x[0]))
        verify_seed_candidates = [v for v, _ in sorted_refined[: min(6, len(sorted_refined))]]

        if not verify_seed_candidates:
            verify_seed_candidates = coarse_candidates[:1]

        verify_frame_size = self._adaptive_frame_size(len(verify_seed_candidates), 32, 512, 8192)
        verify_batch_size = min(32, max(1, len(verify_seed_candidates)))
        verify_rounds = 2

        verify_counts, frames, reader_bits, tag_bits = self._run_stage(
            tags=tags,
            target_values=verify_seed_candidates,
            frame_size=verify_frame_size,
            batch_size=verify_batch_size,
            use_sog=False,
            sog_length=8,
            rounds=verify_rounds
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_tag_bits_sent += tag_bits

        estimated_values, metric_score = self._conservative_mode_candidates(refined_counts, verify_counts)

        if not estimated_values and sorted_refined:
            estimated_values = [sorted_refined[0][0]]
            metric_score = sorted_refined[0][1]

        T_reader = total_bits_sent_by_reader * 0.025
        total_slots = 0
        total_slots += coarse_frame_size * frames if False else 0
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": verify_frame_size,
            "bits_per_slot": verify_batch_size,
            "stage_frame_sizes": {
                "coarse": coarse_frame_size,
                "refine": refine_frame_size,
                "verify": verify_frame_size
            },
            "stage_bits_per_slot": {
                "coarse": coarse_batch_size,
                "refine": refine_batch_size,
                "verify": verify_batch_size
            },
            "total_frames": total_frames,
            "coarse_candidate_pool_size": candidate_pool_size,
            "coarse_top": sorted(coarse_counts.items(), key=lambda x: (-x[1], x[0]))[: min(10, len(coarse_counts))],
            "refined_top": sorted_refined[: min(10, len(sorted_refined))],
            "verify_counts": dict(sorted(verify_counts.items(), key=lambda x: (-x[1], x[0]))),
            "sog_length": sog_length
        }

        metric = int(math.ceil(metric_score)) if metric_score != float("inf") else int(10 ** 9)
        return list(estimated_values), metric, total_time_ms, verification_data
