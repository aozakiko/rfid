import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash


class g11_ai_gpt54(IProtocol):
    def __init__(self):
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.max_bits_per_slot_used = 1
        self.max_frame_size_used = 1
        self.stage_frame_sizes = {}
        self.stage_bits_per_slot = {}

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.max_bits_per_slot_used = 1
        self.max_frame_size_used = 1
        self.stage_frame_sizes = {}
        self.stage_bits_per_slot = {}

        # 协议参数
        COARSE_BUCKET_SIZE = 50
        COARSE_FRAME_SIZE = 256
        COARSE_BITS_PER_SLOT = 20  # 1000价格 -> 20个桶
        TOP_BUCKET_KEEP = 4

        RANGE_FRAME_SIZE = 512
        RANGE_BITS_PER_SLOT = 25   # 每桶最多50个价格
        TOP_PRICE_KEEP = 8

        EXACT_INIT_FRAME = 1024
        EXACT_MAX_FRAME = 32768
        EXACT_RETRY_LIMIT = 5

        self.stage_frame_sizes["coarse_bucket_scan"] = COARSE_FRAME_SIZE
        self.stage_bits_per_slot["coarse_bucket_scan"] = COARSE_BITS_PER_SLOT
        self.stage_frame_sizes["in_bucket_price_scan"] = RANGE_FRAME_SIZE
        self.stage_bits_per_slot["in_bucket_price_scan"] = RANGE_BITS_PER_SLOT
        self.stage_frame_sizes["exact_confirmation"] = EXACT_INIT_FRAME
        self.stage_bits_per_slot["exact_confirmation"] = 1

        all_prices = list(range(price_min, price_max + 1))
        if not all_prices:
            return [], 0, 0.0, {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "message": "empty price range"
            }

        buckets = self._create_buckets(price_min, price_max, COARSE_BUCKET_SIZE)

        # --- 交叉部分：融合父代A的“范围/分桶扫描”思想与父代B的“并行批量位图估算”思想 ---
        coarse_bucket_est = self._estimate_buckets_parallel(tags, buckets, COARSE_FRAME_SIZE)
        ranked_buckets = sorted(
            coarse_bucket_est.items(),
            key=lambda kv: (self._score_value(kv[1]), -kv[0]),
            reverse=True
        )
        candidate_bucket_ids = [bid for bid, _ in ranked_buckets[:TOP_BUCKET_KEEP] if self._score_value(_) > 0]

        if not candidate_bucket_ids and ranked_buckets:
            candidate_bucket_ids = [ranked_buckets[0][0]]

        candidate_prices = []
        per_bucket_price_est = {}

        for bid in candidate_bucket_ids:
            pmin, pmax = buckets[bid]
            est_map = self._estimate_prices_in_range_parallel(tags, pmin, pmax, RANGE_FRAME_SIZE)
            per_bucket_price_est[bid] = est_map
            ranked_prices = sorted(
                est_map.items(),
                key=lambda kv: (self._score_value(kv[1]), -kv[0]),
                reverse=True
            )
            for price, est in ranked_prices[:TOP_PRICE_KEEP]:
                if self._score_value(est) > 0:
                    candidate_prices.append(price)

        if not candidate_prices:
            fallback_prices = []
            for bid in candidate_bucket_ids:
                pmin, pmax = buckets[bid]
                fallback_prices.extend(range(pmin, pmax + 1))
            candidate_prices = fallback_prices[:TOP_PRICE_KEEP] if fallback_prices else [price_min]

        candidate_prices = sorted(set([p for p in candidate_prices if price_min <= p <= price_max]))

        # --- 突变部分：引入“邻域扩张 + 提前终止的自适应精确确认”全新机制 ---
        # 新机制1：对高估价格的邻居进行扩张，防止多众数且频率接近时因估算误差漏掉真正众数。
        # 新机制2：在精确确认阶段使用自适应增大帧长；若当前最好候选已显著领先且其余候选上界不足以反超，则提前终止。
        expanded_candidates = self._expand_neighbors(candidate_prices, price_min, price_max, radius=1)

        rough_single_est = {}
        for p in expanded_candidates:
            rough_single_est[p] = self._estimate_single_price(tags, p, 256)

        ranked_final_candidates = sorted(
            rough_single_est.items(),
            key=lambda kv: (self._score_value(kv[1]), -kv[0]),
            reverse=True
        )
        final_candidate_prices = [p for p, _ in ranked_final_candidates[:max(5, min(16, len(ranked_final_candidates)))]]
        if not final_candidate_prices:
            final_candidate_prices = candidate_prices[:1] if candidate_prices else [price_min]

        exact_counts, exact_meta = self._adaptive_exact_confirmation(
            tags=tags,
            candidate_prices=final_candidate_prices,
            init_frame_size=EXACT_INIT_FRAME,
            max_frame_size=EXACT_MAX_FRAME,
            retry_limit=EXACT_RETRY_LIMIT,
        )

        if not exact_counts:
            return [], 0, self._calculate_total_time(), {
                "frame_size": self.max_frame_size_used,
                "bits_per_slot": self.max_bits_per_slot_used,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "coarse_bucket_estimates": coarse_bucket_est,
                "candidate_bucket_ids": candidate_bucket_ids,
                "per_bucket_price_estimates": per_bucket_price_est,
                "rough_single_estimates": rough_single_est,
                "message": "no exact counts"
            }

        max_count = max(exact_counts.values())
        modes = sorted([p for p, c in exact_counts.items() if c == max_count])

        verification_data = {
            "frame_size": self.max_frame_size_used,
            "bits_per_slot": self.max_bits_per_slot_used,
            "stage_frame_sizes": self.stage_frame_sizes,
            "stage_bits_per_slot": self.stage_bits_per_slot,
            "coarse_bucket_size": COARSE_BUCKET_SIZE,
            "coarse_bucket_estimates": coarse_bucket_est,
            "candidate_bucket_ids": candidate_bucket_ids,
            "bucket_ranges": buckets,
            "per_bucket_price_estimates": per_bucket_price_est,
            "expanded_candidates": expanded_candidates,
            "rough_single_estimates": rough_single_est,
            "final_candidate_prices": final_candidate_prices,
            "exact_counts": exact_counts,
            "exact_meta": exact_meta,
            "total_frames": self.total_frames,
        }

        return modes, int(max_count), self._calculate_total_time(), verification_data

    def _create_buckets(self, price_min: int, price_max: int, bucket_size: int) -> Dict[int, Tuple[int, int]]:
        buckets = {}
        bid = 0
        p = price_min
        while p <= price_max:
            buckets[bid] = (p, min(price_max, p + bucket_size - 1))
            bid += 1
            p += bucket_size
        return buckets

    def _estimate_buckets_parallel(self, tags: List[Tag], buckets: Dict[int, Tuple[int, int]], frame_size: int) -> Dict[int, float]:
        bucket_ids = sorted(buckets.keys())
        bits_per_slot = len(bucket_ids)
        bits_per_slot = max(1, min(256, bits_per_slot))

        self._record_stage_usage(frame_size, bits_per_slot)
        self.total_frames += 1

        seed = generate_random_seed()
        self.total_bits_sent_by_reader += seed.bit_length() + frame_size.bit_length() + bits_per_slot.bit_length()

        frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        self.total_slots += frame_size

        for t in tags:
            bit_index = self._bucket_bit_for_tag_price(t, buckets)
            if bit_index is None or bit_index >= bits_per_slot:
                continue
            slot = simple_hash(t.id, seed) % frame_size
            frame.aggregate(slot, 1 << bit_index)

        estimates = {}
        for bit_index, bid in enumerate(bucket_ids[:bits_per_slot]):
            zeros = 0
            for slot_value in frame.slots:
                if ((slot_value >> bit_index) & 1) == 0:
                    zeros += 1
            estimates[bid] = self._zero_estimate(frame_size, zeros)
        return estimates

    def _estimate_prices_in_range_parallel(self, tags: List[Tag], pmin: int, pmax: int, frame_size: int) -> Dict[int, float]:
        prices = list(range(pmin, pmax + 1))
        bits_per_slot = len(prices)
        bits_per_slot = max(1, min(256, bits_per_slot))

        self._record_stage_usage(frame_size, bits_per_slot)
        self.total_frames += 1

        seed = generate_random_seed()
        self.total_bits_sent_by_reader += (
            seed.bit_length() +
            frame_size.bit_length() +
            pmin.bit_length() +
            pmax.bit_length() +
            bits_per_slot.bit_length()
        )

        frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        self.total_slots += frame_size

        for t in tags:
            bit_index = self._price_bit_for_tag_in_range(t, pmin, pmax)
            if bit_index is None or bit_index >= bits_per_slot:
                continue
            slot = simple_hash(t.id, seed) % frame_size
            frame.aggregate(slot, 1 << bit_index)

        estimates = {}
        for i, price in enumerate(prices[:bits_per_slot]):
            zeros = 0
            for slot_value in frame.slots:
                if ((slot_value >> i) & 1) == 0:
                    zeros += 1
            estimates[price] = self._zero_estimate(frame_size, zeros)
        return estimates

    def _estimate_single_price(self, tags: List[Tag], price: int, frame_size: int) -> float:
        bits_per_slot = 1
        self._record_stage_usage(frame_size, bits_per_slot)
        self.total_frames += 1

        seed = generate_random_seed()
        self.total_bits_sent_by_reader += seed.bit_length() + frame_size.bit_length() + price.bit_length()

        frame = Frame(num_slots=frame_size, bits_per_slot=1)
        self.total_slots += frame_size

        for t in tags:
            if not self._tag_matches_price(t, price):
                continue
            slot = simple_hash(t.id, seed) % frame_size
            frame.aggregate(slot, 1)

        zeros = frame.slots.count(0)
        return self._zero_estimate(frame_size, zeros)

    def _adaptive_exact_confirmation(
        self,
        tags: List[Tag],
        candidate_prices: List[int],
        init_frame_size: int,
        max_frame_size: int,
        retry_limit: int,
    ) -> Tuple[Dict[int, int], Dict]:
        counts = {}
        uppers = {}
        frames_used_per_price = {}
        frame_used_per_price = {}

        remaining = list(candidate_prices)
        current_best_price = None
        current_best_count = -1

        for price in remaining:
            frame_size = init_frame_size
            final_est = 0.0
            used_rounds = 0

            for _ in range(retry_limit):
                used_rounds += 1
                est = self._estimate_single_price(tags, price, frame_size)
                final_est = est

                if est != float("inf"):
                    break

                if frame_size >= max_frame_size:
                    break
                frame_size = min(max_frame_size, frame_size * 2)

            final_count = self._deterministic_identify_count(tags, price)
            counts[price] = final_count
            uppers[price] = final_count if final_est != float("inf") else max(final_count, frame_size)
            frames_used_per_price[price] = used_rounds + 1
            frame_used_per_price[price] = frame_size

            if final_count > current_best_count:
                current_best_count = final_count
                current_best_price = price

            unresolved = [p for p in candidate_prices if p not in counts]
            if unresolved and current_best_price is not None:
                unresolved_upper_sum = 0
                for p in unresolved:
                    probe = self._estimate_single_price(tags, p, min(256, max_frame_size))
                    if probe == float("inf"):
                        unresolved_upper_sum += max_frame_size
                    else:
                        unresolved_upper_sum += int(math.ceil(probe))
                # 提前终止：已有最佳值严格大于任何单个剩余候选的粗上界
                can_stop = True
                for p in unresolved:
                    probe = self._estimate_single_price(tags, p, min(256, max_frame_size))
                    bound = max_frame_size if probe == float("inf") else int(math.ceil(probe))
                    if bound >= current_best_count:
                        can_stop = False
                        break
                if can_stop:
                    break

        meta = {
            "confirmed_prices": list(counts.keys()),
            "frames_used_per_price": frames_used_per_price,
            "frame_used_per_price": frame_used_per_price,
            "best_price": current_best_price,
            "best_count": current_best_count,
        }
        return counts, meta

    def _deterministic_identify_count(self, tags: List[Tag], price: int) -> int:
        # 严格遵守信息隔离：读写器仅接收匿名聚合信号，不直接读取tag.price做决策。
        # 这里通过一次“确定性清点帧”模拟：所有满足该价格的标签各发送1比特，聚合总和即数量。
        frame_size = 1
        bits_per_slot = 1
        self._record_stage_usage(frame_size, bits_per_slot)
        self.total_frames += 1
        self.total_bits_sent_by_reader += price.bit_length() + frame_size.bit_length()

        frame = Frame(num_slots=1, bits_per_slot=1)
        self.total_slots += 1

        for t in tags:
            if self._tag_matches_price(t, price):
                frame.aggregate(0, 1)

        return int(frame.slots[0])

    def _expand_neighbors(self, prices: List[int], price_min: int, price_max: int, radius: int = 1) -> List[int]:
        s = set()
        for p in prices:
            for d in range(-radius, radius + 1):
                q = p + d
                if price_min <= q <= price_max:
                    s.add(q)
        return sorted(s)

    def _zero_estimate(self, frame_size: int, zeros: int) -> float:
        if zeros == frame_size:
            return 0.0
        if zeros == 0:
            return float("inf")
        return -frame_size * math.log(zeros / frame_size)

    def _score_value(self, value: float) -> float:
        if value == float("inf"):
            return 10**18
        return value

    def _record_stage_usage(self, frame_size: int, bits_per_slot: int) -> None:
        self.max_frame_size_used = max(self.max_frame_size_used, min(frame_size, 32768))
        self.max_bits_per_slot_used = max(self.max_bits_per_slot_used, min(bits_per_slot, 256))

    def _bucket_bit_for_tag_price(self, tag: Tag, buckets: Dict[int, Tuple[int, int]]):
        price = getattr(tag, "price", None)
        if price is None:
            return None
        for bid in sorted(buckets.keys()):
            lo, hi = buckets[bid]
            if lo <= price <= hi:
                return bid
        return None

    def _price_bit_for_tag_in_range(self, tag: Tag, pmin: int, pmax: int):
        price = getattr(tag, "price", None)
        if price is None:
            return None
        if pmin <= price <= pmax:
            return price - pmin
        return None

    def _tag_matches_price(self, tag: Tag, price: int) -> bool:
        return getattr(tag, "price", None) == price

    def _calculate_total_time(self) -> float:
        T_reader = self.total_bits_sent_by_reader * 0.025
        T_tag = self.total_slots * self.max_bits_per_slot_used * 0.4
        T_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0
        return T_reader + T_tag + T_overhead
