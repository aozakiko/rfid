import math
from typing import List, Tuple, Dict, Any
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash


class web_mode_query_candidate(IProtocol):
    def __init__(self):
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.max_bits_per_slot_used = 1
        self.last_frame_size = 1
        self.stage_frame_sizes = {}
        self.stage_bits_per_slot = {}

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_slots = 0
        self.max_bits_per_slot_used = 1
        self.last_frame_size = 1
        self.stage_frame_sizes = {}
        self.stage_bits_per_slot = {}

        # 协议参数
        COARSE_BATCH_BITS = 96
        COARSE_FRAME_SIZE = 256
        FINE_BATCH_BITS = 32
        FINE_FRAME_SIZE_BASE = 1024
        VERIFY_FRAME_SIZE_BASE = 2048
        CANDIDATE_POOL_SIZE = 12
        FINAL_POOL_SIZE = 4
        COLLISION_RECHECK_THRESHOLD = 0.22
        EPSILON_RATIO = 0.12
        MAX_FRAME_SIZE = 32768
        MAX_BITS_PER_SLOT = 256

        COARSE_BATCH_BITS = min(COARSE_BATCH_BITS, MAX_BITS_PER_SLOT)
        FINE_BATCH_BITS = min(FINE_BATCH_BITS, MAX_BITS_PER_SLOT)

        all_values = list(range(price_min, price_max + 1))
        if not all_values:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": {},
                "stage_bits_per_slot": {},
                "total_frames": self.total_frames,
                "message": "empty range",
            }
            return [], 0, total_time_ms, verification_data

        # --- 交叉部分：融合父代 A 的“范围/桶级逐步聚焦”思想与父代 B 的“并行批量位图估算”思想 ---
        coarse_counts, coarse_meta = self._run_parallel_value_estimation(
            tags=tags,
            target_values=all_values,
            frame_size=COARSE_FRAME_SIZE,
            bits_per_slot=COARSE_BATCH_BITS,
            stage_name="coarse"
        )

        positive_coarse = [(v, c) for v, c in coarse_counts.items() if c > 0 or c == float("inf")]
        if not positive_coarse:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": COARSE_FRAME_SIZE,
                "bits_per_slot": COARSE_BATCH_BITS,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "coarse_top": [],
                "total_frames": self.total_frames,
            }
            return [], 0, total_time_ms, verification_data

        sorted_coarse = self._sort_count_items(positive_coarse)
        candidate_values = [v for v, _ in sorted_coarse[:CANDIDATE_POOL_SIZE]]

        # 父代A的聚焦思想：对粗筛候选进行局部精估
        fine_frame_size = min(FINE_FRAME_SIZE_BASE, MAX_FRAME_SIZE)
        fine_counts, fine_meta = self._run_parallel_value_estimation(
            tags=tags,
            target_values=candidate_values,
            frame_size=fine_frame_size,
            bits_per_slot=FINE_BATCH_BITS,
            stage_name="fine"
        )

        positive_fine = [(v, c) for v, c in fine_counts.items() if c > 0 or c == float("inf")]
        if not positive_fine:
            positive_fine = positive_coarse[:1]

        sorted_fine = self._sort_count_items(positive_fine)

        top_estimate = sorted_fine[0][1]
        final_candidate_values = self._select_close_candidates(
            sorted_items=sorted_fine,
            max_candidates=FINAL_POOL_SIZE,
            epsilon_ratio=EPSILON_RATIO,
            top_estimate=top_estimate
        )

        # --- 突变部分：引入“候选桶重检 + 自适应验证帧长 + 早停”全新机制 ---
        # 新机制不直接读取标签私有属性做读写器决策，而是先根据匿名聚合结果发现高碰撞/近似并列候选，
        # 再对少量候选值进行自适应重检，以减少误判并控制通信开销。
        verification_counts = {}
        verification_details = {}

        current_best = None
        current_best_value = None
        early_stop_triggered = False

        for idx, value in enumerate(final_candidate_values):
            verify_frame_size = self._adaptive_verify_frame_size(
                estimated_count=fine_counts.get(value, 0),
                base_size=VERIFY_FRAME_SIZE_BASE,
                max_frame_size=MAX_FRAME_SIZE
            )
            est_count, verify_meta = self._run_single_value_verification(
                tags=tags,
                target_value=value,
                frame_size=verify_frame_size,
                stage_name="verify"
            )

            verification_counts[value] = est_count
            verification_details[value] = verify_meta

            if current_best is None or self._count_greater(est_count, current_best):
                current_best = est_count
                current_best_value = value

            remaining = final_candidate_values[idx + 1:]
            if remaining and current_best not in (None, float("inf")):
                max_remaining_upper = self._max_remaining_upper_bound(remaining, fine_counts)
                if max_remaining_upper < current_best * (1.0 - EPSILON_RATIO / 2.0):
                    early_stop_triggered = True
                    break

        if not verification_counts:
            total_time_ms = self._calculate_total_time()
            verification_data = {
                "frame_size": fine_frame_size,
                "bits_per_slot": FINE_BATCH_BITS,
                "stage_frame_sizes": self.stage_frame_sizes,
                "stage_bits_per_slot": self.stage_bits_per_slot,
                "coarse_top": sorted_coarse[:CANDIDATE_POOL_SIZE],
                "fine_top": sorted_fine[:FINAL_POOL_SIZE],
                "total_frames": self.total_frames,
            }
            return [], 0, total_time_ms, verification_data

        sorted_verified = self._sort_count_items(list(verification_counts.items()))
        best_verified = sorted_verified[0][1]

        estimated_values = self._collect_conservative_modes(sorted_verified, best_verified, EPSILON_RATIO)
        metric = self._display_metric(best_verified)

        verification_data = {
            "frame_size": self.last_frame_size,
            "bits_per_slot": self.max_bits_per_slot_used,
            "stage_frame_sizes": self.stage_frame_sizes,
            "stage_bits_per_slot": self.stage_bits_per_slot,
            "coarse_top": sorted_coarse[:CANDIDATE_POOL_SIZE],
            "fine_top": sorted_fine[:FINAL_POOL_SIZE],
            "verification_counts": verification_counts,
            "verification_details": verification_details,
            "candidate_values": candidate_values,
            "final_candidate_values": final_candidate_values,
            "coarse_meta": coarse_meta,
            "fine_meta": fine_meta,
            "early_stop_triggered": early_stop_triggered,
            "total_frames": self.total_frames,
        }

        total_time_ms = self._calculate_total_time()
        return estimated_values, metric, total_time_ms, verification_data

    def _run_parallel_value_estimation(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        bits_per_slot: int,
        stage_name: str
    ) -> Tuple[Dict[int, float], Dict[str, Any]]:
        frame_size = max(1, min(frame_size, 32768))
        bits_per_slot = max(1, min(bits_per_slot, 256))

        self.stage_frame_sizes[stage_name] = frame_size
        self.stage_bits_per_slot[stage_name] = bits_per_slot
        self.last_frame_size = frame_size
        self.max_bits_per_slot_used = max(self.max_bits_per_slot_used, bits_per_slot)

        estimated_counts: Dict[int, float] = {}
        batches = [target_values[i:i + bits_per_slot] for i in range(0, len(target_values), bits_per_slot)]
        stage_collision_rates = []
        stage_saturated_values = []

        for batch in batches:
            if not batch:
                continue

            self.total_frames += 1
            self.total_slots += frame_size
            seed = generate_random_seed()

            self.total_bits_sent_by_reader += (
                seed.bit_length()
                + frame_size.bit_length()
                + len(batch).bit_length()
                + self._range_encoding_bits(batch)
            )

            physical_frame = self._simulate_tag_parallel_bitmap_response(
                tags=tags,
                target_values=batch,
                frame_size=frame_size,
                seed=seed
            )

            value_to_bit = {value: idx for idx, value in enumerate(batch)}
            collision_slots = sum(1 for s in physical_frame.slots if self._popcount(s) > 1)
            stage_collision_rates.append(collision_slots / frame_size if frame_size > 0 else 0.0)

            for value, bit_pos in value_to_bit.items():
                logical_presence = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                ones = sum(logical_presence)
                zeros = frame_size - ones

                if zeros == frame_size:
                    estimated_counts[value] = 0.0
                elif zeros == 0:
                    estimated_counts[value] = float("inf")
                    stage_saturated_values.append(value)
                else:
                    estimated_counts[value] = -frame_size * math.log(zeros / frame_size)

            # 对高碰撞批次的前若干高风险候选进行轻量重检
            if stage_name == "fine" and stage_collision_rates[-1] > 0:
                high_risk_values = [v for v in batch if estimated_counts.get(v) == float("inf")]
                if high_risk_values or stage_collision_rates[-1] >= 0.22:
                    recheck_subset = batch[: min(8, len(batch))]
                    recheck_counts = self._run_recheck_subset(
                        tags=tags,
                        target_values=recheck_subset,
                        frame_size=min(frame_size * 2, 32768),
                        stage_name="fine_recheck"
                    )
                    for v, c in recheck_counts.items():
                        if v in estimated_counts:
                            if estimated_counts[v] == float("inf") and c != float("inf"):
                                estimated_counts[v] = c
                            elif c != float("inf"):
                                estimated_counts[v] = max(estimated_counts[v], c)

        meta = {
            "batches": len(batches),
            "frame_size": frame_size,
            "bits_per_slot": bits_per_slot,
            "avg_collision_rate": sum(stage_collision_rates) / len(stage_collision_rates) if stage_collision_rates else 0.0,
            "saturated_values": stage_saturated_values,
        }
        return estimated_counts, meta

    def _run_recheck_subset(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        stage_name: str
    ) -> Dict[int, float]:
        counts, _ = self._run_parallel_value_estimation(
            tags=tags,
            target_values=target_values,
            frame_size=frame_size,
            bits_per_slot=min(len(target_values), 32),
            stage_name=stage_name
        )
        return counts

    def _run_single_value_verification(
        self,
        tags: List[Tag],
        target_value: int,
        frame_size: int,
        stage_name: str
    ) -> Tuple[float, Dict[str, Any]]:
        frame_size = max(1, min(frame_size, 32768))

        self.stage_frame_sizes[stage_name] = max(self.stage_frame_sizes.get(stage_name, 0), frame_size)
        self.stage_bits_per_slot[stage_name] = 1
        self.last_frame_size = frame_size
        self.max_bits_per_slot_used = max(self.max_bits_per_slot_used, 1)

        self.total_frames += 1
        self.total_slots += frame_size
        seed = generate_random_seed()
        self.total_bits_sent_by_reader += (
            seed.bit_length()
            + frame_size.bit_length()
            + target_value.bit_length()
        )

        physical_frame = self._simulate_tag_single_value_response(
            tags=tags,
            target_value=target_value,
            frame_size=frame_size,
            seed=seed
        )

        collision_slots = sum(1 for s in physical_frame.slots if s > 1)
        occupied_slots = sum(1 for s in physical_frame.slots if s > 0)
        zeros = frame_size - occupied_slots

        if zeros == frame_size:
            estimate = 0.0
        elif zeros == 0:
            estimate = float("inf")
        else:
            estimate = -frame_size * math.log(zeros / frame_size)

        meta = {
            "frame_size": frame_size,
            "bits_per_slot": 1,
            "collision_rate": collision_slots / frame_size if frame_size > 0 else 0.0,
            "occupied_slots": occupied_slots,
            "zero_slots": zeros,
        }
        return estimate, meta

    def _simulate_tag_parallel_bitmap_response(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        seed: int
    ) -> Frame:
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=len(target_values))
        value_to_bit = {value: idx for idx, value in enumerate(target_values)}
        target_set = set(target_values)

        for tag in tags:
            tag_value = getattr(tag, "price", None)
            if tag_value in target_set:
                slot_id = simple_hash(tag.id, seed) % frame_size
                reply_vector = 1 << value_to_bit[tag_value]
                physical_frame.aggregate(slot_id, reply_vector)

        return physical_frame

    def _simulate_tag_single_value_response(
        self,
        tags: List[Tag],
        target_value: int,
        frame_size: int,
        seed: int
    ) -> Frame:
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=1)

        for tag in tags:
            if getattr(tag, "price", None) == target_value:
                slot_id = simple_hash(tag.id, seed) % frame_size
                physical_frame.aggregate(slot_id, 1)

        return physical_frame

    def _adaptive_verify_frame_size(self, estimated_count: float, base_size: int, max_frame_size: int) -> int:
        if estimated_count == float("inf"):
            return min(base_size * 4, max_frame_size)
        if estimated_count <= 0:
            return min(base_size, max_frame_size)
        if estimated_count < 20:
            return min(base_size, max_frame_size)
        if estimated_count < 100:
            return min(base_size * 2, max_frame_size)
        return min(base_size * 4, max_frame_size)

    def _collect_conservative_modes(
        self,
        sorted_verified: List[Tuple[int, float]],
        best_verified: float,
        epsilon_ratio: float
    ) -> List[int]:
        if not sorted_verified:
            return []

        if best_verified == float("inf"):
            return [v for v, c in sorted_verified if c == float("inf")][:4]

        result = []
        threshold = best_verified * (1.0 - epsilon_ratio)
        for value, count in sorted_verified:
            if count >= threshold:
                result.append(value)
            if len(result) >= 4:
                break

        if not result:
            result = [sorted_verified[0][0]]
        return sorted(result)

    def _select_close_candidates(
        self,
        sorted_items: List[Tuple[int, float]],
        max_candidates: int,
        epsilon_ratio: float,
        top_estimate: float
    ) -> List[int]:
        if not sorted_items:
            return []

        if top_estimate == float("inf"):
            values = [v for v, c in sorted_items if c == float("inf")]
            return values[:max_candidates] if values else [sorted_items[0][0]]

        threshold = top_estimate * (1.0 - epsilon_ratio)
        selected = []
        for value, count in sorted_items:
            if count >= threshold or len(selected) < min(2, max_candidates):
                selected.append(value)
            if len(selected) >= max_candidates:
                break

        if not selected:
            selected = [sorted_items[0][0]]
        return selected

    def _max_remaining_upper_bound(self, remaining: List[int], fine_counts: Dict[int, float]) -> float:
        upper = 0.0
        for value in remaining:
            c = fine_counts.get(value, 0.0)
            if c == float("inf"):
                return float("inf")
            upper = max(upper, c)
        return upper

    def _sort_count_items(self, items: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
        return sorted(items, key=lambda x: (x[1] == float("inf"), x[1], -x[0]), reverse=True)

    def _count_greater(self, a: float, b: float) -> bool:
        if a == float("inf") and b != float("inf"):
            return True
        if b == float("inf"):
            return False
        return a > b

    def _display_metric(self, best_verified: float) -> int:
        if best_verified == float("inf"):
            return 10 ** 9
        return int(math.ceil(best_verified))

    def _range_encoding_bits(self, values: List[int]) -> int:
        if not values:
            return 1
        return sum(max(1, int(v).bit_length()) for v in values)

    def _popcount(self, x: int) -> int:
        return x.bit_count() if hasattr(int, "bit_count") else bin(x).count("1")

    def _calculate_total_time(self) -> float:
        t_reader = self.total_bits_sent_by_reader * 0.025
        t_tag = self.total_slots * self.max_bits_per_slot_used * 0.4
        t_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0
        return t_reader + t_tag + t_overhead
