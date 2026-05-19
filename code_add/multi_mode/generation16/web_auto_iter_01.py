import math
from typing import List, Tuple, Dict, Any

from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash


class web_auto_iter_01(IProtocol):
    def __init__(self):
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.stage_time_slots = []
        self.stage_frame_sizes = {}
        self.stage_bits_per_slot = {}
        self.last_bits_per_slot = 1

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        self._reset_stats()

        # --- 协议超参数 ---
        COARSE_BUCKET_SIZE = max(8, min(128, int(math.sqrt(max(1, price_max - price_min + 1)))))
        COARSE_INITIAL_FRAME = 128
        COARSE_BATCH_BITS = 64
        COARSE_MAX_RETRY = 3

        FINE_INITIAL_FRAME = 256
        FINE_BATCH_BITS = 32
        FINE_MAX_RETRY = 3

        VERIFY_FRAME = 1024
        VERIFY_TOP_K = 3
        CANDIDATE_BUCKET_LIMIT = 4
        EARLY_STOP_MARGIN = 0.18
        RECHECK_RATIO = 0.85

        all_buckets = self._create_buckets(price_min, price_max, COARSE_BUCKET_SIZE)
        bucket_ids = list(all_buckets.keys())

        # --- 交叉部分：融合父代 A 的“按范围分桶+候选桶聚焦”思想与父代 B 的“并行批量位图估计”思想 ---
        coarse_estimates, coarse_meta = self._batched_bucket_estimation(
            tags=tags,
            buckets=all_buckets,
            target_bucket_ids=bucket_ids,
            initial_frame_size=COARSE_INITIAL_FRAME,
            batch_bits=COARSE_BATCH_BITS,
            max_retry=COARSE_MAX_RETRY,
            stage_name="coarse"
        )

        if not coarse_estimates:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": COARSE_INITIAL_FRAME,
                "bits_per_slot": COARSE_BATCH_BITS,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "total_frames": self.total_frames,
                "message": "no_estimates"
            }
            return [], 0, total_time_ms, verification_data

        sorted_coarse = self._sort_estimates_desc(coarse_estimates)
        candidate_bucket_ids = self._select_candidate_buckets(
            sorted_items=sorted_coarse,
            limit=CANDIDATE_BUCKET_LIMIT,
            margin=EARLY_STOP_MARGIN
        )

        if not candidate_bucket_ids:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": COARSE_INITIAL_FRAME,
                "bits_per_slot": COARSE_BATCH_BITS,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "total_frames": self.total_frames,
                "coarse_estimates": sorted_coarse
            }
            return [], 0, total_time_ms, verification_data

        # --- 突变部分：引入“候选桶重检 + 自适应细粒度重分桶 + 早停判定”全新机制 ---
        candidate_bucket_ids = self._recheck_candidate_buckets(
            tags=tags,
            buckets=all_buckets,
            coarse_estimates=coarse_estimates,
            candidate_bucket_ids=candidate_bucket_ids,
            recheck_ratio=RECHECK_RATIO
        )

        fine_value_ranges = self._refine_candidate_buckets(all_buckets, candidate_bucket_ids)
        fine_value_ids = list(fine_value_ranges.keys())

        fine_estimates, fine_meta = self._batched_value_estimation(
            tags=tags,
            value_ranges=fine_value_ranges,
            target_value_ids=fine_value_ids,
            initial_frame_size=FINE_INITIAL_FRAME,
            batch_bits=FINE_BATCH_BITS,
            max_retry=FINE_MAX_RETRY,
            stage_name="fine"
        )

        if not fine_estimates:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": FINE_INITIAL_FRAME,
                "bits_per_slot": FINE_BATCH_BITS,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "coarse_estimates": sorted_coarse,
                "candidate_bucket_ids": candidate_bucket_ids,
                "total_frames": self.total_frames
            }
            return [], 0, total_time_ms, verification_data

        sorted_fine = self._sort_estimates_desc(fine_estimates)

        final_candidate_value_ids = self._select_final_value_candidates(
            sorted_items=sorted_fine,
            top_k=VERIFY_TOP_K,
            margin=EARLY_STOP_MARGIN
        )

        verified_counts = self._verify_single_values(
            tags=tags,
            value_ranges=fine_value_ranges,
            target_ids=final_candidate_value_ids,
            frame_size=VERIFY_FRAME
        )

        if not verified_counts:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": VERIFY_FRAME,
                "bits_per_slot": 1,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "coarse_estimates": sorted_coarse,
                "fine_estimates": sorted_fine,
                "candidate_bucket_ids": candidate_bucket_ids,
                "total_frames": self.total_frames
            }
            return [], 0, total_time_ms, verification_data

        max_metric = max(verified_counts.values()) if verified_counts else 0
        estimated_values = sorted([v for v, c in verified_counts.items() if c == max_metric and c > 0])

        total_time_ms = self._calculate_total_time()
        verification_data = {
            "frame_size": VERIFY_FRAME,
            "bits_per_slot": 1,
            "stage_frame_sizes": self.stage_frame_sizes,
            "stage_bits_per_slot": self.stage_bits_per_slot,
            "coarse_bucket_size": COARSE_BUCKET_SIZE,
            "coarse_estimates": sorted_coarse,
            "coarse_meta": coarse_meta,
            "candidate_bucket_ids": candidate_bucket_ids,
            "candidate_bucket_ranges": {bid: all_buckets[bid] for bid in candidate_bucket_ids},
            "fine_estimates": sorted_fine,
            "fine_meta": fine_meta,
            "final_candidate_values": [fine_value_ranges[i][0] for i in final_candidate_value_ids if i in fine_value_ranges],
            "verified_counts": verified_counts,
            "total_frames": self.total_frames
        }

        return estimated_values, int(math.ceil(max_metric)), total_time_ms, verification_data

    def _reset_stats(self) -> None:
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.stage_time_slots = []
        self.stage_frame_sizes = {}
        self.stage_bits_per_slot = {}
        self.last_bits_per_slot = 1

    def _create_buckets(self, price_min: int, price_max: int, bucket_size: int) -> Dict[int, Tuple[int, int]]:
        buckets = {}
        idx = 0
        cur = price_min
        while cur <= price_max:
            buckets[idx] = (cur, min(price_max, cur + bucket_size - 1))
            idx += 1
            cur += bucket_size
        return buckets

    def _refine_candidate_buckets(self, buckets: Dict[int, Tuple[int, int]], candidate_bucket_ids: List[int]) -> Dict[int, Tuple[int, int]]:
        refined = {}
        idx = 0
        for bid in candidate_bucket_ids:
            if bid not in buckets:
                continue
            lo, hi = buckets[bid]
            for v in range(lo, hi + 1):
                refined[idx] = (v, v)
                idx += 1
        return refined

    def _batched_bucket_estimation(
        self,
        tags: List[Tag],
        buckets: Dict[int, Tuple[int, int]],
        target_bucket_ids: List[int],
        initial_frame_size: int,
        batch_bits: int,
        max_retry: int,
        stage_name: str
    ) -> Tuple[Dict[int, float], Dict[str, Any]]:
        estimates = {}
        batch_bits = max(1, min(256, batch_bits))
        current_frame_size = max(1, min(32768, initial_frame_size))
        batches = [target_bucket_ids[i:i + batch_bits] for i in range(0, len(target_bucket_ids), batch_bits)]
        frame_history = []
        retry_history = []

        for batch in batches:
            frame_size = current_frame_size
            final_slots = None
            final_mapping = None
            retry_used = 0

            for retry in range(max_retry):
                seed = generate_random_seed()
                slots, mapping = self._simulate_tag_bucket_bitmap_response(
                    tags=tags,
                    seed=seed,
                    frame_size=frame_size,
                    bucket_map={i: buckets[i] for i in batch}
                )

                self._record_frame(
                    frame_size=frame_size,
                    bits_per_slot=len(batch),
                    reader_bits=seed.bit_length() + frame_size.bit_length() + len(batch).bit_length(),
                    stage_name=stage_name
                )

                collision_rate = self._estimate_collision_rate_from_bitmap_slots(slots, len(batch))
                final_slots = slots
                final_mapping = mapping
                retry_used = retry + 1

                if collision_rate > 0.22 and retry < max_retry - 1:
                    frame_size = min(32768, frame_size * 2)
                    continue
                break

            frame_history.append(frame_size)
            retry_history.append(retry_used)

            for local_idx, bucket_id in enumerate(batch):
                logical_bits = [((slot_val >> local_idx) & 1) for slot_val in final_slots]
                estimates[bucket_id] = self._zero_based_estimate(logical_bits, len(final_slots))

            current_frame_size = frame_size

        self.stage_frame_sizes[stage_name] = max(frame_history) if frame_history else current_frame_size
        self.stage_bits_per_slot[stage_name] = batch_bits

        return estimates, {
            "batches": len(batches),
            "frame_history": frame_history,
            "retry_history": retry_history
        }

    def _batched_value_estimation(
        self,
        tags: List[Tag],
        value_ranges: Dict[int, Tuple[int, int]],
        target_value_ids: List[int],
        initial_frame_size: int,
        batch_bits: int,
        max_retry: int,
        stage_name: str
    ) -> Tuple[Dict[int, float], Dict[str, Any]]:
        estimates = {}
        batch_bits = max(1, min(256, batch_bits))
        current_frame_size = max(1, min(32768, initial_frame_size))
        batches = [target_value_ids[i:i + batch_bits] for i in range(0, len(target_value_ids), batch_bits)]
        frame_history = []
        retry_history = []

        for batch in batches:
            frame_size = current_frame_size
            final_slots = None
            retry_used = 0

            for retry in range(max_retry):
                seed = generate_random_seed()
                slots, mapping = self._simulate_tag_value_bitmap_response(
                    tags=tags,
                    seed=seed,
                    frame_size=frame_size,
                    value_map={i: value_ranges[i] for i in batch}
                )

                self._record_frame(
                    frame_size=frame_size,
                    bits_per_slot=len(batch),
                    reader_bits=seed.bit_length() + frame_size.bit_length() + len(batch).bit_length(),
                    stage_name=stage_name
                )

                collision_rate = self._estimate_collision_rate_from_bitmap_slots(slots, len(batch))
                final_slots = slots
                retry_used = retry + 1

                if collision_rate > 0.18 and retry < max_retry - 1:
                    frame_size = min(32768, frame_size * 2)
                    continue
                break

            frame_history.append(frame_size)
            retry_history.append(retry_used)

            for local_idx, value_id in enumerate(batch):
                logical_bits = [((slot_val >> local_idx) & 1) for slot_val in final_slots]
                estimates[value_id] = self._zero_based_estimate(logical_bits, len(final_slots))

            current_frame_size = frame_size

        self.stage_frame_sizes[stage_name] = max(frame_history) if frame_history else current_frame_size
        self.stage_bits_per_slot[stage_name] = batch_bits

        return estimates, {
            "batches": len(batches),
            "frame_history": frame_history,
            "retry_history": retry_history
        }

    def _recheck_candidate_buckets(
        self,
        tags: List[Tag],
        buckets: Dict[int, Tuple[int, int]],
        coarse_estimates: Dict[int, float],
        candidate_bucket_ids: List[int],
        recheck_ratio: float
    ) -> List[int]:
        if not candidate_bucket_ids:
            return []

        rechecked = {}
        frame_size = 64
        for bid in candidate_bucket_ids:
            if bid not in buckets:
                continue
            lo, hi = buckets[bid]
            seed = generate_random_seed()
            occupied_slots = self._simulate_tag_presence_frame_for_range(tags, lo, hi, seed, frame_size)
            self._record_frame(
                frame_size=frame_size,
                bits_per_slot=1,
                reader_bits=seed.bit_length() + frame_size.bit_length() + lo.bit_length() + hi.bit_length(),
                stage_name="recheck"
            )
            rechecked[bid] = occupied_slots

        if not rechecked:
            return candidate_bucket_ids

        best_presence = max(rechecked.values())
        filtered = []
        for bid in candidate_bucket_ids:
            est = coarse_estimates.get(bid, 0.0)
            presence = rechecked.get(bid, 0)
            if presence >= best_presence * recheck_ratio or est == max(coarse_estimates.get(x, 0.0) for x in candidate_bucket_ids):
                filtered.append(bid)

        self.stage_frame_sizes["recheck"] = frame_size
        self.stage_bits_per_slot["recheck"] = 1

        return filtered[:max(1, len(candidate_bucket_ids))]

    def _select_candidate_buckets(self, sorted_items: List[Tuple[int, float]], limit: int, margin: float) -> List[int]:
        if not sorted_items:
            return []
        top_id, top_val = sorted_items[0]
        if top_val <= 0:
            return []

        selected = []
        for bid, val in sorted_items:
            if len(selected) < limit:
                if top_val == float("inf"):
                    if val == float("inf"):
                        selected.append(bid)
                else:
                    if val >= top_val * (1.0 - margin):
                        selected.append(bid)
            else:
                break

        if not selected:
            selected = [top_id]
        return selected

    def _select_final_value_candidates(self, sorted_items: List[Tuple[int, float]], top_k: int, margin: float) -> List[int]:
        if not sorted_items:
            return []
        top_id, top_val = sorted_items[0]
        if top_val <= 0:
            return []

        selected = []
        for vid, val in sorted_items:
            if len(selected) >= top_k:
                break
            if top_val == float("inf"):
                if val == float("inf"):
                    selected.append(vid)
            else:
                if val >= top_val * (1.0 - margin):
                    selected.append(vid)

        if not selected:
            selected = [top_id]
        return selected[:top_k]

    def _verify_single_values(
        self,
        tags: List[Tag],
        value_ranges: Dict[int, Tuple[int, int]],
        target_ids: List[int],
        frame_size: int
    ) -> Dict[int, float]:
        result = {}
        self.stage_frame_sizes["verify"] = frame_size
        self.stage_bits_per_slot["verify"] = 1

        for value_id in target_ids:
            if value_id not in value_ranges:
                continue
            lo, hi = value_ranges[value_id]
            seed = generate_random_seed()
            slots = self._simulate_tag_single_value_frame(tags, lo, hi, seed, frame_size)
            self._record_frame(
                frame_size=frame_size,
                bits_per_slot=1,
                reader_bits=seed.bit_length() + frame_size.bit_length() + lo.bit_length(),
                stage_name="verify"
            )
            result[lo] = self._zero_based_estimate(slots, frame_size)
        return result

    def _simulate_tag_bucket_bitmap_response(
        self,
        tags: List[Tag],
        seed: int,
        frame_size: int,
        bucket_map: Dict[int, Tuple[int, int]]
    ) -> Tuple[List[int], Dict[int, int]]:
        slots = [0] * frame_size
        key_list = list(bucket_map.keys())
        local_pos = {bucket_id: idx for idx, bucket_id in enumerate(key_list)}

        for tag in tags:
            bucket_id = self._tag_response_bucket_match(tag, bucket_map)
            if bucket_id is None:
                continue
            slot_id = simple_hash(tag.id, seed) % frame_size
            bit_pos = local_pos[bucket_id]
            slots[slot_id] |= (1 << bit_pos)

        return slots, local_pos

    def _simulate_tag_value_bitmap_response(
        self,
        tags: List[Tag],
        seed: int,
        frame_size: int,
        value_map: Dict[int, Tuple[int, int]]
    ) -> Tuple[List[int], Dict[int, int]]:
        slots = [0] * frame_size
        key_list = list(value_map.keys())
        local_pos = {value_id: idx for idx, value_id in enumerate(key_list)}

        for tag in tags:
            value_id = self._tag_response_value_match(tag, value_map)
            if value_id is None:
                continue
            slot_id = simple_hash(tag.id, seed) % frame_size
            bit_pos = local_pos[value_id]
            slots[slot_id] |= (1 << bit_pos)

        return slots, local_pos

    def _simulate_tag_presence_frame_for_range(
        self,
        tags: List[Tag],
        lo: int,
        hi: int,
        seed: int,
        frame_size: int
    ) -> int:
        slots = [0] * frame_size
        for tag in tags:
            bit = self._tag_response_range_presence(tag, lo, hi)
            if bit == 0:
                continue
            slot_id = simple_hash(tag.id, seed) % frame_size
            slots[slot_id] += 1
        return sum(1 for x in slots if x > 0)

    def _simulate_tag_single_value_frame(
        self,
        tags: List[Tag],
        lo: int,
        hi: int,
        seed: int,
        frame_size: int
    ) -> List[int]:
        slots = [0] * frame_size
        for tag in tags:
            bit = self._tag_response_range_presence(tag, lo, hi)
            if bit == 0:
                continue
            slot_id = simple_hash(tag.id, seed) % frame_size
            slots[slot_id] += 1
        return slots

    def _tag_response_bucket_match(self, tag: Tag, bucket_map: Dict[int, Tuple[int, int]]) -> Any:
        for bucket_id, (lo, hi) in bucket_map.items():
            if lo <= tag.price <= hi:
                return bucket_id
        return None

    def _tag_response_value_match(self, tag: Tag, value_map: Dict[int, Tuple[int, int]]) -> Any:
        for value_id, (lo, hi) in value_map.items():
            if lo <= tag.price <= hi:
                return value_id
        return None

    def _tag_response_range_presence(self, tag: Tag, lo: int, hi: int) -> int:
        return 1 if lo <= tag.price <= hi else 0

    def _estimate_collision_rate_from_bitmap_slots(self, slots: List[int], bits_per_slot: int) -> float:
        if not slots or bits_per_slot <= 0:
            return 0.0
        occupied = 0
        multi = 0
        for s in slots:
            if s != 0:
                occupied += 1
                bit_count = s.bit_count() if hasattr(int, "bit_count") else bin(s).count("1")
                if bit_count > 1:
                    multi += 1
        return (multi / occupied) if occupied > 0 else 0.0

    def _zero_based_estimate(self, logical_bits: List[int], frame_size: int) -> float:
        ones = sum(1 for b in logical_bits if b)
        zeros = frame_size - ones
        if zeros == frame_size:
            return 0.0
        if zeros == 0:
            return float("inf")
        return -frame_size * math.log(zeros / frame_size)

    def _sort_estimates_desc(self, estimates: Dict[int, float]) -> List[Tuple[int, float]]:
        return sorted(
            estimates.items(),
            key=lambda x: (x[1] == float("inf"), x[1]),
            reverse=True
        )

    def _record_frame(self, frame_size: int, bits_per_slot: int, reader_bits: int, stage_name: str) -> None:
        bits_per_slot = max(1, min(256, bits_per_slot))
        frame_size = max(1, min(32768, frame_size))
        self.total_frames += 1
        self.total_bits_sent_by_reader += max(0, reader_bits)
        self.total_slots += frame_size
        self.last_bits_per_slot = bits_per_slot

    def _calculate_total_time(self) -> float:
        T_reader = self.total_bits_sent_by_reader * 0.025
        T_tag = self.total_slots * self.last_bits_per_slot * 0.4
        T_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0
        return T_reader + T_tag + T_overhead
