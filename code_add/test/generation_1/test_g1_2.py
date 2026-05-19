import math
from typing import List, Tuple, Dict, Any

try:
    from base import Tag, IProtocol, generate_random_seed, Frame
except Exception:
    class Tag:
        def __init__(self, id=None, **kwargs):
            self.id = id
            self.__dict__.update(kwargs)

    class IProtocol:
        pass

    def generate_random_seed():
        import random
        return random.randint(1, 2**31 - 1)

    class Frame:
        def __init__(self, num_slots: int, bits_per_slot: int):
            self.num_slots = num_slots
            self.bits_per_slot = bits_per_slot
            self.slots = [0 for _ in range(num_slots)]

        def aggregate(self, slot_id: int, reply_vector: int):
            self.slots[slot_id] |= reply_vector

try:
    from hash import simple_hash
except Exception:
    try:
        from base import simple_hash
    except Exception:
        def simple_hash(value, seed):
            return hash((value, seed)) & 0x7fffffff


class test_g1_2(IProtocol):
    def _get_attr_value(self, tag: Tag) -> Any:
        return tag.__dict__.get("price")

    def _tag_response_batch(self, tag: Tag, batch_price_set: set, price_to_bit_pos_map: Dict[int, int], seed: int, frame_size: int):
        v = self._get_attr_value(tag)
        if v in batch_price_set:
            slot_id = simple_hash(tag.id, seed) % frame_size
            bit_pos = price_to_bit_pos_map[v]
            return slot_id, (1 << bit_pos)
        return None

    def _tag_response_single(self, tag: Tag, target_value: int, seed: int, frame_size: int):
        v = self._get_attr_value(tag)
        if v == target_value:
            slot_id = simple_hash(tag.id, seed) % frame_size
            return slot_id, 1
        return None

    def _simulate_batch_frame(self, tags: List[Tag], batch: List[int], frame_size: int, seed: int):
        num_prices_in_batch = len(batch)
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=num_prices_in_batch)
        price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
        batch_price_set = set(batch)
        for t in tags:
            resp = self._tag_response_batch(t, batch_price_set, price_to_bit_pos_map, seed, frame_size)
            if resp is not None:
                slot_id, reply_vector = resp
                physical_frame.aggregate(slot_id, reply_vector)
        return physical_frame, price_to_bit_pos_map

    def _simulate_single_frame(self, tags: List[Tag], target_value: int, frame_size: int, seed: int):
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=1)
        for t in tags:
            resp = self._tag_response_single(t, target_value, seed, frame_size)
            if resp is not None:
                slot_id, reply_vector = resp
                physical_frame.aggregate(slot_id, reply_vector)
        return physical_frame

    def _estimate_from_slot_bits(self, slot_bits: List[int], frame_size: int):
        occupied = sum(slot_bits)
        zeros = frame_size - occupied
        if zeros == frame_size:
            return 0.0
        if zeros == 0:
            return float("inf")
        return -frame_size * math.log(zeros / frame_size)

    def _run_parallel_estimation_phase(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], Dict[str, int], Dict[int, int]]:
        estimated_counts: Dict[int, float] = {}
        saturation_flags: Dict[int, int] = {}
        frames_used = 0
        reader_bits = 0
        total_slots = 0
        bits_per_slot = max(1, min(batch_size, 256))

        value_batches = [target_values[i:i + batch_size] for i in range(0, len(target_values), batch_size)]
        for batch in value_batches:
            if not batch:
                continue
            seed = generate_random_seed()
            frames_used += 1
            reader_bits += seed.bit_length() + frame_size.bit_length() + len(batch).bit_length()
            total_slots += frame_size

            physical_frame, price_to_bit_pos_map = self._simulate_batch_frame(tags, batch, frame_size, seed)

            for value, bit_pos in price_to_bit_pos_map.items():
                logical_bits = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                occupied = sum(logical_bits)
                zeros = frame_size - occupied
                saturation_flags[value] = 1 if zeros == 0 else 0
                estimated_counts[value] = self._estimate_from_slot_bits(logical_bits, frame_size)

        metrics = {
            "frames_used": frames_used,
            "reader_bits": reader_bits,
            "total_slots": total_slots,
            "bits_per_slot": bits_per_slot,
        }
        return estimated_counts, metrics, saturation_flags

    def _run_single_value_estimation(
        self,
        tags: List[Tag],
        target_value: int,
        frame_size: int
    ) -> Tuple[float, Dict[str, int], int]:
        seed = generate_random_seed()
        reader_bits = seed.bit_length() + frame_size.bit_length() + max(1, int(target_value)).bit_length() if isinstance(target_value, int) and target_value > 0 else seed.bit_length() + frame_size.bit_length() + 1
        physical_frame = self._simulate_single_frame(tags, target_value, frame_size, seed)
        zeros = physical_frame.slots.count(0)
        occupied = frame_size - zeros
        if zeros == frame_size:
            est = 0.0
        elif zeros == 0:
            est = float("inf")
        else:
            est = -frame_size * math.log(zeros / frame_size)
        metrics = {
            "frames_used": 1,
            "reader_bits": reader_bits,
            "total_slots": frame_size,
            "bits_per_slot": 1,
        }
        return est, metrics, occupied

    def _sort_items_desc(self, d: Dict[int, float]):
        return sorted(d.items(), key=lambda kv: (kv[1] == float("inf"), kv[1]), reverse=True)

    def _adaptive_frame_size(self, candidate_count: int, stage: str) -> int:
        if stage == "coarse":
            base = 256
            scaled = max(base, min(2048, candidate_count * 4))
            return min(32768, scaled)
        if stage == "fine":
            base = 1024
            scaled = max(base, min(8192, candidate_count * 64))
            return min(32768, scaled)
        base = 4096
        scaled = max(base, min(16384, candidate_count * 512))
        return min(32768, scaled)

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        all_values = list(range(price_min, price_max + 1))
        if not all_values:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 0,
                "stage_frame_sizes": {},
                "stage_bits_per_slot": {},
                "total_frames": 0,
            }
            return [], 0, 0.0, verification_data

        COARSE_BATCH_SIZE = min(128, max(16, len(all_values)))
        COARSE_FRAME_SIZE = self._adaptive_frame_size(len(all_values), "coarse")
        FINE_BATCH_SIZE = 32
        CANDIDATE_POOL_SIZE = min(12, max(4, int(math.sqrt(max(1, len(all_values))))))
        FINAL_POOL_SIZE = min(4, CANDIDATE_POOL_SIZE)
        RECHECK_MARGIN_RATIO = 0.88

        # --- 交叉部分：融合父代 A 的并行批量位图估算思想与父代 B 的逐级候选筛选/精估思想 ---
        coarse_counts, coarse_metrics, coarse_saturation = self._run_parallel_estimation_phase(
            tags, all_values, COARSE_FRAME_SIZE, COARSE_BATCH_SIZE
        )
        total_frames += coarse_metrics["frames_used"]
        total_bits_sent_by_reader += coarse_metrics["reader_bits"]
        total_slots += coarse_metrics["total_slots"]

        sorted_coarse = self._sort_items_desc(coarse_counts)
        candidate_values = [v for v, c in sorted_coarse[:CANDIDATE_POOL_SIZE] if c > 0 or c == float("inf")]
        if not candidate_values and sorted_coarse:
            candidate_values = [sorted_coarse[0][0]]

        if not candidate_values:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            total_time_ms = T_reader + T_tag + T_overhead
            verification_data = {
                "frame_size": COARSE_FRAME_SIZE,
                "bits_per_slot": min(COARSE_BATCH_SIZE, 256),
                "stage_frame_sizes": {"coarse": COARSE_FRAME_SIZE},
                "stage_bits_per_slot": {"coarse": min(COARSE_BATCH_SIZE, 256)},
                "total_frames": total_frames,
                "coarse_counts": sorted_coarse[:CANDIDATE_POOL_SIZE],
            }
            return [], 0, total_time_ms, verification_data

        FINE_FRAME_SIZE = self._adaptive_frame_size(len(candidate_values), "fine")
        fine_counts, fine_metrics, fine_saturation = self._run_parallel_estimation_phase(
            tags, candidate_values, FINE_FRAME_SIZE, min(FINE_BATCH_SIZE, 256)
        )
        total_frames += fine_metrics["frames_used"]
        total_bits_sent_by_reader += fine_metrics["reader_bits"]
        total_slots += fine_metrics["total_slots"]

        sorted_fine = self._sort_items_desc(fine_counts)
        preliminary_final = [v for v, c in sorted_fine[:FINAL_POOL_SIZE]]

        # --- 突变部分：引入“候选桶重检 + 自适应单值复核 + 保守多众数阈值”全新机制 ---
        if sorted_fine:
            best_fine = sorted_fine[0][1]
            recheck_candidates = []
            for v, c in sorted_fine:
                if len(recheck_candidates) >= max(FINAL_POOL_SIZE + 2, 3):
                    break
                if best_fine == float("inf"):
                    if c == float("inf"):
                        recheck_candidates.append(v)
                else:
                    if c == float("inf") or c >= best_fine * RECHECK_MARGIN_RATIO:
                        recheck_candidates.append(v)
            if recheck_candidates:
                preliminary_final = recheck_candidates

        single_frame_size = self._adaptive_frame_size(len(preliminary_final), "accurate")
        final_counts: Dict[int, float] = {}
        occupied_map: Dict[int, int] = {}
        for v in preliminary_final:
            est, m, occupied = self._run_single_value_estimation(tags, v, single_frame_size)
            total_frames += m["frames_used"]
            total_bits_sent_by_reader += m["reader_bits"]
            total_slots += m["total_slots"]
            final_counts[v] = est
            occupied_map[v] = occupied

        if not final_counts:
            final_counts = {sorted_fine[0][0]: sorted_fine[0][1]} if sorted_fine else {}
            single_frame_size = FINE_FRAME_SIZE

        estimated_values: List[int] = []
        metric = 0

        if final_counts:
            inf_values = sorted([v for v, c in final_counts.items() if c == float("inf")])
            if inf_values:
                estimated_values = inf_values[:max(1, min(3, len(inf_values)))]
                metric = float("inf")
            else:
                best = max(final_counts.values())
                second = None
                ordered = self._sort_items_desc(final_counts)
                if len(ordered) > 1:
                    second = ordered[1][1]

                confidence_gap = 1.0
                if second is not None and best > 0:
                    confidence_gap = (best - second) / max(best, 1e-9)

                if confidence_gap < 0.08:
                    threshold = best * 0.93
                elif confidence_gap < 0.18:
                    threshold = best * 0.96
                else:
                    threshold = best * 0.995

                estimated_values = sorted([v for v, c in final_counts.items() if c >= threshold and c > 0])

                if not estimated_values and ordered:
                    estimated_values = [ordered[0][0]]

                estimated_values = estimated_values[:3]
                metric = int(math.ceil(best)) if best != float("inf") else float("inf")

        bits_per_slot_for_time = max(
            1,
            min(256, COARSE_BATCH_SIZE),
            min(256, FINE_BATCH_SIZE),
            1
        )
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot_for_time * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": single_frame_size,
            "bits_per_slot": bits_per_slot_for_time,
            "stage_frame_sizes": {
                "coarse": COARSE_FRAME_SIZE,
                "fine": FINE_FRAME_SIZE,
                "accurate": single_frame_size,
            },
            "stage_bits_per_slot": {
                "coarse": min(COARSE_BATCH_SIZE, 256),
                "fine": min(FINE_BATCH_SIZE, 256),
                "accurate": 1,
            },
            "total_frames": total_frames,
            "coarse_candidate_pool_size": CANDIDATE_POOL_SIZE,
            "final_pool_size": FINAL_POOL_SIZE,
            "coarse_counts": sorted_coarse[:CANDIDATE_POOL_SIZE],
            "fine_counts": sorted_fine[:max(FINAL_POOL_SIZE + 2, 5)],
            "final_accurate_counts": final_counts,
            "occupied_slots_final": occupied_map,
            "coarse_saturation_flags": coarse_saturation,
            "fine_saturation_flags": fine_saturation,
        }

        return estimated_values, metric, total_time_ms, verification_data
