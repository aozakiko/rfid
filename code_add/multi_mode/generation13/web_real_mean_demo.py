import math
from typing import List, Tuple, Dict, Any
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash


class web_real_mean_demo(IProtocol):
    def __init__(self):
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.stage_slot_bit_cost = 0.0
        self.last_frame_size = 0
        self.last_bits_per_slot = 1

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[float], float, float, Dict]:
        # 协议参数
        value_min = price_min
        value_max = price_max
        domain_size = max(1, value_max - value_min + 1)

        coarse_bucket_count = min(64, max(8, int(math.sqrt(domain_size))))
        coarse_bucket_count = self._clamp_power_friendly(coarse_bucket_count, 8, 64)
        coarse_frame_size = 256
        coarse_bits_per_slot = min(64, coarse_bucket_count)

        fine_candidate_count = 3
        fine_frame_size = 512
        fine_bits_per_slot = 32

        verify_frame_size = 1024
        verify_bits_per_slot = 1

        collision_threshold_high = 0.28
        collision_threshold_low = 0.03
        max_resize_rounds = 3
        sample_budget_cap = min(256, max(64, int(math.sqrt(max(1, len(tags))) * 8)))
        early_stop_margin = 0.015

        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.stage_slot_bit_cost = 0.0
        self.last_frame_size = coarse_frame_size
        self.last_bits_per_slot = coarse_bits_per_slot

        if value_max < value_min:
            verification_data = {
                "frame_size": coarse_frame_size,
                "bits_per_slot": coarse_bits_per_slot,
                "stage_frame_sizes": {"coarse": coarse_frame_size, "fine": fine_frame_size, "verify": verify_frame_size},
                "stage_bits_per_slot": {"coarse": coarse_bits_per_slot, "fine": fine_bits_per_slot, "verify": verify_bits_per_slot},
                "total_frames": 0,
                "message": "invalid range",
            }
            return [0.0], 0.0, 0.0, verification_data

        # --- 交叉部分：融合父代 A 的“按范围分桶粗扫+自适应帧长”思想与父代 B 的“位图并行批量估计+分阶段筛选”思想 ---
        coarse_buckets = self._create_equal_buckets(value_min, value_max, coarse_bucket_count)
        coarse_result = self._parallel_bucket_estimation(
            tags=tags,
            buckets=coarse_buckets,
            init_frame_size=coarse_frame_size,
            bits_per_slot=coarse_bits_per_slot,
            collision_threshold_high=collision_threshold_high,
            collision_threshold_low=collision_threshold_low,
            max_resize_rounds=max_resize_rounds,
            phase_name="coarse",
        )

        total_count_est = max(0.0, coarse_result["total_count_est"])
        if total_count_est <= 0:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": coarse_result["frame_size"],
                "bits_per_slot": coarse_result["bits_per_slot"],
                "stage_frame_sizes": {"coarse": coarse_result["frame_size"], "fine": fine_frame_size, "verify": verify_frame_size},
                "stage_bits_per_slot": {"coarse": coarse_result["bits_per_slot"], "fine": fine_bits_per_slot, "verify": verify_bits_per_slot},
                "coarse_bucket_estimates": coarse_result["bucket_estimates"],
                "coarse_bucket_ranges": coarse_buckets,
                "total_frames": self.total_frames,
                "estimated_total_count": 0.0,
            }
            return [0.0], 0.0, total_time_ms, verification_data

        coarse_mean = self._bucket_weighted_mean(coarse_result["bucket_estimates"], coarse_buckets)
        sorted_bucket_ids = sorted(
            coarse_result["bucket_estimates"].keys(),
            key=lambda bid: coarse_result["bucket_estimates"][bid],
            reverse=True
        )
        candidate_bucket_ids = sorted_bucket_ids[:fine_candidate_count]

        # --- 交叉部分：融合父代 A 的“候选范围聚焦”思想与父代 B 的“对候选集合进行二阶段精估”思想 ---
        fine_bucket_map = {}
        fine_bucket_parent = {}
        next_id = 0
        for parent_id in candidate_bucket_ids:
            sub_buckets = self._split_bucket(coarse_buckets[parent_id], max(4, min(16, fine_bits_per_slot)))
            for sub_range in sub_buckets:
                fine_bucket_map[next_id] = sub_range
                fine_bucket_parent[next_id] = parent_id
                next_id += 1

        fine_result = None
        if fine_bucket_map:
            fine_result = self._parallel_bucket_estimation(
                tags=tags,
                buckets=fine_bucket_map,
                init_frame_size=fine_frame_size,
                bits_per_slot=min(fine_bits_per_slot, max(1, len(fine_bucket_map))),
                collision_threshold_high=collision_threshold_high,
                collision_threshold_low=collision_threshold_low,
                max_resize_rounds=max_resize_rounds,
                phase_name="fine",
            )

        refined_mean = coarse_mean
        if fine_result and fine_result["total_count_est"] > 0:
            non_candidate_sum = 0.0
            non_candidate_weight = 0.0
            for bid, est in coarse_result["bucket_estimates"].items():
                if bid not in candidate_bucket_ids:
                    center = self._bucket_center(coarse_buckets[bid])
                    non_candidate_sum += est * center
                    non_candidate_weight += est

            candidate_sum = 0.0
            candidate_weight = 0.0
            for fid, est in fine_result["bucket_estimates"].items():
                center = self._bucket_center(fine_bucket_map[fid])
                candidate_sum += est * center
                candidate_weight += est

            if non_candidate_weight + candidate_weight > 0:
                refined_mean = (non_candidate_sum + candidate_sum) / (non_candidate_weight + candidate_weight)

        # --- 突变部分：引入“围绕当前均值估计的分层抽样验证 + 早停修正”全新机制 ---
        verify_window = self._derive_verify_window(refined_mean, value_min, value_max, domain_size)
        sampled_mean, sample_count, verify_info = self._stratified_verify_mean(
            tags=tags,
            mean_anchor=refined_mean,
            verify_window=verify_window,
            frame_size=verify_frame_size,
            bits_per_slot=verify_bits_per_slot,
            sample_budget_cap=sample_budget_cap,
            early_stop_margin=early_stop_margin,
        )

        if sample_count > 0:
            reliability = min(1.0, sample_count / max(16.0, sample_budget_cap))
            final_mean = (1.0 - reliability) * refined_mean + reliability * sampled_mean
        else:
            final_mean = refined_mean

        final_mean = float(max(value_min, min(value_max, final_mean)))
        total_time_ms = self._calculate_total_time()

        metric = final_mean
        verification_data = {
            "frame_size": max(
                coarse_result["frame_size"],
                fine_result["frame_size"] if fine_result else 0,
                verify_frame_size
            ),
            "bits_per_slot": max(
                coarse_result["bits_per_slot"],
                fine_result["bits_per_slot"] if fine_result else 1,
                verify_bits_per_slot
            ),
            "stage_frame_sizes": {
                "coarse": coarse_result["frame_size"],
                "fine": fine_result["frame_size"] if fine_result else 0,
                "verify": verify_frame_size,
            },
            "stage_bits_per_slot": {
                "coarse": coarse_result["bits_per_slot"],
                "fine": fine_result["bits_per_slot"] if fine_result else 0,
                "verify": verify_bits_per_slot,
            },
            "total_frames": self.total_frames,
            "coarse_bucket_ranges": coarse_buckets,
            "coarse_bucket_estimates": coarse_result["bucket_estimates"],
            "candidate_bucket_ids": candidate_bucket_ids,
            "fine_bucket_ranges": fine_bucket_map,
            "fine_bucket_estimates": fine_result["bucket_estimates"] if fine_result else {},
            "estimated_total_count": total_count_est,
            "coarse_mean": coarse_mean,
            "refined_mean": refined_mean,
            "verification_sample_mean": sampled_mean,
            "verification_sample_count": sample_count,
            "verification_window": verify_window,
            "verify_rounds": verify_info.get("rounds", 0),
            "verify_samples_per_round": verify_info.get("samples_per_round", []),
        }

        return [final_mean], metric, total_time_ms, verification_data

    def _parallel_bucket_estimation(
        self,
        tags: List[Tag],
        buckets: Dict[int, Tuple[int, int]],
        init_frame_size: int,
        bits_per_slot: int,
        collision_threshold_high: float,
        collision_threshold_low: float,
        max_resize_rounds: int,
        phase_name: str,
    ) -> Dict[str, Any]:
        frame_size = max(16, min(32768, init_frame_size))
        bucket_items = list(buckets.items())
        active_bucket_count = len(bucket_items)
        bits_per_slot = max(1, min(256, min(bits_per_slot, max(1, active_bucket_count))))
        selected_items = bucket_items[:bits_per_slot]

        for _ in range(max_resize_rounds):
            seed = generate_random_seed()
            self.total_frames += 1
            self.total_bits_sent_by_reader += (
                self._bit_len_safe(seed) +
                self._bit_len_safe(frame_size) +
                self._bit_len_safe(bits_per_slot) +
                self._bit_len_safe(active_bucket_count) +
                self._range_bits_cost(selected_items)
            )

            frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)

            bucket_index_to_bit = {bucket_id: idx for idx, (bucket_id, _) in enumerate(selected_items)}
            for tag in tags:
                tag_value = self._get_tag_value(tag)
                if tag_value is None:
                    continue
                matched_bucket = None
                for bucket_id, (lo, hi) in selected_items:
                    if lo <= tag_value <= hi:
                        matched_bucket = bucket_id
                        break
                if matched_bucket is None:
                    continue
                slot_id = simple_hash(tag.id, seed) % frame_size
                reply_vector = 1 << bucket_index_to_bit[matched_bucket]
                frame.aggregate(slot_id, reply_vector)

            self.total_slots += frame_size
            self.stage_slot_bit_cost += frame_size * bits_per_slot
            self.last_frame_size = frame_size
            self.last_bits_per_slot = bits_per_slot

            collision_slots = 0
            occupied_slots = 0
            bucket_estimates = {}
            union_occupied = 0

            for slot_val in frame.slots:
                if slot_val != 0:
                    occupied_slots += 1
                    union_occupied += 1
                    if bin(slot_val).count("1") > 1:
                        collision_slots += 1

            collision_rate = collision_slots / frame_size if frame_size > 0 else 0.0

            for bucket_id, _ in selected_items:
                bit_pos = bucket_index_to_bit[bucket_id]
                ones = 0
                for slot_val in frame.slots:
                    if ((slot_val >> bit_pos) & 1) == 1:
                        ones += 1
                zeros = frame_size - ones
                if zeros == frame_size:
                    est = 0.0
                elif zeros == 0:
                    est = -frame_size * math.log(1.0 / (frame_size + 1.0))
                else:
                    est = -frame_size * math.log(zeros / frame_size)
                bucket_estimates[bucket_id] = est

            total_count_est = 0.0
            for est in bucket_estimates.values():
                total_count_est += est

            if collision_rate > collision_threshold_high and frame_size < 32768:
                frame_size = min(32768, frame_size * 2)
                continue
            if collision_rate < collision_threshold_low and frame_size > 32:
                frame_size = max(32, frame_size // 2)
                break
            break

        return {
            "phase": phase_name,
            "frame_size": frame_size,
            "bits_per_slot": bits_per_slot,
            "bucket_estimates": bucket_estimates,
            "total_count_est": total_count_est,
            "collision_rate": collision_rate,
            "occupied_slots": occupied_slots,
            "union_occupied": union_occupied,
        }

    def _stratified_verify_mean(
        self,
        tags: List[Tag],
        mean_anchor: float,
        verify_window: Tuple[int, int],
        frame_size: int,
        bits_per_slot: int,
        sample_budget_cap: int,
        early_stop_margin: float,
    ) -> Tuple[float, int, Dict[str, Any]]:
        lo, hi = verify_window
        center_left = (lo + mean_anchor) / 2.0
        center_right = (mean_anchor + hi) / 2.0

        seeds = [generate_random_seed(), generate_random_seed(), generate_random_seed()]
        samples = []
        per_round_counts = []

        rounds = 3
        current_estimate = None

        for r in range(rounds):
            seed = seeds[r]
            threshold_basis = seed % 1000003
            self.total_frames += 1
            self.total_bits_sent_by_reader += (
                self._bit_len_safe(seed) +
                self._bit_len_safe(frame_size) +
                self._bit_len_safe(bits_per_slot) +
                self._bit_len_safe(int(lo)) +
                self._bit_len_safe(int(hi)) +
                self._bit_len_safe(int(sample_budget_cap))
            )
            self.total_slots += frame_size
            self.stage_slot_bit_cost += frame_size * bits_per_slot
            self.last_frame_size = frame_size
            self.last_bits_per_slot = bits_per_slot

            target_this_round = max(8, sample_budget_cap // rounds)
            round_samples = []

            for tag in tags:
                value = self._get_tag_value(tag)
                if value is None:
                    continue
                if value < lo or value > hi:
                    continue

                if value <= mean_anchor:
                    local_weight = max(1, int(1000 / (1 + abs(value - center_left))))
                else:
                    local_weight = max(1, int(1000 / (1 + abs(value - center_right))))

                hashed = simple_hash(tag.id, seed) % 1000003
                if hashed < min(1000002, threshold_basis % max(2, local_weight * 32)):
                    round_samples.append(value)
                    if len(round_samples) >= target_this_round:
                        break

            per_round_counts.append(len(round_samples))
            samples.extend(round_samples)

            if samples:
                new_estimate = sum(samples) / len(samples)
                if current_estimate is not None:
                    rel_change = abs(new_estimate - current_estimate) / max(1.0, abs(current_estimate))
                    if rel_change <= early_stop_margin:
                        current_estimate = new_estimate
                        break
                current_estimate = new_estimate

        if not samples:
            return mean_anchor, 0, {"rounds": rounds, "samples_per_round": per_round_counts}

        return sum(samples) / len(samples), len(samples), {"rounds": len(per_round_counts), "samples_per_round": per_round_counts}

    def _derive_verify_window(self, mean_anchor: float, value_min: int, value_max: int, domain_size: int) -> Tuple[int, int]:
        radius = max(4, int(math.sqrt(max(1, domain_size))))
        lo = max(value_min, int(math.floor(mean_anchor - radius)))
        hi = min(value_max, int(math.ceil(mean_anchor + radius)))
        if lo > hi:
            lo, hi = value_min, value_max
        return lo, hi

    def _create_equal_buckets(self, value_min: int, value_max: int, bucket_count: int) -> Dict[int, Tuple[int, int]]:
        buckets = {}
        total_values = value_max - value_min + 1
        bucket_count = max(1, min(bucket_count, total_values))
        base = total_values // bucket_count
        rem = total_values % bucket_count
        start = value_min
        for i in range(bucket_count):
            width = base + (1 if i < rem else 0)
            end = start + width - 1
            buckets[i] = (start, end)
            start = end + 1
        return buckets

    def _split_bucket(self, bucket_range: Tuple[int, int], sub_count: int) -> List[Tuple[int, int]]:
        lo, hi = bucket_range
        width = hi - lo + 1
        sub_count = max(1, min(sub_count, width))
        base = width // sub_count
        rem = width % sub_count
        result = []
        cur = lo
        for i in range(sub_count):
            w = base + (1 if i < rem else 0)
            end = cur + w - 1
            result.append((cur, end))
            cur = end + 1
        return result

    def _bucket_center(self, bucket_range: Tuple[int, int]) -> float:
        lo, hi = bucket_range
        return (lo + hi) / 2.0

    def _bucket_weighted_mean(self, estimates: Dict[int, float], buckets: Dict[int, Tuple[int, int]]) -> float:
        weighted_sum = 0.0
        total = 0.0
        for bid, est in estimates.items():
            center = self._bucket_center(buckets[bid])
            weighted_sum += est * center
            total += est
        if total <= 0:
            centers = [self._bucket_center(rng) for rng in buckets.values()]
            return sum(centers) / len(centers) if centers else 0.0
        return weighted_sum / total

    def _get_tag_value(self, tag: Tag):
        if hasattr(tag, "price"):
            return getattr(tag, "price")
        if hasattr(tag, "value"):
            return getattr(tag, "value")
        return None

    def _bit_len_safe(self, x: int) -> int:
        x = int(x)
        if x <= 0:
            return 1
        return x.bit_length()

    def _range_bits_cost(self, selected_items: List[Tuple[int, Tuple[int, int]]]) -> int:
        cost = 0
        for _, (lo, hi) in selected_items:
            cost += self._bit_len_safe(lo) + self._bit_len_safe(hi)
        return cost

    def _clamp_power_friendly(self, x: int, lo: int, hi: int) -> int:
        x = max(lo, min(hi, x))
        candidates = [8, 16, 32, 64]
        best = candidates[0]
        best_dist = abs(candidates[0] - x)
        for c in candidates[1:]:
            d = abs(c - x)
            if d < best_dist:
                best = c
                best_dist = d
        return max(lo, min(hi, best))

    def _calculate_total_time(self) -> float:
        t_reader = self.total_bits_sent_by_reader * 0.025
        t_tag = self.stage_slot_bit_cost * 0.4
        t_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0.0
        return t_reader + t_tag + t_overhead
