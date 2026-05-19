from typing import List, Tuple, Dict, Any
from collections import defaultdict
from base import Tag, IProtocol


class web_mode_query_candidate(IProtocol):
    """
    面向 Web 平台的候选众数查询协议：
    - 以“价格桶时隙帧”进行匿名聚合估计
    - 以“候选集分批验证”进行精确复核
    - 通过自适应帧长、碰撞感知和早停控制通信开销

    输出:
        estimated_values: 候选众数列表
        metric: 估计/验证得到的最高频次
        total_time_ms: 总执行时间
        verification_data: 可解释协议过程数据
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        value_range = max(0, price_max - price_min + 1)

        if value_range <= 0:
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "candidate_prices": [],
                "estimated_bucket_loads": {},
                "verified_counts": {},
                "collision_buckets": [],
                "task_type": "discrete_mode_candidate"
            }
            return [], 0, 0.0, verification_data

        num_tags = len(tags)

        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        stage_frame_sizes = []
        stage_bits_per_slot = []

        # --- 交叉部分：融合父代 A 的逐值精确验证思想与父代 B 的整场盘点/匿名聚合思想 ---
        # 阶段1先对整个价格域执行匿名桶聚合，避免像父代A那样逐价全量精确探测；
        # 阶段2只对少量高疑似候选值执行精确验证，继承父代A的“精确计数”优点；
        # 同时保持读写器只接收聚合帧结果，不直接读取标签私有属性。

        # --- 突变部分：引入自适应帧长 + 候选桶重检 + 早停的全新机制 ---
        # 1) 自适应 frame_size，依据价格域与标签规模调节
        # 2) 若候选桶碰撞严重，则进行二级重检以细化候选
        # 3) 精确验证阶段采用分批验证和早停，减少不必要开销

        frame_size_stage1 = self._choose_frame_size(value_range=value_range, num_tags=num_tags)
        bits_per_slot_stage1 = self._choose_bits_per_slot(value_range=value_range, frame_size=frame_size_stage1)
        stage_frame_sizes.append(frame_size_stage1)
        stage_bits_per_slot.append(bits_per_slot_stage1)

        total_frames += 1
        total_bits_sent_by_reader += self._reader_bits_for_bucket_frame(frame_size_stage1, bits_per_slot_stage1, price_min, price_max)
        total_slots += frame_size_stage1

        bucket_signal_stage1 = self._simulate_tag_bucket_bitmap_response(
            tags=tags,
            price_min=price_min,
            price_max=price_max,
            frame_size=frame_size_stage1,
            bits_per_slot=bits_per_slot_stage1
        )

        bucket_loads_stage1 = self._decode_bucket_loads(bucket_signal_stage1)
        candidate_buckets = self._select_top_buckets(bucket_loads_stage1, top_k=min(4, frame_size_stage1))
        candidate_prices = self._expand_buckets_to_prices(candidate_buckets, price_min, price_max, frame_size_stage1)

        stage2_bucket_loads = {}
        collision_buckets = [b for b in candidate_buckets if bucket_loads_stage1.get(b, 0) >= 3]

        if collision_buckets and len(candidate_prices) > 1:
            sub_price_list = self._expand_buckets_to_prices(collision_buckets, price_min, price_max, frame_size_stage1)
            if sub_price_list:
                sub_min = min(sub_price_list)
                sub_max = max(sub_price_list)
                sub_range = max(1, sub_max - sub_min + 1)

                frame_size_stage2 = self._choose_refine_frame_size(sub_range, len(sub_price_list))
                bits_per_slot_stage2 = self._choose_bits_per_slot(value_range=sub_range, frame_size=frame_size_stage2)

                stage_frame_sizes.append(frame_size_stage2)
                stage_bits_per_slot.append(bits_per_slot_stage2)

                total_frames += 1
                total_bits_sent_by_reader += self._reader_bits_for_bucket_frame(frame_size_stage2, bits_per_slot_stage2, sub_min, sub_max)
                total_slots += frame_size_stage2

                bucket_signal_stage2 = self._simulate_tag_bucket_bitmap_response_filtered_range(
                    tags=tags,
                    active_price_set=set(sub_price_list),
                    sub_min=sub_min,
                    sub_max=sub_max,
                    frame_size=frame_size_stage2,
                    bits_per_slot=bits_per_slot_stage2
                )
                stage2_bucket_loads = self._decode_bucket_loads(bucket_signal_stage2)
                refined_buckets = self._select_top_buckets(stage2_bucket_loads, top_k=min(4, frame_size_stage2))
                refined_prices = self._expand_refined_buckets_to_prices(refined_buckets, sub_min, sub_max, frame_size_stage2)
                candidate_prices = sorted(set([p for p in candidate_prices if p not in sub_price_list] + refined_prices))

        candidate_prices = [p for p in candidate_prices if price_min <= p <= price_max]
        candidate_prices = sorted(set(candidate_prices))

        if not candidate_prices:
            verification_data = {
                "frame_size": frame_size_stage1,
                "bits_per_slot": bits_per_slot_stage1,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "total_frames": total_frames,
                "candidate_prices": [],
                "estimated_bucket_loads": {int(k): int(v) for k, v in bucket_loads_stage1.items()},
                "refined_bucket_loads": {int(k): int(v) for k, v in stage2_bucket_loads.items()},
                "verified_counts": {},
                "collision_buckets": collision_buckets,
                "task_type": "discrete_mode_candidate"
            }
            total_time_ms = self._compute_total_time(
                total_bits_sent_by_reader=total_bits_sent_by_reader,
                total_slots=total_slots,
                bits_per_slot=max(stage_bits_per_slot) if stage_bits_per_slot else 1,
                total_frames=total_frames
            )
            return [], 0, total_time_ms, verification_data

        max_candidates_to_verify = min(6, max(2, len(candidate_prices)))
        candidate_prices = candidate_prices[:max_candidates_to_verify]

        verified_counts = {}
        best_count = -1
        best_prices = []

        verification_batch_size = min(3, len(candidate_prices))
        idx = 0
        remaining_upper_bound = max(num_tags, 0)

        while idx < len(candidate_prices):
            batch = candidate_prices[idx: idx + verification_batch_size]

            batch_frame_size = min(32768, max(1, len(batch)))
            batch_bits_per_slot = min(256, 16)
            stage_frame_sizes.append(batch_frame_size)
            stage_bits_per_slot.append(batch_bits_per_slot)

            total_frames += 1
            total_bits_sent_by_reader += self._reader_bits_for_verify_frame(batch)
            total_slots += batch_frame_size

            batch_counts = self._simulate_tag_exact_count_batch(tags=tags, candidate_prices=batch)

            for price in batch:
                count = int(batch_counts.get(price, 0))
                verified_counts[price] = count
                if count > best_count:
                    best_count = count
                    best_prices = [price]
                elif count == best_count:
                    best_prices.append(price)

            idx += verification_batch_size

            remaining_unverified = len(candidate_prices) - idx
            rough_bucket_ceiling = self._estimate_remaining_upper_bound(
                candidate_prices=candidate_prices[idx:],
                price_min=price_min,
                price_max=price_max,
                frame_size=frame_size_stage1,
                bucket_loads_stage1=bucket_loads_stage1
            )
            remaining_upper_bound = min(num_tags, rough_bucket_ceiling if rough_bucket_ceiling > 0 else num_tags)

            if best_count >= remaining_upper_bound:
                break

        best_prices = sorted(set(best_prices))
        metric = max(best_count, 0)

        verification_data = {
            "frame_size": frame_size_stage1,
            "bits_per_slot": bits_per_slot_stage1,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "total_frames": total_frames,
            "candidate_prices": candidate_prices,
            "estimated_bucket_loads": {int(k): int(v) for k, v in bucket_loads_stage1.items()},
            "refined_bucket_loads": {int(k): int(v) for k, v in stage2_bucket_loads.items()},
            "verified_counts": {int(k): int(v) for k, v in verified_counts.items()},
            "collision_buckets": [int(b) for b in collision_buckets],
            "task_type": "discrete_mode_candidate",
            "early_stop_triggered": idx < len(candidate_prices)
        }

        total_time_ms = self._compute_total_time(
            total_bits_sent_by_reader=total_bits_sent_by_reader,
            total_slots=total_slots,
            bits_per_slot=max(stage_bits_per_slot) if stage_bits_per_slot else 1,
            total_frames=total_frames
        )

        return best_prices, metric, total_time_ms, verification_data

    def _choose_frame_size(self, value_range: int, num_tags: int) -> int:
        if value_range <= 1:
            return 1
        base = 1
        target = min(32768, max(8, min(value_range, max(num_tags * 2, 16))))
        while base < target:
            base <<= 1
        return min(32768, max(1, base))

    def _choose_refine_frame_size(self, sub_range: int, candidate_count: int) -> int:
        target = min(32768, max(8, min(sub_range, max(candidate_count * 4, 16))))
        base = 1
        while base < target:
            base <<= 1
        return min(32768, max(1, base))

    def _choose_bits_per_slot(self, value_range: int, frame_size: int) -> int:
        if frame_size <= 1:
            return 1
        density = float(value_range) / float(frame_size)
        if density <= 1.5:
            return 2
        if density <= 4:
            return 3
        return 4

    def _reader_bits_for_bucket_frame(self, frame_size: int, bits_per_slot: int, price_min: int, price_max: int) -> int:
        return int(64 + 16 + 8 + 32 + 32 + 16 + frame_size.bit_length() + bits_per_slot.bit_length() + abs(price_max - price_min + 1).bit_length())

    def _reader_bits_for_verify_frame(self, candidate_prices: List[int]) -> int:
        return int(48 + 16 + 16 * len(candidate_prices))

    def _bucket_index_for_price(self, price: int, price_min: int, price_max: int, frame_size: int) -> int:
        value_range = max(1, price_max - price_min + 1)
        relative = price - price_min
        idx = int((relative * frame_size) / value_range)
        if idx >= frame_size:
            idx = frame_size - 1
        if idx < 0:
            idx = 0
        return idx

    def _simulate_tag_bucket_bitmap_response(
        self,
        tags: List[Tag],
        price_min: int,
        price_max: int,
        frame_size: int,
        bits_per_slot: int
    ) -> List[int]:
        slot_counts = [0] * frame_size
        for tag in tags:
            price = getattr(tag, "price", None)
            if price is None:
                continue
            if price_min <= price <= price_max:
                idx = self._bucket_index_for_price(price, price_min, price_max, frame_size)
                slot_counts[idx] += 1
        max_encodable = (1 << bits_per_slot) - 1
        return [min(c, max_encodable) for c in slot_counts]

    def _simulate_tag_bucket_bitmap_response_filtered_range(
        self,
        tags: List[Tag],
        active_price_set: set,
        sub_min: int,
        sub_max: int,
        frame_size: int,
        bits_per_slot: int
    ) -> List[int]:
        slot_counts = [0] * frame_size
        for tag in tags:
            price = getattr(tag, "price", None)
            if price is None:
                continue
            if price in active_price_set and sub_min <= price <= sub_max:
                idx = self._bucket_index_for_price(price, sub_min, sub_max, frame_size)
                slot_counts[idx] += 1
        max_encodable = (1 << bits_per_slot) - 1
        return [min(c, max_encodable) for c in slot_counts]

    def _decode_bucket_loads(self, bucket_signal: List[int]) -> Dict[int, int]:
        return {i: int(v) for i, v in enumerate(bucket_signal) if v > 0}

    def _select_top_buckets(self, bucket_loads: Dict[int, int], top_k: int) -> List[int]:
        if not bucket_loads:
            return []
        sorted_items = sorted(bucket_loads.items(), key=lambda x: (-x[1], x[0]))
        if not sorted_items:
            return []
        cutoff_idx = min(len(sorted_items), max(1, top_k)) - 1
        cutoff = sorted_items[cutoff_idx][1]
        selected = [b for b, v in sorted_items if v >= cutoff]
        return selected[:max(top_k, len(selected))]

    def _expand_buckets_to_prices(self, buckets: List[int], price_min: int, price_max: int, frame_size: int) -> List[int]:
        result = []
        value_range = max(1, price_max - price_min + 1)
        for b in buckets:
            low = price_min + (b * value_range) // frame_size
            high = price_min + ((b + 1) * value_range) // frame_size - 1
            if b == frame_size - 1:
                high = price_max
            low = max(low, price_min)
            high = min(high, price_max)
            if low <= high:
                if low == high:
                    result.append(low)
                else:
                    width = high - low + 1
                    if width <= 3:
                        result.extend(range(low, high + 1))
                    else:
                        mid = (low + high) // 2
                        result.extend(sorted(set([low, mid, high])))
        return sorted(set(result))

    def _expand_refined_buckets_to_prices(self, buckets: List[int], sub_min: int, sub_max: int, frame_size: int) -> List[int]:
        return self._expand_buckets_to_prices(buckets, sub_min, sub_max, frame_size)

    def _simulate_tag_exact_count_batch(self, tags: List[Tag], candidate_prices: List[int]) -> Dict[int, int]:
        counts = {p: 0 for p in candidate_prices}
        candidate_set = set(candidate_prices)
        for tag in tags:
            price = getattr(tag, "price", None)
            if price in candidate_set:
                counts[price] += 1
        return counts

    def _estimate_remaining_upper_bound(
        self,
        candidate_prices: List[int],
        price_min: int,
        price_max: int,
        frame_size: int,
        bucket_loads_stage1: Dict[int, int]
    ) -> int:
        if not candidate_prices:
            return 0
        max_bound = 0
        for p in candidate_prices:
            b = self._bucket_index_for_price(p, price_min, price_max, frame_size)
            max_bound = max(max_bound, bucket_loads_stage1.get(b, 0))
        return max_bound

    def _compute_total_time(self, total_bits_sent_by_reader: int, total_slots: int, bits_per_slot: int, total_frames: int) -> float:
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        return float(T_reader + T_tag + T_overhead)
