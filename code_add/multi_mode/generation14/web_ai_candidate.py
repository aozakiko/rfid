import math
from typing import List, Tuple, Dict, Any

from base import Tag, IProtocol
from hash import simple_hash


class web_ai_candidate(IProtocol):
    def __init__(self):
        self.max_frame_size = 32768
        self.max_bits_per_slot = 256

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        stage_names = []
        stage_frame_sizes = []
        stage_bits_per_slot = []
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0
        total_slots = 0

        all_prices = list(range(price_min, price_max + 1))
        if not all_prices:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 0,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "task_type": "discrete_mode_query"
            }
            return [], 0, 0.0, verification_data

        price_range_size = len(all_prices)

        existence_batch_size = min(192, price_range_size, self.max_bits_per_slot)
        existence_bits_per_slot = max(1, existence_batch_size)
        existence_frame_size = self._clamp_frame_size(self._next_power_of_two(max(64, existence_batch_size * 4)))

        candidate_check_batch_size = min(64, self.max_bits_per_slot)
        scoring_bits_per_slot = candidate_check_batch_size
        scoring_frame_size = self._clamp_frame_size(self._next_power_of_two(max(128, candidate_check_batch_size * 8)))

        recount_bits_per_slot = candidate_check_batch_size
        recount_frame_size = self._clamp_frame_size(self._next_power_of_two(max(256, candidate_check_batch_size * 12)))

        ee_frame_slots = 16
        ee_bits_per_slot = 1

        stage1_candidate_prices = set()
        existence_batches = [
            all_prices[i:i + existence_batch_size]
            for i in range(0, len(all_prices), existence_batch_size)
        ]

        for batch_index, batch in enumerate(existence_batches):
            if not batch:
                continue

            seed = self._make_seed(101, batch_index, price_min, price_max, len(tags))
            batch_result = self._simulate_tag_bitmap_frame(
                tags=tags,
                batch=batch,
                seed=seed,
                frame_size=existence_frame_size,
                bits_per_slot=existence_bits_per_slot
            )

            total_frames += 1
            total_slots += existence_frame_size
            total_tag_bits_sent += existence_frame_size * existence_bits_per_slot
            total_bits_sent_by_reader += self._reader_command_bits(
                seed=seed,
                frame_size=existence_frame_size,
                bits_per_slot=existence_bits_per_slot,
                batch_size=len(batch),
                opcode_bits=10
            )

            stage_names.append("existence_pruning")
            stage_frame_sizes.append(existence_frame_size)
            stage_bits_per_slot.append(existence_bits_per_slot)

            for local_idx, price in enumerate(batch):
                if self._logical_bit_has_any_response(batch_result["slots"], local_idx):
                    stage1_candidate_prices.add(price)

        if not stage1_candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * max(1, existence_bits_per_slot) * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            total_time_ms = T_reader + T_tag + T_overhead
            verification_data = {
                "frame_size": existence_frame_size,
                "bits_per_slot": existence_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "total_frames": total_frames,
                "ee_frame_slots": ee_frame_slots,
                "num_candidate_prices_stage1": 0,
                "num_candidate_prices_stage2": 0,
                "final_scores": {},
                "task_type": "discrete_mode_query"
            }
            return [], 0, total_time_ms, verification_data

        # --- 交叉部分：融合父代 A 的逐价精确验证思想与父代 B 的分批位图评分/存在性剪枝思想 ---
        candidate_price_list = sorted(stage1_candidate_prices)
        scoring_batches = [
            candidate_price_list[i:i + candidate_check_batch_size]
            for i in range(0, len(candidate_price_list), candidate_check_batch_size)
        ]

        scores_stage2 = {}
        occupancy_stage2 = {}
        collision_stage2 = {}

        for batch_index, batch in enumerate(scoring_batches):
            if not batch:
                continue

            seed = self._make_seed(211, batch_index, len(batch), price_range_size, len(stage1_candidate_prices))
            batch_result = self._simulate_tag_bitmap_frame(
                tags=tags,
                batch=batch,
                seed=seed,
                frame_size=scoring_frame_size,
                bits_per_slot=scoring_bits_per_slot
            )

            total_frames += 1
            total_slots += scoring_frame_size
            total_tag_bits_sent += scoring_frame_size * scoring_bits_per_slot
            total_bits_sent_by_reader += self._reader_command_bits(
                seed=seed,
                frame_size=scoring_frame_size,
                bits_per_slot=scoring_bits_per_slot,
                batch_size=len(batch),
                opcode_bits=12
            )

            stage_names.append("batched_scoring")
            stage_frame_sizes.append(scoring_frame_size)
            stage_bits_per_slot.append(scoring_bits_per_slot)

            for local_idx, price in enumerate(batch):
                slot_stats = self._logical_bit_slot_statistics(batch_result["slots"], local_idx)
                occupied = slot_stats["occupied_slots"]
                collided = slot_stats["collided_slots"]
                est_count = self._estimate_count_from_occupancy(
                    occupied_slots=occupied,
                    frame_size=scoring_frame_size
                )
                penalty = 0.35 * collided
                score = est_count - penalty
                scores_stage2[price] = score
                occupancy_stage2[price] = occupied
                collision_stage2[price] = collided

        if not scores_stage2:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * max(1, existence_bits_per_slot) * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            total_time_ms = T_reader + T_tag + T_overhead
            verification_data = {
                "frame_size": existence_frame_size,
                "bits_per_slot": existence_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "total_frames": total_frames,
                "ee_frame_slots": ee_frame_slots,
                "num_candidate_prices_stage1": len(stage1_candidate_prices),
                "num_candidate_prices_stage2": 0,
                "final_scores": {},
                "task_type": "discrete_mode_query"
            }
            return [], 0, total_time_ms, verification_data

        sorted_stage2 = sorted(scores_stage2.items(), key=lambda x: (-x[1], x[0]))
        top_score = sorted_stage2[0][1]

        # --- 突变部分：引入“自适应候选桶重检 + 早停式精确验证”全新机制 ---
        # 该机制不在两个父代中同时出现：先对高分候选进行更大帧重检以降低碰撞偏差，
        # 再仅对极少数最强候选执行父代A风格的逐价精确计数，并支持优势明显时提前终止。
        adaptive_margin = max(1.0, abs(top_score) * 0.12)
        preliminary_candidates = [
            p for p, s in sorted_stage2
            if s >= top_score - adaptive_margin
        ]
        preliminary_candidates = preliminary_candidates[:min(8, len(preliminary_candidates))]

        recount_scores = {}
        recount_occupancy = {}
        recount_collisions = {}

        recount_batches = [
            preliminary_candidates[i:i + candidate_check_batch_size]
            for i in range(0, len(preliminary_candidates), candidate_check_batch_size)
        ]

        for batch_index, batch in enumerate(recount_batches):
            if not batch:
                continue

            seed = self._make_seed(307, batch_index, len(batch), len(preliminary_candidates), price_min + price_max)
            batch_result = self._simulate_tag_bitmap_frame(
                tags=tags,
                batch=batch,
                seed=seed,
                frame_size=recount_frame_size,
                bits_per_slot=recount_bits_per_slot
            )

            total_frames += 1
            total_slots += recount_frame_size
            total_tag_bits_sent += recount_frame_size * recount_bits_per_slot
            total_bits_sent_by_reader += self._reader_command_bits(
                seed=seed,
                frame_size=recount_frame_size,
                bits_per_slot=recount_bits_per_slot,
                batch_size=len(batch),
                opcode_bits=13
            )

            stage_names.append("adaptive_recheck")
            stage_frame_sizes.append(recount_frame_size)
            stage_bits_per_slot.append(recount_bits_per_slot)

            for local_idx, price in enumerate(batch):
                slot_stats = self._logical_bit_slot_statistics(batch_result["slots"], local_idx)
                occupied = slot_stats["occupied_slots"]
                collided = slot_stats["collided_slots"]
                est_count = self._estimate_count_from_occupancy(
                    occupied_slots=occupied,
                    frame_size=recount_frame_size
                )
                penalty = 0.20 * collided
                score = est_count - penalty
                recount_scores[price] = score
                recount_occupancy[price] = occupied
                recount_collisions[price] = collided

        refined_source = recount_scores if recount_scores else scores_stage2
        refined_sorted = sorted(refined_source.items(), key=lambda x: (-x[1], x[0]))
        if not refined_sorted:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            total_time_ms = T_reader + T_tag + T_overhead
            verification_data = {
                "frame_size": existence_frame_size,
                "bits_per_slot": existence_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "total_frames": total_frames,
                "ee_frame_slots": ee_frame_slots,
                "num_candidate_prices_stage1": len(stage1_candidate_prices),
                "num_candidate_prices_stage2": len(scores_stage2),
                "final_scores": scores_stage2,
                "task_type": "discrete_mode_query"
            }
            return [], 0, total_time_ms, verification_data

        refined_top = refined_sorted[0][1]
        refined_margin = max(0.75, abs(refined_top) * 0.08)
        exact_candidates = [
            p for p, s in refined_sorted
            if s >= refined_top - refined_margin
        ]
        exact_candidates = exact_candidates[:min(4, len(exact_candidates))]

        exact_counts = {}
        exact_execution_trace = {}

        current_best_price = None
        current_best_count = -1

        remaining_candidates = list(exact_candidates)
        for idx, price in enumerate(remaining_candidates):
            count, extra_reader_bits, extra_slots, extra_frames, ee_rounds = self._exact_count_by_ee_simulation(
                tags=tags,
                target_price=price,
                ee_frame_slots=ee_frame_slots
            )

            total_bits_sent_by_reader += extra_reader_bits
            total_slots += extra_slots
            total_frames += extra_frames
            total_tag_bits_sent += extra_slots * ee_bits_per_slot

            exact_counts[price] = count
            exact_execution_trace[price] = {"ee_rounds": ee_rounds, "count": count}

            if count > current_best_count:
                current_best_count = count
                current_best_price = price

            remaining_unverified = len(remaining_candidates) - idx - 1
            theoretical_max_challengers = count + remaining_unverified
            if current_best_count > theoretical_max_challengers:
                break

        if exact_counts:
            max_exact = max(exact_counts.values())
            estimated_values = sorted([p for p, c in exact_counts.items() if c == max_exact])
            metric = max_exact
        else:
            fallback_top_score = refined_sorted[0][1]
            estimated_values = [refined_sorted[0][0]]
            metric = int(round(max(0.0, fallback_top_score)))

        T_reader = total_bits_sent_by_reader * 0.025
        dominant_bits_per_slot = max(stage_bits_per_slot) if stage_bits_per_slot else 1
        T_tag = total_slots * max(1, dominant_bits_per_slot if total_tag_bits_sent == 0 else 1)
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": existence_frame_size,
            "bits_per_slot": existence_bits_per_slot,
            "stage_names": stage_names,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "total_frames": total_frames,
            "ee_frame_slots": ee_frame_slots,
            "num_candidate_prices_stage1": len(stage1_candidate_prices),
            "num_candidate_prices_stage2": len(scores_stage2),
            "num_preliminary_candidates": len(preliminary_candidates),
            "num_exact_candidates": len(exact_candidates),
            "stage2_scores": scores_stage2,
            "stage2_occupied_slots": occupancy_stage2,
            "stage2_collided_slots": collision_stage2,
            "recheck_scores": recount_scores,
            "exact_counts": exact_counts,
            "exact_execution_trace": exact_execution_trace,
            "final_scores": refined_source,
            "task_type": "discrete_mode_query"
        }

        return estimated_values, metric, total_time_ms, verification_data

    def _simulate_tag_bitmap_frame(
        self,
        tags: List[Tag],
        batch: List[int],
        seed: int,
        frame_size: int,
        bits_per_slot: int
    ) -> Dict[str, Any]:
        slots = [0] * frame_size
        slot_population = [0] * frame_size
        price_to_bit_pos = {price: idx for idx, price in enumerate(batch)}
        batch_set = set(batch)

        for tag in tags:
            response = self._tag_response_bitmap(tag, batch_set, price_to_bit_pos, seed, frame_size)
            if response is None:
                continue
            slot_id, bit_vector = response
            slots[slot_id] |= bit_vector
            slot_population[slot_id] += 1

        return {
            "slots": slots,
            "slot_population": slot_population
        }

    def _tag_response_bitmap(
        self,
        tag: Tag,
        batch_set: set,
        price_to_bit_pos: Dict[int, int],
        seed: int,
        frame_size: int
    ):
        tag_price = getattr(tag, "price", None)
        if tag_price not in batch_set:
            return None
        slot_id = simple_hash(getattr(tag, "id"), seed) % frame_size
        bit_pos = price_to_bit_pos[tag_price]
        bit_vector = 1 << bit_pos
        return slot_id, bit_vector

    def _exact_count_by_ee_simulation(
        self,
        tags: List[Tag],
        target_price: int,
        ee_frame_slots: int
    ) -> Tuple[int, int, int, int, int]:
        current_ids = self._simulate_tag_collect_ids_for_price(tags, target_price)
        if not current_ids:
            reader_bits = self._reader_command_bits(seed=0, frame_size=ee_frame_slots, bits_per_slot=1, batch_size=1, opcode_bits=14)
            return 0, reader_bits, 0, 1, 0

        count = 0
        total_reader_bits = 0
        total_slots = 0
        total_frames = 0
        ee_rounds = 0

        while current_ids:
            l_bound = min(current_ids)
            h_bound = max(current_ids) + 1

            while (h_bound - l_bound) > 1:
                total_frames += 1
                total_slots += ee_frame_slots
                total_reader_bits += self._reader_command_bits(
                    seed=0,
                    frame_size=ee_frame_slots,
                    bits_per_slot=1,
                    batch_size=1,
                    opcode_bits=9
                )
                ee_rounds += 1

                d = (h_bound - l_bound) / ee_frame_slots
                if d <= 0:
                    break

                min_slot_idx_found = None
                for tag_id in current_ids:
                    if l_bound <= tag_id < h_bound:
                        slot_idx = int(math.floor((tag_id - l_bound) / d))
                        if slot_idx >= ee_frame_slots:
                            slot_idx = ee_frame_slots - 1
                        if min_slot_idx_found is None or slot_idx < min_slot_idx_found:
                            min_slot_idx_found = slot_idx

                if min_slot_idx_found is None:
                    break

                new_l = l_bound + math.ceil(d * min_slot_idx_found)
                new_h = l_bound + math.ceil(d * (min_slot_idx_found + 1))
                l_bound, h_bound = int(new_l), int(new_h)

            min_id = int(l_bound)
            if min_id in current_ids:
                count += 1
                current_ids.remove(min_id)
                total_frames += 1
                total_reader_bits += 16
            else:
                break

        return count, total_reader_bits, total_slots, total_frames, ee_rounds

    def _simulate_tag_collect_ids_for_price(self, tags: List[Tag], target_price: int) -> List[int]:
        matched = []
        for tag in tags:
            if getattr(tag, "price", None) == target_price:
                matched.append(getattr(tag, "id"))
        return matched

    def _logical_bit_has_any_response(self, slots: List[int], bit_pos: int) -> bool:
        mask = 1 << bit_pos
        for slot_data in slots:
            if slot_data & mask:
                return True
        return False

    def _logical_bit_slot_statistics(self, slots: List[int], bit_pos: int) -> Dict[str, int]:
        mask = 1 << bit_pos
        occupied_slots = 0
        collided_slots = 0

        for slot_data in slots:
            if slot_data & mask:
                occupied_slots += 1
                if self._popcount(slot_data) > 1:
                    collided_slots += 1

        return {
            "occupied_slots": occupied_slots,
            "collided_slots": collided_slots
        }

    def _estimate_count_from_occupancy(self, occupied_slots: int, frame_size: int) -> float:
        if occupied_slots <= 0:
            return 0.0
        if occupied_slots >= frame_size:
            return float(frame_size)
        ratio = 1.0 - (occupied_slots / float(frame_size))
        if ratio <= 0:
            return float(frame_size)
        return -frame_size * math.log(ratio)

    def _reader_command_bits(self, seed: int, frame_size: int, bits_per_slot: int, batch_size: int, opcode_bits: int) -> int:
        return (
            max(1, opcode_bits)
            + max(1, int(seed).bit_length())
            + max(1, int(frame_size).bit_length())
            + max(1, int(bits_per_slot).bit_length())
            + max(1, int(batch_size).bit_length())
        )

    def _make_seed(self, a: int, b: int, c: int, d: int, e: int) -> int:
        value = (
            (a * 1315423911)
            ^ (b * 2654435761)
            ^ (c * 97531)
            ^ (d * 314159)
            ^ (e * 271828)
        ) & 0x7FFFFFFF
        return value if value > 0 else 1

    def _next_power_of_two(self, x: int) -> int:
        if x <= 1:
            return 1
        return 1 << (x - 1).bit_length()

    def _clamp_frame_size(self, frame_size: int) -> int:
        return max(1, min(frame_size, self.max_frame_size))

    def _popcount(self, x: int) -> int:
        try:
            return x.bit_count()
        except AttributeError:
            return bin(x).count("1")
