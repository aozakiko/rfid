import math
from typing import List, Tuple, Dict, Any

from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash


class ui_real_connection_check(IProtocol):
    def __init__(self):
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self._slot_bit_volume = 0

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self._slot_bit_volume = 0

        COARSE_FRAME_SIZE = 256
        COARSE_BATCH_SIZE = 64
        FINE_BASE_FRAME_SIZE = 1024
        FINE_BATCH_SIZE = 16
        RECHECK_FRAME_SIZE = 2048
        CANDIDATE_POOL_SIZE = 12
        FINAL_CANDIDATE_SIZE = 4
        EARLY_STOP_MARGIN = 0.35
        COLLISION_RECHECK_THRESHOLD = 0.18

        all_prices = list(range(price_min, price_max + 1))
        if not all_prices:
            verification_data = {
                "frame_size": COARSE_FRAME_SIZE,
                "bits_per_slot": COARSE_BATCH_SIZE,
                "stage_frame_sizes": {"coarse": COARSE_FRAME_SIZE},
                "stage_bits_per_slot": {"coarse": COARSE_BATCH_SIZE},
                "total_frames": 0,
            }
            return [], 0, 0.0, verification_data

        # --- 交叉部分：融合父代 A 的“按范围/分桶粗筛”思想与父代 B 的“并行位图批量估计、逐级过滤”思想 ---
        coarse_counts, coarse_meta = self._run_batched_bitmap_estimation(
            tags=tags,
            target_values=all_prices,
            frame_size=COARSE_FRAME_SIZE,
            batch_size=COARSE_BATCH_SIZE,
            stage_name="coarse",
        )

        sorted_coarse = self._sort_estimates_desc(coarse_counts)
        candidate_prices = [v for v, c in sorted_coarse[:CANDIDATE_POOL_SIZE] if c > 0 or c == float("inf")]

        if not candidate_prices:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": COARSE_FRAME_SIZE,
                "bits_per_slot": COARSE_BATCH_SIZE,
                "stage_frame_sizes": {"coarse": COARSE_FRAME_SIZE},
                "stage_bits_per_slot": {"coarse": COARSE_BATCH_SIZE},
                "coarse_counts": sorted_coarse[: min(20, len(sorted_coarse))],
                "total_frames": self.total_frames,
            }
            return [], 0, total_time_ms, verification_data

        top1 = sorted_coarse[0][1] if sorted_coarse else 0
        top2 = sorted_coarse[1][1] if len(sorted_coarse) > 1 else 0

        stage_frame_sizes = {"coarse": COARSE_FRAME_SIZE}
        stage_bits_per_slot = {"coarse": COARSE_BATCH_SIZE}

        # --- 突变部分：引入“早停 + 候选桶重检 + 自适应精估帧长”全新机制 ---
        # 1) 若粗估已显示明显领先，则只保守保留少量候选，降低通信开销
        # 2) 对候选者使用碰撞监测进行重检
        # 3) 根据候选规模动态设置精估帧长
        if self._is_clear_leader(top1, top2, EARLY_STOP_MARGIN):
            candidate_prices = candidate_prices[: max(1, min(3, len(candidate_prices)))]
            fine_frame_size = min(32768, max(512, FINE_BASE_FRAME_SIZE // 2))
        else:
            fine_frame_size = min(32768, max(512, FINE_BASE_FRAME_SIZE + 64 * len(candidate_prices)))

        fine_counts, fine_meta = self._run_batched_bitmap_estimation(
            tags=tags,
            target_values=candidate_prices,
            frame_size=fine_frame_size,
            batch_size=FINE_BATCH_SIZE,
            stage_name="fine",
        )
        stage_frame_sizes["fine"] = fine_frame_size
        stage_bits_per_slot["fine"] = FINE_BATCH_SIZE

        sorted_fine = self._sort_estimates_desc(fine_counts)
        final_candidate_prices = [v for v, c in sorted_fine[:FINAL_CANDIDATE_SIZE] if c > 0 or c == float("inf")]

        if not final_candidate_prices:
            final_candidate_prices = [v for v, c in sorted_fine[:1]] if sorted_fine else []

        recheck_counts = {}
        recheck_collision_rates = {}
        if final_candidate_prices:
            recheck_counts, recheck_collision_rates = self._run_candidate_recheck(
                tags=tags,
                target_values=final_candidate_prices,
                frame_size=RECHECK_FRAME_SIZE,
            )
        stage_frame_sizes["recheck"] = RECHECK_FRAME_SIZE
        stage_bits_per_slot["recheck"] = min(256, len(final_candidate_prices) if final_candidate_prices else 1)

        merged_scores = {}
        for value in final_candidate_prices:
            fine_v = fine_counts.get(value, 0)
            recheck_v = recheck_counts.get(value, fine_v)
            collision_rate = recheck_collision_rates.get(value, 0.0)

            if fine_v == float("inf") and recheck_v == float("inf"):
                merged = float("inf")
            elif fine_v == float("inf"):
                merged = recheck_v
            elif recheck_v == float("inf"):
                merged = fine_v
            else:
                merged = 0.4 * fine_v + 0.6 * recheck_v

            if collision_rate > COLLISION_RECHECK_THRESHOLD and merged != float("inf"):
                merged *= 0.98

            merged_scores[value] = merged

        estimated_values, metric = self._select_conservative_modes(merged_scores)

        total_time_ms = self._calculate_total_time()
        verification_data = {
            "frame_size": RECHECK_FRAME_SIZE,
            "bits_per_slot": min(256, max(COARSE_BATCH_SIZE, FINE_BATCH_SIZE, len(final_candidate_prices) if final_candidate_prices else 1)),
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "coarse_counts": sorted_coarse[: min(20, len(sorted_coarse))],
            "fine_counts": sorted_fine[: min(10, len(sorted_fine))],
            "recheck_counts": self._sort_estimates_desc(recheck_counts) if recheck_counts else [],
            "recheck_collision_rates": recheck_collision_rates,
            "selected_candidates": estimated_values,
            "total_frames": self.total_frames,
            "coarse_batches": coarse_meta.get("batches", 0),
            "fine_batches": fine_meta.get("batches", 0),
        }

        return estimated_values, metric, total_time_ms, verification_data

    def _run_batched_bitmap_estimation(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        batch_size: int,
        stage_name: str,
    ) -> Tuple[Dict[int, float], Dict[str, Any]]:
        estimated_counts: Dict[int, float] = {}
        batches = [target_values[i:i + batch_size] for i in range(0, len(target_values), batch_size)]

        for batch in batches:
            if not batch:
                continue

            bits_per_slot = min(256, len(batch))
            seed = generate_random_seed()
            self.total_frames += 1
            self.total_bits_sent_by_reader += (
                seed.bit_length()
                + frame_size.bit_length()
                + bits_per_slot.bit_length()
                + len(batch).bit_length()
            )

            physical_frame = self._simulate_tag_response_bitmap_frame(
                tags=tags,
                candidate_values=batch,
                seed=seed,
                frame_size=frame_size,
                bits_per_slot=bits_per_slot,
            )

            self.total_slots += frame_size
            self._slot_bit_volume += frame_size * bits_per_slot

            value_to_bit = {value: idx for idx, value in enumerate(batch)}

            for value, bit_pos in value_to_bit.items():
                ones = 0
                collisions = 0
                for slot_data in physical_frame.slots:
                    bit = (slot_data >> bit_pos) & 1
                    if bit:
                        ones += 1
                    if self._slot_has_collision(slot_data):
                        collisions += 1

                zeros = frame_size - ones
                if zeros == frame_size:
                    estimated_counts[value] = 0
                elif zeros == 0:
                    estimated_counts[value] = float("inf")
                else:
                    estimated_counts[value] = -frame_size * math.log(zeros / frame_size)

        return estimated_counts, {"batches": len(batches), "stage": stage_name}

    def _run_candidate_recheck(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
    ) -> Tuple[Dict[int, float], Dict[int, float]]:
        if not target_values:
            return {}, {}

        bits_per_slot = min(256, len(target_values))
        seed = generate_random_seed()
        self.total_frames += 1
        self.total_bits_sent_by_reader += (
            seed.bit_length()
            + frame_size.bit_length()
            + bits_per_slot.bit_length()
            + len(target_values).bit_length()
        )

        physical_frame = self._simulate_tag_response_bitmap_frame(
            tags=tags,
            candidate_values=target_values,
            seed=seed,
            frame_size=frame_size,
            bits_per_slot=bits_per_slot,
        )

        self.total_slots += frame_size
        self._slot_bit_volume += frame_size * bits_per_slot

        estimates: Dict[int, float] = {}
        collision_rates: Dict[int, float] = {}
        value_to_bit = {value: idx for idx, value in enumerate(target_values)}

        for value, bit_pos in value_to_bit.items():
            occupied = 0
            collision_slots = 0
            for slot_data in physical_frame.slots:
                bit = (slot_data >> bit_pos) & 1
                if bit:
                    occupied += 1
                    if self._slot_has_collision(slot_data):
                        collision_slots += 1

            zeros = frame_size - occupied
            collision_rates[value] = collision_slots / frame_size if frame_size > 0 else 0.0

            if zeros == frame_size:
                estimates[value] = 0
            elif zeros == 0:
                estimates[value] = float("inf")
            else:
                base_est = -frame_size * math.log(zeros / frame_size)
                correction = 1.0 + 0.15 * collision_rates[value]
                estimates[value] = base_est * correction

        return estimates, collision_rates

    def _simulate_tag_response_bitmap_frame(
        self,
        tags: List[Tag],
        candidate_values: List[int],
        seed: int,
        frame_size: int,
        bits_per_slot: int,
    ) -> Frame:
        frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        value_to_bit = {value: idx for idx, value in enumerate(candidate_values)}
        candidate_set = set(candidate_values)

        for tag in tags:
            value = self._tag_response_get_price_if_in_candidate_set(tag, candidate_set)
            if value is None:
                continue
            bit_pos = value_to_bit.get(value)
            if bit_pos is None or bit_pos >= bits_per_slot:
                continue
            slot_id = simple_hash(tag.id, seed) % frame_size
            frame.aggregate(slot_id, 1 << bit_pos)
        return frame

    def _tag_response_get_price_if_in_candidate_set(self, tag: Tag, candidate_set: set):
        tag_value = getattr(tag, "price", None)
        if tag_value in candidate_set:
            return tag_value
        return None

    def _slot_has_collision(self, slot_data: int) -> bool:
        return slot_data != 0 and (slot_data & (slot_data - 1)) != 0

    def _sort_estimates_desc(self, estimates: Dict[int, float]) -> List[Tuple[int, float]]:
        return sorted(
            estimates.items(),
            key=lambda item: (item[1] == float("inf"), item[1]),
            reverse=True,
        )

    def _is_clear_leader(self, top1: float, top2: float, margin: float) -> bool:
        if top1 == float("inf") and top2 != float("inf"):
            return True
        if top1 in (0, None):
            return False
        if top2 == float("inf"):
            return False
        if top1 == float("inf") and top2 == float("inf"):
            return False
        return (top1 - top2) / max(1.0, top1) >= margin

    def _select_conservative_modes(self, merged_scores: Dict[int, float]) -> Tuple[List[int], int]:
        if not merged_scores:
            return [], 0

        sorted_items = self._sort_estimates_desc(merged_scores)
        top_value, top_score = sorted_items[0]

        if top_score == float("inf"):
            inf_values = [v for v, s in sorted_items if s == float("inf")]
            return sorted(inf_values[: min(3, len(inf_values))]), 10**9

        selected = [top_value]
        tolerance = max(0.5, top_score * 0.08)

        for value, score in sorted_items[1:]:
            if len(selected) >= 3:
                break
            if abs(score - top_score) <= tolerance and score > 0:
                selected.append(value)
            else:
                break

        metric = max(0, int(round(top_score)))
        return sorted(selected), metric

    def _calculate_total_time(self) -> float:
        T_reader = self.total_bits_sent_by_reader * 0.025
        avg_bits_per_slot = (self._slot_bit_volume / self.total_slots) if self.total_slots > 0 else 1
        T_tag = self.total_slots * avg_bits_per_slot * 0.4
        T_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0
        return T_reader + T_tag + T_overhead
