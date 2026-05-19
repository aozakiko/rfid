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
        self.last_frame_size = 1

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.max_bits_per_slot_used = 1
        self.last_frame_size = 1

        # 参数设计：兼顾100000标签与价格1..1000的多众数场景
        COARSE_BUCKET_SIZE = 25
        COARSE_FRAME_SIZE = 512
        COARSE_BITS_PER_SLOT = 40  # <= 256
        FINE_FRAME_SIZE = 2048
        FINE_BITS_PER_SLOT = 32    # <= 256
        VERIFY_FRAME_SIZE = 4096
        VERIFY_BITS_PER_SLOT = 16  # <= 256

        all_prices = list(range(price_min, price_max + 1))
        buckets = self._create_price_buckets(price_min, price_max, COARSE_BUCKET_SIZE)

        # --- 交叉部分：融合父代A的“范围/桶扫描与分治聚焦”思想与父代B的“并行批量位向量估算”思想 ---
        # 阶段1：桶级并行粗扫，快速锁定热门价格区间
        bucket_estimates = self._estimate_buckets_parallel(tags, buckets, COARSE_FRAME_SIZE, COARSE_BITS_PER_SLOT)

        if not bucket_estimates:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": self.last_frame_size,
                "bits_per_slot": self.max_bits_per_slot_used,
                "stage_frame_sizes": {
                    "coarse_bucket": COARSE_FRAME_SIZE,
                    "fine_price": FINE_FRAME_SIZE,
                    "verify": VERIFY_FRAME_SIZE,
                },
                "stage_bits_per_slot": {
                    "coarse_bucket": COARSE_BITS_PER_SLOT,
                    "fine_price": FINE_BITS_PER_SLOT,
                    "verify": VERIFY_BITS_PER_SLOT,
                },
                "bucket_estimates": {},
                "status": "empty_bucket_estimates",
            }
            return [], 0, total_time_ms, verification_data

        sorted_buckets = sorted(bucket_estimates.items(), key=lambda kv: kv[1], reverse=True)
        top_bucket_value = sorted_buckets[0][1]

        candidate_bucket_ids = []
        for bid, est in sorted_buckets:
            if est <= 0:
                continue
            if est >= top_bucket_value * 0.90:
                candidate_bucket_ids.append(bid)
            if len(candidate_bucket_ids) >= 4:
                break
        if not candidate_bucket_ids:
            candidate_bucket_ids = [sorted_buckets[0][0]]

        # 从候选桶中展开价格列表
        candidate_prices = []
        for bid in candidate_bucket_ids:
            lo, hi = buckets[bid]
            candidate_prices.extend(range(lo, hi + 1))
        candidate_prices = sorted(set([p for p in candidate_prices if price_min <= p <= price_max]))

        # 阶段2：候选价格并行精估
        fine_estimates = self._estimate_prices_parallel(tags, candidate_prices, FINE_FRAME_SIZE, FINE_BITS_PER_SLOT)

        if not fine_estimates:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": self.last_frame_size,
                "bits_per_slot": self.max_bits_per_slot_used,
                "stage_frame_sizes": {
                    "coarse_bucket": COARSE_FRAME_SIZE,
                    "fine_price": FINE_FRAME_SIZE,
                    "verify": VERIFY_FRAME_SIZE,
                },
                "stage_bits_per_slot": {
                    "coarse_bucket": COARSE_BITS_PER_SLOT,
                    "fine_price": FINE_BITS_PER_SLOT,
                    "verify": VERIFY_BITS_PER_SLOT,
                },
                "bucket_estimates": bucket_estimates,
                "candidate_bucket_ids": candidate_bucket_ids,
                "candidate_prices": candidate_prices,
                "status": "empty_fine_estimates",
            }
            return [], 0, total_time_ms, verification_data

        sorted_fine = sorted(fine_estimates.items(), key=lambda kv: kv[1], reverse=True)
        top_price_est = sorted_fine[0][1]

        # --- 突变部分：引入“邻域一致性峰值筛选 + 早停验证”全新机制 ---
        # 新机制1：邻域一致性峰值筛选
        # 不是直接取估值最高若干价格，而是偏向于局部稳定峰值，减少由哈希波动导致的误判。
        neighborhood_scores = {}
        fine_map = {p: fine_estimates.get(p, 0.0) for p in candidate_prices}
        for p in candidate_prices:
            left_v = fine_map.get(p - 1, 0.0)
            mid_v = fine_map.get(p, 0.0)
            right_v = fine_map.get(p + 1, 0.0)
            neighborhood_scores[p] = 0.5 * mid_v + 0.25 * left_v + 0.25 * right_v

        sorted_neighborhood = sorted(neighborhood_scores.items(), key=lambda kv: kv[1], reverse=True)

        verify_candidates = []
        for p, score in sorted_neighborhood:
            if fine_map.get(p, 0.0) <= 0:
                continue
            if fine_map.get(p, 0.0) >= top_price_est * 0.88 or score >= sorted_neighborhood[0][1] * 0.90:
                verify_candidates.append(p)
            if len(verify_candidates) >= 6:
                break
        if not verify_candidates:
            verify_candidates = [sorted_fine[0][0]]

        # 新机制2：早停验证
        # 对验证候选做分组计数估计；若某组明显领先，提前终止后续验证，降低时间开销。
        verify_counts = self._verify_candidates_grouped(tags, verify_candidates, VERIFY_FRAME_SIZE, VERIFY_BITS_PER_SLOT)

        if not verify_counts:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": self.last_frame_size,
                "bits_per_slot": self.max_bits_per_slot_used,
                "stage_frame_sizes": {
                    "coarse_bucket": COARSE_FRAME_SIZE,
                    "fine_price": FINE_FRAME_SIZE,
                    "verify": VERIFY_FRAME_SIZE,
                },
                "stage_bits_per_slot": {
                    "coarse_bucket": COARSE_BITS_PER_SLOT,
                    "fine_price": FINE_BITS_PER_SLOT,
                    "verify": VERIFY_BITS_PER_SLOT,
                },
                "bucket_estimates": bucket_estimates,
                "candidate_bucket_ids": candidate_bucket_ids,
                "candidate_prices": candidate_prices,
                "fine_estimates": fine_estimates,
                "verify_candidates": verify_candidates,
                "status": "empty_verify_counts",
            }
            return [], 0, total_time_ms, verification_data

        best_price = max(verify_counts.items(), key=lambda kv: kv[1])[0]
        best_count = verify_counts[best_price]

        modes = [best_price] if best_count > 0 else []

        total_time_ms = self._calculate_total_time()
        verification_data = {
            "frame_size": self.last_frame_size,
            "bits_per_slot": self.max_bits_per_slot_used,
            "stage_frame_sizes": {
                "coarse_bucket": COARSE_FRAME_SIZE,
                "fine_price": FINE_FRAME_SIZE,
                "verify": VERIFY_FRAME_SIZE,
            },
            "stage_bits_per_slot": {
                "coarse_bucket": COARSE_BITS_PER_SLOT,
                "fine_price": FINE_BITS_PER_SLOT,
                "verify": VERIFY_BITS_PER_SLOT,
            },
            "bucket_estimates": bucket_estimates,
            "candidate_bucket_ids": candidate_bucket_ids,
            "candidate_prices": candidate_prices,
            "fine_estimates": fine_estimates,
            "neighborhood_scores": {k: neighborhood_scores[k] for k in verify_candidates if k in neighborhood_scores},
            "verify_candidates": verify_candidates,
            "verify_counts": verify_counts,
            "total_frames": self.total_frames,
            "total_bits_sent_by_reader": self.total_bits_sent_by_reader,
            "total_slots": self.total_slots,
        }
        return modes, int(math.ceil(best_count)) if best_count != float("inf") else int(10**9), total_time_ms, verification_data

    def _estimate_buckets_parallel(
        self,
        tags: List[Tag],
        buckets: Dict[int, Tuple[int, int]],
        frame_size: int,
        bits_per_slot: int,
    ) -> Dict[int, float]:
        bucket_ids = sorted(buckets.keys())
        estimates = {}

        for i in range(0, len(bucket_ids), bits_per_slot):
            batch_ids = bucket_ids[i:i + bits_per_slot]
            if not batch_ids:
                continue

            seed = generate_random_seed()
            self._record_frame(
                frame_size=frame_size,
                bits_per_slot=bits_per_slot,
                reader_bits=seed.bit_length() + frame_size.bit_length() + len(batch_ids).bit_length() + 24
            )

            frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
            batch_map = {bid: idx for idx, bid in enumerate(batch_ids)}

            for t in tags:
                matched_bid = None
                for bid in batch_ids:
                    lo, hi = buckets[bid]
                    if lo <= t.price <= hi:
                        matched_bid = bid
                        break
                if matched_bid is None:
                    continue
                slot_id = simple_hash(t.id, seed) % frame_size
                bit_pos = batch_map[matched_bid]
                frame.aggregate(slot_id, 1 << bit_pos)

            for bid, bit_pos in batch_map.items():
                logical_ones = 0
                for slot_data in frame.slots:
                    logical_ones += ((slot_data >> bit_pos) & 1)
                zeros = frame_size - logical_ones
                if zeros == frame_size:
                    estimates[bid] = 0.0
                elif zeros == 0:
                    estimates[bid] = float(frame_size * 4)
                else:
                    estimates[bid] = -frame_size * math.log(zeros / frame_size)
        return estimates

    def _estimate_prices_parallel(
        self,
        tags: List[Tag],
        prices: List[int],
        frame_size: int,
        bits_per_slot: int,
    ) -> Dict[int, float]:
        estimates = {}

        for i in range(0, len(prices), bits_per_slot):
            batch = prices[i:i + bits_per_slot]
            if not batch:
                continue

            seed = generate_random_seed()
            self._record_frame(
                frame_size=frame_size,
                bits_per_slot=bits_per_slot,
                reader_bits=seed.bit_length() + frame_size.bit_length() + len(batch).bit_length() + 32
            )

            frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
            price_to_bit = {p: idx for idx, p in enumerate(batch)}
            batch_set = set(batch)

            for t in tags:
                if t.price not in batch_set:
                    continue
                slot_id = simple_hash(t.id, seed) % frame_size
                bit_pos = price_to_bit[t.price]
                frame.aggregate(slot_id, 1 << bit_pos)

            for p, bit_pos in price_to_bit.items():
                logical_ones = 0
                for slot_data in frame.slots:
                    logical_ones += ((slot_data >> bit_pos) & 1)
                zeros = frame_size - logical_ones
                if zeros == frame_size:
                    estimates[p] = 0.0
                elif zeros == 0:
                    estimates[p] = float(frame_size * 4)
                else:
                    estimates[p] = -frame_size * math.log(zeros / frame_size)

        return estimates

    def _verify_candidates_grouped(
        self,
        tags: List[Tag],
        candidates: List[int],
        frame_size: int,
        bits_per_slot: int,
    ) -> Dict[int, float]:
        results = {}
        groups = [candidates[i:i + bits_per_slot] for i in range(0, len(candidates), bits_per_slot)]

        for gi, group in enumerate(groups):
            if not group:
                continue

            seed = generate_random_seed()
            self._record_frame(
                frame_size=frame_size,
                bits_per_slot=bits_per_slot,
                reader_bits=seed.bit_length() + frame_size.bit_length() + len(group).bit_length() + 40
            )

            frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
            pos_map = {p: idx for idx, p in enumerate(group)}
            group_set = set(group)

            for t in tags:
                if t.price not in group_set:
                    continue
                slot_id = simple_hash(t.id, seed) % frame_size
                bit_pos = pos_map[t.price]
                frame.aggregate(slot_id, 1 << bit_pos)

            for p, bit_pos in pos_map.items():
                logical_ones = 0
                for slot_data in frame.slots:
                    logical_ones += ((slot_data >> bit_pos) & 1)
                zeros = frame_size - logical_ones
                if zeros == frame_size:
                    results[p] = 0.0
                elif zeros == 0:
                    results[p] = float(frame_size * 4)
                else:
                    results[p] = -frame_size * math.log(zeros / frame_size)

            # 早停：若当前已出现明显领先者且后续组的理论上限难以反超，则提前结束
            current_sorted = sorted(results.items(), key=lambda kv: kv[1], reverse=True)
            if len(current_sorted) >= 2:
                first_v = current_sorted[0][1]
                second_v = current_sorted[1][1]
                if first_v > 0 and second_v >= 0 and first_v >= second_v * 1.25 and gi < len(groups) - 1:
                    break

        return results

    def _create_price_buckets(self, price_min: int, price_max: int, bucket_size: int) -> Dict[int, Tuple[int, int]]:
        buckets = {}
        bid = 0
        p = price_min
        while p <= price_max:
            buckets[bid] = (p, min(price_max, p + bucket_size - 1))
            p += bucket_size
            bid += 1
        return buckets

    def _record_frame(self, frame_size: int, bits_per_slot: int, reader_bits: int) -> None:
        self.total_frames += 1
        self.total_bits_sent_by_reader += reader_bits
        self.total_slots += frame_size
        self.max_bits_per_slot_used = max(self.max_bits_per_slot_used, bits_per_slot)
        self.last_frame_size = frame_size

    def _calculate_total_time(self) -> float:
        T_reader = self.total_bits_sent_by_reader * 0.025
        T_tag = self.total_slots * self.max_bits_per_slot_used * 0.4
        T_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0
        return T_reader + T_tag + T_overhead
