from typing import List, Tuple, Dict, Any
from collections import defaultdict

from base import Tag, IProtocol, generate_random_seed, simple_hash


class web_mode_query_iter_03(IProtocol):
    """
    面向离散属性多众数查询的可扩展 RFID 协议。
    """

    def __init__(self) -> None:
        self.max_frame_size = 32768
        self.max_bits_per_slot = 256
        self.stage1_default_bits_per_slot = 1
        self.stage2_default_bits_per_slot = 32
        self.recheck_bits_per_slot = 1
        self.min_frame_size = 32
        self.target_load = 0.7

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        stage_frame_sizes = []
        stage_bits_per_slot = []
        stage_names = []

        candidate_scores = {}
        exact_counts = {}
        stage1_non_empty = {}
        stage1_collision = {}
        recheck_scores = {}
        candidate_rank_trace = []

        candidate_prices = list(range(price_min, price_max + 1))
        if not candidate_prices:
            verification_data = {
                "frame_size": self.min_frame_size,
                "bits_per_slot": self.stage1_default_bits_per_slot,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "method": "two_stage_mode_query_with_recheck"
            }
            return [], 0, 0.0, verification_data

        # --- 交叉部分：融合父代 A 的逐值匿名时隙探测思想与父代 B 的分批精确验证思想 ---
        stage1_frame_size = self._choose_stage1_frame_size(len(tags), len(candidate_prices))
        stage1_bits_per_slot = self._safe_bits_per_slot(self.stage1_default_bits_per_slot)

        stage_frame_sizes.append(stage1_frame_size)
        stage_bits_per_slot.append(stage1_bits_per_slot)
        stage_names.append("stage1_screening")

        for p in candidate_prices:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += self._command_bits_for_probe(p, stage1_frame_size, stage1_bits_per_slot, seed)
            total_slots += stage1_frame_size

            slot_states = self._simulate_tag_response_probe(tags, p, stage1_frame_size, seed)
            non_empty = sum(1 for x in slot_states if x > 0)
            collisions = sum(1 for x in slot_states if x > 1)
            successes = sum(1 for x in slot_states if x == 1)

            stage1_non_empty[p] = non_empty
            stage1_collision[p] = collisions

            estimated_count = self._estimate_count_from_slots(non_empty, collisions, stage1_frame_size)
            score = estimated_count + 0.15 * collisions + 0.02 * successes
            candidate_scores[p] = score

        ranked_candidates = sorted(candidate_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        candidate_rank_trace = ranked_candidates[: min(10, len(ranked_candidates))]

        if not ranked_candidates:
            total_time_ms = self._compute_total_time(total_bits_sent_by_reader, total_slots, total_frames, stage1_bits_per_slot)
            verification_data = {
                "frame_size": stage1_frame_size,
                "bits_per_slot": stage1_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "total_frames": total_frames,
                "stage1_scores": candidate_scores,
                "method": "two_stage_mode_query_with_recheck"
            }
            return [], 0, total_time_ms, verification_data

        # --- 突变部分：引入动态候选收缩 + 候选桶重检 + 早停的全新机制 ---
        top_score = ranked_candidates[0][1]
        screening_threshold = max(top_score * 0.88, top_score - 1.0)
        screened = [p for p, s in ranked_candidates if s >= screening_threshold]
        max_candidates_for_verify = self._cap_candidate_count(len(candidate_prices))
        screened = screened[:max_candidates_for_verify]

        rechecked_candidates = []
        if len(screened) > 1:
            recheck_frame_size = self._choose_recheck_frame_size(stage1_frame_size, len(tags))
            recheck_bits = self._safe_bits_per_slot(self.recheck_bits_per_slot)
            stage_frame_sizes.append(recheck_frame_size)
            stage_bits_per_slot.append(recheck_bits)
            stage_names.append("stage1b_recheck")

            for p in screened:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += self._command_bits_for_probe(p, recheck_frame_size, recheck_bits, seed)
                total_slots += recheck_frame_size

                slot_states = self._simulate_tag_response_probe(tags, p, recheck_frame_size, seed)
                non_empty = sum(1 for x in slot_states if x > 0)
                collisions = sum(1 for x in slot_states if x > 1)
                est = self._estimate_count_from_slots(non_empty, collisions, recheck_frame_size)
                recheck_scores[p] = est + 0.1 * collisions

            rechecked_rank = sorted(recheck_scores.items(), key=lambda kv: (-kv[1], kv[0]))
            if rechecked_rank:
                best_recheck = rechecked_rank[0][1]
                recheck_threshold = max(best_recheck * 0.93, best_recheck - 0.75)
                rechecked_candidates = [p for p, s in rechecked_rank if s >= recheck_threshold]
            else:
                rechecked_candidates = screened[:]
        else:
            rechecked_candidates = screened[:]

        if not rechecked_candidates:
            rechecked_candidates = [ranked_candidates[0][0]]

        if len(rechecked_candidates) > self._cap_final_verify_count():
            rechecked_candidates = rechecked_candidates[: self._cap_final_verify_count()]

        # 父代B基因：仅对少量候选进行精确验证，而非全量读取
        stage2_bits_per_slot = self._safe_bits_per_slot(self.stage2_default_bits_per_slot)
        stage2_frame_size = max(1, len(rechecked_candidates))
        stage_frame_sizes.append(stage2_frame_size)
        stage_bits_per_slot.append(stage2_bits_per_slot)
        stage_names.append("stage2_exact_verify")

        for p in rechecked_candidates:
            total_frames += 1
            total_bits_sent_by_reader += self._command_bits_for_exact_verify(p, stage2_bits_per_slot)
            exact_count, tag_replies = self._simulate_tag_response_exact_count(tags, p)
            exact_counts[p] = exact_count
            total_slots += max(1, tag_replies)

        if exact_counts:
            best_exact = max(exact_counts.values())
            estimated_values = sorted([p for p, c in exact_counts.items() if c == best_exact])
            metric = best_exact
        else:
            fallback_best = ranked_candidates[0][0]
            estimated_values = [fallback_best]
            metric = int(round(candidate_scores.get(fallback_best, 0)))

        bits_per_slot_for_time = max(stage_bits_per_slot) if stage_bits_per_slot else 1
        total_time_ms = self._compute_total_time(
            total_bits_sent_by_reader,
            total_slots,
            total_frames,
            bits_per_slot_for_time
        )

        verification_data = {
            "frame_size": stage1_frame_size,
            "bits_per_slot": stage1_bits_per_slot,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "stage_names": stage_names,
            "total_frames": total_frames,
            "stage1_scores": candidate_scores,
            "stage1_non_empty_slots": stage1_non_empty,
            "stage1_collision_slots": stage1_collision,
            "recheck_scores": recheck_scores,
            "final_exact_counts": exact_counts,
            "candidate_rank_trace": candidate_rank_trace,
            "screened_candidates": screened,
            "rechecked_candidates": rechecked_candidates,
            "method": "hybrid_frame_screening_plus_candidate_exact_verify"
        }

        return estimated_values, metric, total_time_ms, verification_data

    def _choose_stage1_frame_size(self, num_tags: int, num_candidates: int) -> int:
        base = max(self.min_frame_size, min(self.max_frame_size, self._next_power_of_two(max(8, num_tags))))
        if num_candidates <= 8:
            base = min(self.max_frame_size, max(base, 128))
        elif num_candidates <= 32:
            base = min(self.max_frame_size, max(base, 64))
        else:
            base = min(self.max_frame_size, max(base // 2, 64))
        return max(1, min(self.max_frame_size, base))

    def _choose_recheck_frame_size(self, prev_frame_size: int, num_tags: int) -> int:
        enlarged = min(self.max_frame_size, max(prev_frame_size * 2, self._next_power_of_two(max(16, int(num_tags * 1.2)))))
        return max(1, enlarged)

    def _cap_candidate_count(self, num_candidates: int) -> int:
        if num_candidates <= 8:
            return num_candidates
        if num_candidates <= 32:
            return 4
        if num_candidates <= 128:
            return 3
        return 2

    def _cap_final_verify_count(self) -> int:
        return 4

    def _safe_bits_per_slot(self, bits: int) -> int:
        return max(1, min(self.max_bits_per_slot, bits))

    def _next_power_of_two(self, x: int) -> int:
        v = 1
        while v < x:
            v <<= 1
        return v

    def _estimate_count_from_slots(self, non_empty: int, collisions: int, frame_size: int) -> float:
        if frame_size <= 0:
            return 0.0
        empty = max(0, frame_size - non_empty)
        if empty == 0:
            return float(frame_size + collisions)
        ratio = empty / float(frame_size)
        if ratio <= 0:
            return float(frame_size + collisions)
        loa_est = -frame_size * self._safe_log(ratio)
        return max(0.0, loa_est + 0.5 * collisions)

    def _safe_log(self, x: float) -> float:
        import math
        if x <= 0:
            return 50.0
        return math.log(x)

    def _command_bits_for_probe(self, price: int, frame_size: int, bits_per_slot: int, seed: int) -> int:
        return 16 + max(1, price.bit_length()) + max(1, frame_size.bit_length()) + max(1, bits_per_slot.bit_length()) + max(1, seed.bit_length())

    def _command_bits_for_exact_verify(self, price: int, bits_per_slot: int) -> int:
        return 48 + max(1, price.bit_length()) + max(1, bits_per_slot.bit_length())

    def _compute_total_time(self, total_bits_sent_by_reader: int, total_slots: int, total_frames: int, bits_per_slot: int) -> float:
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        return T_reader + T_tag + T_overhead

    def _simulate_tag_response_probe(self, tags: List[Tag], query_price: int, frame_size: int, seed: int) -> List[int]:
        slot_states = [0] * frame_size
        for t in tags:
            if self._tag_response_match_price(t, query_price):
                slot_id = simple_hash(t.id, seed) % frame_size
                slot_states[slot_id] += 1
        return slot_states

    def _simulate_tag_response_exact_count(self, tags: List[Tag], query_price: int) -> Tuple[int, int]:
        count = 0
        replies = 0
        for t in tags:
            matched = self._tag_response_match_price(t, query_price)
            if matched:
                count += 1
                replies += 1
        return count, replies

    def _tag_response_match_price(self, tag: Tag, query_price: int) -> bool:
        return getattr(tag, "price", None) == query_price
