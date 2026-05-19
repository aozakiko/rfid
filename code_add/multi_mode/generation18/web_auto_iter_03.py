import math
from typing import List, Tuple, Dict, Set, Any
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash


class web_auto_iter_03(IProtocol):
    """
    面向Web平台的自优化RFID子代协议
    当前任务画像：离散属性众数/多众数查询
    """

    def __init__(self):
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_tag_response_bits = 0
        self.stage_frame_sizes = []
        self.stage_bits_per_slot = []
        self.frame_size = 0
        self.bits_per_slot = 1

        self.initial_filter_frame_size = 256
        self.num_hash_functions = 2
        self.max_filter_rounds = 5
        self.candidate_threshold = 24
        self.high_density_threshold = 0.78
        self.low_density_threshold = 0.22
        self.min_filter_frame_size = 64
        self.max_filter_frame_size = 4096

        self.bucket_size = 64
        self.initial_bucket_frame_size = 128
        self.bucket_collision_threshold = 0.12
        self.bucket_resize_factor = 2
        self.bucket_max_retries = 3

        self.verification_frame_size = 256
        self.max_candidates_for_parallel_verify = 16

        self.early_stop_ratio = 0.96
        self.recheck_margin_ratio = 0.90
        self.recheck_top_k = 3

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        self._reset_stats()

        if price_min > price_max:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 1,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "status": "invalid_range",
            }
            return [], 0, total_time_ms, verification_data

        candidate_prices = set(range(price_min, price_max + 1))
        current_filter_frame_size = min(max(self.initial_filter_frame_size, 1), 32768)
        filter_rounds_done = 0
        termination_reason = "max_rounds"
        filter_history = []

        # --- 交叉部分：融合父代 A 的“范围/桶级粗扫描”思想与父代 B 的“多哈希自适应过滤”思想 ---
        while len(candidate_prices) > self.candidate_threshold and filter_rounds_done < self.max_filter_rounds:
            seeds = [generate_random_seed() for _ in range(self.num_hash_functions)]
            frames = self._simulate_tag_response_filter(tags, candidate_prices, current_filter_frame_size, seeds)

            self.total_frames += self.num_hash_functions
            self.total_bits_sent_by_reader += self.num_hash_functions * (current_filter_frame_size.bit_length() + 32)
            self.total_tag_response_bits += self.num_hash_functions * current_filter_frame_size * 1
            self.stage_frame_sizes.extend([current_filter_frame_size] * self.num_hash_functions)
            self.stage_bits_per_slot.extend([1] * self.num_hash_functions)
            self.frame_size = current_filter_frame_size
            self.bits_per_slot = 1

            new_candidates = set()
            for p in candidate_prices:
                survive = True
                for i in range(self.num_hash_functions):
                    slot_id = simple_hash(p, seeds[i]) % current_filter_frame_size
                    if frames[i][slot_id] == 0:
                        survive = False
                        break
                if survive:
                    new_candidates.add(p)

            reduction_ratio = (len(new_candidates) / len(candidate_prices)) if candidate_prices else 0.0
            density = self._frame_density(frames)

            filter_history.append({
                "round": filter_rounds_done + 1,
                "frame_size": current_filter_frame_size,
                "input_candidates": len(candidate_prices),
                "output_candidates": len(new_candidates),
                "reduction_ratio": reduction_ratio,
                "density": density,
            })

            # --- 突变部分：引入“收益递减早停 + 候选桶重检触发”全新机制 ---
            if not new_candidates:
                candidate_prices = set()
                termination_reason = "no_candidates_survived"
                filter_rounds_done += 1
                break

            candidate_prices = new_candidates
            filter_rounds_done += 1

            if len(candidate_prices) <= self.candidate_threshold:
                termination_reason = "candidate_threshold_reached"
                break

            if reduction_ratio > self.early_stop_ratio:
                termination_reason = "diminishing_returns_early_stop"
                break

            if density > self.high_density_threshold:
                current_filter_frame_size = min(current_filter_frame_size * 2, self.max_filter_frame_size, 32768)
            elif density < self.low_density_threshold:
                current_filter_frame_size = max(current_filter_frame_size // 2, self.min_filter_frame_size)

        bucket_estimates = {}
        bucket_frame_sizes = {}
        candidate_buckets = {}

        # --- 交叉部分：融合父代 A 的“候选范围二阶段聚焦”与父代 B 的“过滤后并行验证准备”思想 ---
        if candidate_prices:
            buckets = self._create_buckets_from_candidates(candidate_prices, self.bucket_size)
            for bucket_id, bucket_range in buckets.items():
                est_count, used_frame = self._estimate_bucket_dynamically(tags, bucket_range)
                bucket_estimates[bucket_id] = est_count
                bucket_frame_sizes[bucket_id] = used_frame
                candidate_buckets[bucket_id] = bucket_range
        else:
            buckets = {}

        shortlisted_prices = set()
        shortlisted_bucket_ids = []
        bucket_threshold_value = 0.0

        if bucket_estimates:
            finite_values = [v for v in bucket_estimates.values() if v != float("inf")]
            if any(v == float("inf") for v in bucket_estimates.values()):
                shortlisted_bucket_ids = [bid for bid, v in bucket_estimates.items() if v == float("inf")]
                bucket_threshold_value = float("inf")
            elif finite_values:
                max_est = max(finite_values)
                bucket_threshold_value = max_est * self.recheck_margin_ratio
                shortlisted_bucket_ids = [bid for bid, v in bucket_estimates.items() if v >= bucket_threshold_value]
                shortlisted_bucket_ids = sorted(
                    shortlisted_bucket_ids,
                    key=lambda x: bucket_estimates.get(x, 0),
                    reverse=True
                )[:max(self.recheck_top_k, 1)]

        for bid in shortlisted_bucket_ids:
            r0, r1 = candidate_buckets[bid]
            for p in candidate_prices:
                if r0 <= p <= r1:
                    shortlisted_prices.add(p)

        if not shortlisted_prices and candidate_prices:
            shortlisted_prices = set(sorted(candidate_prices)[:min(len(candidate_prices), self.max_candidates_for_parallel_verify)])

        if len(shortlisted_prices) > self.max_candidates_for_parallel_verify:
            shortlisted_prices = self._rank_candidates_by_bucket(shortlisted_prices, candidate_buckets, bucket_estimates, self.max_candidates_for_parallel_verify)

        final_counts = {}
        verification_bitmap = []
        verified_candidates = sorted(shortlisted_prices)

        # --- 交叉部分：融合父代 B 的“并行位图验证”思想与父代 A 的“最终精查”思想 ---
        if verified_candidates:
            final_counts, verification_bitmap = self._parallel_verify_candidates(tags, verified_candidates)

        # --- 突变部分：引入“多阶段校验后的保守候选输出”全新机制 ---
        estimated_values = []
        metric = 0

        if final_counts:
            max_score = max(final_counts.values())
            conservative_threshold = max(1, math.floor(max_score * self.recheck_margin_ratio))
            estimated_values = sorted([p for p, c in final_counts.items() if c >= conservative_threshold and c > 0])
            metric = max_score

            if not estimated_values:
                best_prices = [p for p, c in final_counts.items() if c == max_score]
                estimated_values = sorted(best_prices)
        else:
            estimated_values = []
            metric = 0

        total_time_ms = self._calculate_total_time()
        verification_data = {
            "frame_size": self.frame_size,
            "bits_per_slot": self.bits_per_slot,
            "stage_frame_sizes": self.stage_frame_sizes,
            "stage_bits_per_slot": self.stage_bits_per_slot,
            "total_frames": self.total_frames,
            "filter_rounds_done": filter_rounds_done,
            "termination_reason": termination_reason,
            "filter_history": filter_history,
            "bucket_size": self.bucket_size,
            "bucket_estimates": bucket_estimates,
            "bucket_frame_sizes": bucket_frame_sizes,
            "shortlisted_bucket_ids": shortlisted_bucket_ids,
            "shortlisted_candidates": verified_candidates,
            "final_counts": final_counts,
            "verification_frame_size": min(self.verification_frame_size, 32768),
            "verification_bits_per_slot": min(max(len(verified_candidates), 1), 256),
            "verification_bitmap_nonzero_slots": sum(1 for x in verification_bitmap if x != 0) if verification_bitmap else 0,
        }

        return estimated_values, metric, total_time_ms, verification_data

    def _reset_stats(self) -> None:
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_tag_response_bits = 0
        self.stage_frame_sizes = []
        self.stage_bits_per_slot = []
        self.frame_size = 0
        self.bits_per_slot = 1

    def _simulate_tag_response_filter(self, tags: List[Tag], candidate_prices: Set[int], frame_size: int, seeds: List[int]) -> List[List[int]]:
        frames = [[0] * frame_size for _ in seeds]
        for t in tags:
            tag_price = getattr(t, "price", None)
            if tag_price in candidate_prices:
                for i, seed in enumerate(seeds):
                    slot_id = simple_hash(tag_price, seed) % frame_size
                    frames[i][slot_id] = 1
        return frames

    def _simulate_tag_response_bucket(self, tags: List[Tag], price_range: Tuple[int, int], frame_size: int, seed: int) -> List[int]:
        slots = [0] * frame_size
        low, high = price_range
        for t in tags:
            tag_price = getattr(t, "price", None)
            if low <= tag_price <= high:
                slot_id = simple_hash(t.id, seed) % frame_size
                slots[slot_id] += 1
        return slots

    def _simulate_tag_response_parallel_verify(self, tags: List[Tag], candidates: List[int], frame_size: int, seed: int) -> List[int]:
        slots = [0] * frame_size
        pos_map = {p: i for i, p in enumerate(candidates)}
        for t in tags:
            tag_price = getattr(t, "price", None)
            if tag_price in pos_map:
                slot_id = simple_hash(t.id, seed) % frame_size
                bit_pos = pos_map[tag_price]
                slots[slot_id] |= (1 << bit_pos)
        return slots

    def _estimate_bucket_dynamically(self, tags: List[Tag], price_range: Tuple[int, int]) -> Tuple[float, int]:
        current_frame_size = min(max(self.initial_bucket_frame_size, 1), 32768)
        low, high = price_range

        for retry in range(self.bucket_max_retries):
            seed = generate_random_seed()
            slots = self._simulate_tag_response_bucket(tags, price_range, current_frame_size, seed)

            self.total_frames += 1
            self.total_bits_sent_by_reader += current_frame_size.bit_length() + 32 + low.bit_length() + high.bit_length()
            self.total_tag_response_bits += current_frame_size * 1
            self.stage_frame_sizes.append(current_frame_size)
            self.stage_bits_per_slot.append(1)
            self.frame_size = current_frame_size
            self.bits_per_slot = 1

            num_collisions = sum(1 for s in slots if s > 1)
            collision_rate = num_collisions / current_frame_size if current_frame_size > 0 else 0.0

            if collision_rate > self.bucket_collision_threshold and retry < self.bucket_max_retries - 1:
                current_frame_size = min(int(current_frame_size * self.bucket_resize_factor), 32768)
                continue

            num_zeros = sum(1 for s in slots if s == 0)
            if num_zeros == 0:
                return float("inf"), current_frame_size
            if num_zeros == current_frame_size:
                return 0.0, current_frame_size

            est = -current_frame_size * math.log(num_zeros / current_frame_size)
            return est, current_frame_size

        return float("inf"), current_frame_size

    def _parallel_verify_candidates(self, tags: List[Tag], candidates: List[int]) -> Tuple[Dict[int, int], List[int]]:
        frame_size = min(max(self.verification_frame_size, 1), 32768)
        bits_per_slot = min(max(len(candidates), 1), 256)
        candidates = candidates[:bits_per_slot]

        seed = generate_random_seed()
        bitmap_slots = self._simulate_tag_response_parallel_verify(tags, candidates, frame_size, seed)

        self.total_frames += 1
        self.total_bits_sent_by_reader += frame_size.bit_length() + 32 + sum(max(1, p.bit_length()) for p in candidates)
        self.total_tag_response_bits += frame_size * bits_per_slot
        self.stage_frame_sizes.append(frame_size)
        self.stage_bits_per_slot.append(bits_per_slot)
        self.frame_size = frame_size
        self.bits_per_slot = bits_per_slot

        counts = {}
        for i, price in enumerate(candidates):
            mask = 1 << i
            counts[price] = sum(1 for slot_data in bitmap_slots if (slot_data & mask) != 0)
        return counts, bitmap_slots

    def _create_buckets_from_candidates(self, candidate_prices: Set[int], bucket_size: int) -> Dict[int, Tuple[int, int]]:
        if not candidate_prices:
            return {}
        sorted_prices = sorted(candidate_prices)
        buckets = {}
        bucket_id = 0
        start = sorted_prices[0]
        end = start + bucket_size - 1

        current_bucket_prices = []
        for p in sorted_prices:
            if p <= end:
                current_bucket_prices.append(p)
            else:
                if current_bucket_prices:
                    buckets[bucket_id] = (current_bucket_prices[0], current_bucket_prices[-1])
                    bucket_id += 1
                start = p
                end = start + bucket_size - 1
                current_bucket_prices = [p]
        if current_bucket_prices:
            buckets[bucket_id] = (current_bucket_prices[0], current_bucket_prices[-1])
        return buckets

    def _rank_candidates_by_bucket(self, shortlisted_prices: Set[int], candidate_buckets: Dict[int, Tuple[int, int]], bucket_estimates: Dict[int, float], limit: int) -> Set[int]:
        scored = []
        for p in shortlisted_prices:
            score = 0.0
            for bid, rng in candidate_buckets.items():
                if rng[0] <= p <= rng[1]:
                    score = bucket_estimates.get(bid, 0.0)
                    break
            scored.append((score, p))
        scored.sort(key=lambda x: (-float("inf") if x[0] == float("inf") else -x[0], x[1]))
        return set(p for _, p in scored[:limit])

    def _frame_density(self, frames: List[List[int]]) -> float:
        total_slots = sum(len(f) for f in frames)
        if total_slots == 0:
            return 0.0
        non_empty = sum(sum(1 for s in f if s > 0) for f in frames)
        return non_empty / total_slots

    def _calculate_total_time(self) -> float:
        t_reader = self.total_bits_sent_by_reader * 0.025
        t_tag = self.total_tag_response_bits * 0.4
        t_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0
        return t_reader + t_tag + t_overhead
