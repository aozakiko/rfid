import math
from typing import List, Tuple, Dict, Set, Any

from base import Tag, IProtocol, generate_random_seed
try:
    from hash import simple_hash
except Exception:
    from base import simple_hash


class web_auto_iter_02(IProtocol):
    def __init__(self):
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self._slot_bit_volume = 0

        self.max_frame_size = 32768
        self.max_bits_per_slot = 256

        self.stage_frame_sizes = []
        self.stage_bits_per_slot = []

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        self._reset_metrics()

        if price_min > price_max:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 0,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "status": "invalid_range",
            }
            return [], 0, 0.0, verification_data

        # 协议超参数
        initial_filter_frame_size = 128
        min_filter_frame_size = 64
        max_filter_rounds = 4
        num_filter_hashes = 2
        candidate_keep_cap = 48
        density_high = 0.82
        density_low = 0.22

        initial_bucket_size = max(1, int(math.sqrt(max(1, price_max - price_min + 1))))
        initial_bucket_size = min(initial_bucket_size, 128)

        verify_frame_size = 192
        verify_frame_size = min(verify_frame_size, self.max_frame_size)
        verify_batch_size = 24

        recheck_frame_size = 256
        recheck_frame_size = min(recheck_frame_size, self.max_frame_size)

        confidence_margin = 1
        early_stop_gap = 2
        max_final_candidates = 6

        # --- 交叉部分：融合父代 A 的“分桶粗筛 + 局部精化”思想与父代 B 的“自适应过滤 + 碰撞评分 + 分批验证”思想 ---
        # 阶段1：按值域分桶，用多轮匿名帧进行粗筛，并根据碰撞/占用密度自适应调整帧长
        buckets = self._create_buckets(price_min, price_max, initial_bucket_size)
        active_bucket_ids: Set[int] = set(buckets.keys())
        bucket_collision_scores: Dict[int, int] = {bid: 0 for bid in buckets}
        bucket_presence_scores: Dict[int, int] = {bid: 0 for bid in buckets}

        current_filter_frame_size = initial_filter_frame_size
        filter_rounds_done = 0

        while len(active_bucket_ids) > candidate_keep_cap and filter_rounds_done < max_filter_rounds:
            seeds = [generate_random_seed() for _ in range(num_filter_hashes)]
            frame_states = self._simulate_tag_bucket_filter_response(
                tags=tags,
                buckets=buckets,
                active_bucket_ids=active_bucket_ids,
                seeds=seeds,
                frame_size=current_filter_frame_size,
            )
            self._record_frame(
                frame_size=current_filter_frame_size,
                bits_per_slot=1,
                reader_bits=self._bits_for_int(current_filter_frame_size) +
                            sum(self._bits_for_int(s) for s in seeds) +
                            num_filter_hashes * (self._bits_for_int(price_min) + self._bits_for_int(price_max) + self._bits_for_int(initial_bucket_size)),
                total_slots=current_filter_frame_size * num_filter_hashes
            )

            next_active: Set[int] = set()
            for bid in active_bucket_ids:
                survived = True
                local_collisions = 0
                local_presences = 0
                for i, seed in enumerate(seeds):
                    slot_id = simple_hash((bid << 16) ^ seed, seed) % current_filter_frame_size
                    count = frame_states[i][slot_id]
                    if count == 0:
                        survived = False
                        break
                    local_presences += 1
                    if count > 1:
                        local_collisions += 1
                if survived:
                    next_active.add(bid)
                    bucket_collision_scores[bid] += local_collisions
                    bucket_presence_scores[bid] += local_presences

            if not next_active:
                break

            active_bucket_ids = next_active
            filter_rounds_done += 1

            occupied = sum(1 for frame in frame_states for c in frame if c > 0)
            total_slots = current_filter_frame_size * num_filter_hashes
            density = occupied / total_slots if total_slots else 0.0

            # --- 突变部分：引入“密度驱动 + 候选规模反馈”的双因子动态帧长控制全新机制 ---
            if density > density_high or len(active_bucket_ids) > candidate_keep_cap * 2:
                current_filter_frame_size = min(self.max_frame_size, current_filter_frame_size * 2)
            elif density < density_low and current_filter_frame_size > min_filter_frame_size:
                current_filter_frame_size = max(min_filter_frame_size, current_filter_frame_size // 2)

        if not active_bucket_ids:
            verification_data = {
                "frame_size": current_filter_frame_size,
                "bits_per_slot": 1,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "total_frames": self.total_frames,
                "bucket_size": initial_bucket_size,
                "filter_rounds_done": filter_rounds_done,
                "status": "no_bucket_candidate",
            }
            return [], 0, self._calculate_total_time(), verification_data

        prioritized_buckets = sorted(
            list(active_bucket_ids),
            key=lambda bid: (bucket_collision_scores.get(bid, 0), bucket_presence_scores.get(bid, 0), -bid),
            reverse=True
        )

        # 阶段2：在候选桶内展开为候选值，并以位图批验证进行打分
        candidate_prices: List[int] = []
        for bid in prioritized_buckets:
            lo, hi = buckets[bid]
            for p in range(lo, hi + 1):
                candidate_prices.append(p)

        if not candidate_prices:
            verification_data = {
                "frame_size": current_filter_frame_size,
                "bits_per_slot": 1,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "total_frames": self.total_frames,
                "bucket_size": initial_bucket_size,
                "filter_rounds_done": filter_rounds_done,
                "status": "empty_candidate_prices",
            }
            return [], 0, self._calculate_total_time(), verification_data

        value_scores: Dict[int, int] = {}
        batch_records = []
        verification_batches = [
            candidate_prices[i:i + verify_batch_size]
            for i in range(0, len(candidate_prices), verify_batch_size)
        ]

        for batch in verification_batches:
            if not batch:
                continue
            bits_per_slot = min(len(batch), self.max_bits_per_slot)
            batch = batch[:bits_per_slot]
            seed = generate_random_seed()

            slot_bitmaps = self._simulate_tag_value_bitmap_response(
                tags=tags,
                candidate_values=batch,
                seed=seed,
                frame_size=verify_frame_size,
            )

            self._record_frame(
                frame_size=verify_frame_size,
                bits_per_slot=bits_per_slot,
                reader_bits=self._bits_for_int(verify_frame_size) +
                            self._bits_for_int(seed) +
                            sum(self._bits_for_int(v) for v in batch),
                total_slots=verify_frame_size
            )

            batch_score_map = {}
            for bit_pos, value in enumerate(batch):
                score = 0
                for slot_data in slot_bitmaps:
                    score += (slot_data >> bit_pos) & 1
                if score > 0:
                    value_scores[value] = value_scores.get(value, 0) + score
                batch_score_map[value] = score

            batch_records.append({
                "batch_size": len(batch),
                "bits_per_slot": bits_per_slot,
                "frame_size": verify_frame_size,
                "scores": batch_score_map,
            })

            # --- 突变部分：引入“保守早停 + 候选桶重检”联动机制 ---
            if len(value_scores) >= 2:
                sorted_items = sorted(value_scores.items(), key=lambda x: x[1], reverse=True)
                top_score = sorted_items[0][1]
                second_score = sorted_items[1][1]
                if top_score >= 4 and top_score >= second_score * early_stop_gap:
                    break

        if not value_scores:
            verification_data = {
                "frame_size": verify_frame_size,
                "bits_per_slot": min(verify_batch_size, self.max_bits_per_slot),
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "total_frames": self.total_frames,
                "bucket_size": initial_bucket_size,
                "filter_rounds_done": filter_rounds_done,
                "active_bucket_ids": prioritized_buckets,
                "batch_records": batch_records,
                "status": "no_value_score",
            }
            return [], 0, self._calculate_total_time(), verification_data

        sorted_value_scores = sorted(value_scores.items(), key=lambda x: (-x[1], x[0]))
        top_score = sorted_value_scores[0][1]
        tentative_candidates = [v for v, s in sorted_value_scores if s >= top_score - confidence_margin]
        tentative_candidates = tentative_candidates[:max_final_candidates]

        # 阶段3：对少量领先候选执行独立重检，降低位图同槽伪并列
        final_counts = {}
        recheck_details = []
        for value in tentative_candidates:
            seed = generate_random_seed()
            slot_counts = self._simulate_tag_single_value_count_response(
                tags=tags,
                target_value=value,
                seed=seed,
                frame_size=recheck_frame_size
            )

            self._record_frame(
                frame_size=recheck_frame_size,
                bits_per_slot=1,
                reader_bits=self._bits_for_int(recheck_frame_size) +
                            self._bits_for_int(seed) +
                            self._bits_for_int(value),
                total_slots=recheck_frame_size
            )

            non_empty = sum(1 for c in slot_counts if c > 0)
            collisions = sum(1 for c in slot_counts if c > 1)
            zeros = recheck_frame_size - non_empty

            if zeros == 0:
                estimate = recheck_frame_size + collisions
            else:
                estimate = int(round(-recheck_frame_size * math.log(max(1e-9, zeros / recheck_frame_size))))
                if collisions > recheck_frame_size * 0.1:
                    estimate += collisions // 2

            final_counts[value] = max(estimate, 0)
            recheck_details.append({
                "value": value,
                "non_empty_slots": non_empty,
                "collision_slots": collisions,
                "estimated_count": final_counts[value],
            })

        if final_counts:
            max_metric = max(final_counts.values())
            estimated_values = sorted([v for v, c in final_counts.items() if c == max_metric])
            metric = max_metric
        else:
            max_bitmap_score = max(value_scores.values())
            estimated_values = sorted([v for v, s in value_scores.items() if s == max_bitmap_score])[:max_final_candidates]
            metric = max_bitmap_score

        total_time_ms = self._calculate_total_time()
        verification_data = {
            "frame_size": verify_frame_size,
            "bits_per_slot": min(verify_batch_size, self.max_bits_per_slot),
            "stage_frame_sizes": self.stage_frame_sizes,
            "stage_bits_per_slot": self.stage_bits_per_slot,
            "total_frames": self.total_frames,
            "bucket_size": initial_bucket_size,
            "filter_rounds_done": filter_rounds_done,
            "active_bucket_ids": prioritized_buckets,
            "bucket_collision_scores": {bid: bucket_collision_scores[bid] for bid in prioritized_buckets[:min(len(prioritized_buckets), 16)]},
            "candidate_count": len(candidate_prices),
            "value_scores_top": sorted_value_scores[:min(len(sorted_value_scores), 12)],
            "batch_records_count": len(batch_records),
            "recheck_frame_size": recheck_frame_size,
            "recheck_details": recheck_details,
        }
        return estimated_values, metric, total_time_ms, verification_data

    def _simulate_tag_bucket_filter_response(
        self,
        tags: List[Tag],
        buckets: Dict[int, Tuple[int, int]],
        active_bucket_ids: Set[int],
        seeds: List[int],
        frame_size: int
    ) -> List[List[int]]:
        frames = [[0] * frame_size for _ in seeds]
        price_to_bucket = {}
        for bid in active_bucket_ids:
            lo, hi = buckets[bid]
            for p in range(lo, hi + 1):
                price_to_bucket[p] = bid

        for tag in tags:
            tag_price = getattr(tag, "price", None)
            if tag_price is None:
                continue
            bid = price_to_bucket.get(tag_price)
            if bid is None:
                continue
            for i, seed in enumerate(seeds):
                slot_id = simple_hash((bid << 16) ^ seed, seed) % frame_size
                frames[i][slot_id] += 1
        return frames

    def _simulate_tag_value_bitmap_response(
        self,
        tags: List[Tag],
        candidate_values: List[int],
        seed: int,
        frame_size: int
    ) -> List[int]:
        value_to_pos = {v: i for i, v in enumerate(candidate_values)}
        slots = [0] * frame_size
        for tag in tags:
            tag_price = getattr(tag, "price", None)
            if tag_price not in value_to_pos:
                continue
            slot_id = simple_hash(getattr(tag, "id", 0), seed) % frame_size
            bit_pos = value_to_pos[tag_price]
            slots[slot_id] |= (1 << bit_pos)
        return slots

    def _simulate_tag_single_value_count_response(
        self,
        tags: List[Tag],
        target_value: int,
        seed: int,
        frame_size: int
    ) -> List[int]:
        slots = [0] * frame_size
        for tag in tags:
            tag_price = getattr(tag, "price", None)
            if tag_price != target_value:
                continue
            slot_id = simple_hash(getattr(tag, "id", 0), seed) % frame_size
            slots[slot_id] += 1
        return slots

    def _create_buckets(self, price_min: int, price_max: int, bucket_size: int) -> Dict[int, Tuple[int, int]]:
        buckets = {}
        bid = 0
        start = price_min
        while start <= price_max:
            end = min(start + bucket_size - 1, price_max)
            buckets[bid] = (start, end)
            start = end + 1
            bid += 1
        return buckets

    def _record_frame(self, frame_size: int, bits_per_slot: int, reader_bits: int, total_slots: int) -> None:
        frame_size = min(max(1, frame_size), self.max_frame_size)
        bits_per_slot = min(max(1, bits_per_slot), self.max_bits_per_slot)
        self.total_frames += 1
        self.total_bits_sent_by_reader += max(0, reader_bits)
        self.total_slots += max(0, total_slots)
        self._slot_bit_volume += max(0, total_slots) * bits_per_slot
        self.stage_frame_sizes.append(frame_size)
        self.stage_bits_per_slot.append(bits_per_slot)

    def _calculate_total_time(self) -> float:
        T_reader = self.total_bits_sent_by_reader * 0.025
        T_tag = self._slot_bit_volume * 0.4
        T_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0
        return T_reader + T_tag + T_overhead

    def _bits_for_int(self, x: Any) -> int:
        try:
            xi = int(x)
        except Exception:
            xi = 0
        if xi == 0:
            return 1
        return abs(xi).bit_length()

    def _reset_metrics(self) -> None:
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self._slot_bit_volume = 0
        self.stage_frame_sizes = []
        self.stage_bits_per_slot = []
