import math
from typing import List, Tuple, Dict, Any
from collections import defaultdict

from base import Tag, IProtocol, generate_random_seed
try:
    from base import Frame
except Exception:
    Frame = None

try:
    from hash import simple_hash
except Exception:
    from base import simple_hash


class web_mode_query_iter_01(IProtocol):
    """
    面向离散属性多众数查询的子代协议。

    设计目标：
    1. 融合父代A的“按候选值进行匿名帧探测、使用非空时隙/碰撞可观测量评分”的思想；
    2. 融合父代B的“多轮草图估计、同构映射过滤、位串聚合估计”的思想；
    3. 引入新突变机制：两阶段自适应筛选 + 动态帧长 + 候选桶重检 + 早停。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        domain_values = list(range(price_min, price_max + 1))
        num_values = len(domain_values)

        if num_values <= 0:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 1,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
            }
            return [], 0, 0.0, verification_data

        # --- 交叉部分：融合父代 A 的按值匿名帧探测思想与父代 B 的多轮草图估计思想 ---
        # 阶段1：粗筛。每轮中将多个价格值映射到共享桶，标签按自身属性进入桶并发送SOG位串；
        # 读写器只能看到匿名聚合的桶信号，再结合已知候选值域和哈希种子进行桶级推断。
        stage1_rounds = 3
        stage1_bits_per_slot = 32
        stage1_frame_size = self._bounded_int(self._next_power_of_two(max(8, min(32768, num_values // 2 + 1))), 8, 32768)

        # 每个值的阶段1统计
        coarse_stats = {
            p: {
                "sum_non_empty": 0.0,
                "sum_collision": 0.0,
                "sum_L": 0.0,
                "unique_rounds": 0,
                "shared_rounds": 0,
                "coarse_score": 0.0,
                "est_n": 0.0,
            }
            for p in domain_values
        }

        stage1_round_bucket_details = []

        for _ in range(stage1_rounds):
            total_frames += 1
            seed_r = generate_random_seed()

            total_bits_sent_by_reader += (
                stage1_frame_size.bit_length()
                + stage1_bits_per_slot.bit_length()
                + seed_r.bit_length()
                + 8
            )

            bucket_signals, bucket_counts = self._simulate_tag_bucket_sog_frame(
                tags=tags,
                frame_size=stage1_frame_size,
                bits_per_slot=stage1_bits_per_slot,
                seed=seed_r,
            )
            total_slots += stage1_frame_size * stage1_bits_per_slot

            bucket_to_values = defaultdict(list)
            for p in domain_values:
                bucket_idx = simple_hash(p, seed_r) % stage1_frame_size
                bucket_to_values[bucket_idx].append(p)

            round_detail = {"seed": seed_r, "unique_buckets": 0, "shared_buckets": 0}

            for bucket_idx, values_in_bucket in bucket_to_values.items():
                observed_signal = bucket_signals[bucket_idx]
                observed_count = bucket_counts[bucket_idx]
                non_empty = 1 if observed_count > 0 else 0
                collision = 1 if observed_count > 1 else 0
                L = self._find_leading_ones(observed_signal, stage1_bits_per_slot)

                if len(values_in_bucket) == 1:
                    round_detail["unique_buckets"] += 1
                    p = values_in_bucket[0]
                    coarse_stats[p]["sum_non_empty"] += non_empty
                    coarse_stats[p]["sum_collision"] += collision
                    coarse_stats[p]["sum_L"] += L
                    coarse_stats[p]["unique_rounds"] += 1
                else:
                    round_detail["shared_buckets"] += 1
                    shared_weight = 1.0 / len(values_in_bucket)
                    for p in values_in_bucket:
                        coarse_stats[p]["sum_non_empty"] += non_empty * shared_weight
                        coarse_stats[p]["sum_collision"] += collision * shared_weight
                        coarse_stats[p]["sum_L"] += L * shared_weight
                        coarse_stats[p]["shared_rounds"] += 1

            stage1_round_bucket_details.append(round_detail)

        omega_w = 1.2897
        for p in domain_values:
            st = coarse_stats[p]
            effective_rounds = st["unique_rounds"] + st["shared_rounds"]
            avg_L = (st["sum_L"] / effective_rounds) if effective_rounds > 0 else 0.0
            est_n = omega_w * (2 ** avg_L) if avg_L > 0 else 0.0

            # 父代A中的“非空时隙可作为鲁棒评分”；父代B中的“草图L值可用于数量估计”
            # 这里做加权融合评分，偏向稳健候选筛选。
            coarse_score = (
                1.6 * st["sum_non_empty"]
                + 1.2 * st["sum_collision"]
                + 0.35 * avg_L
                + 0.15 * math.log2(est_n + 1.0)
            )

            st["est_n"] = est_n
            st["coarse_score"] = coarse_score

        sorted_by_coarse = sorted(
            domain_values,
            key=lambda x: (coarse_stats[x]["coarse_score"], coarse_stats[x]["est_n"], -x),
            reverse=True,
        )

        # --- 突变部分：引入两阶段自适应候选筛选与动态帧长全新机制 ---
        # 保守候选集：仅取少量高分值进入验证，避免返回大量不相关候选。
        top_k = self._bounded_int(max(2, min(8, int(math.sqrt(num_values)) + 1)), 2, 8)
        if sorted_by_coarse:
            top_score = coarse_stats[sorted_by_coarse[0]]["coarse_score"]
            threshold = top_score * 0.82
            preliminary_candidates = [p for p in sorted_by_coarse[: max(top_k * 2, 4)] if coarse_stats[p]["coarse_score"] >= threshold]
            if len(preliminary_candidates) < min(2, len(sorted_by_coarse)):
                preliminary_candidates = sorted_by_coarse[: min(top_k, len(sorted_by_coarse))]
            preliminary_candidates = preliminary_candidates[: max(top_k, 2)]
        else:
            preliminary_candidates = []

        # 阶段2：对候选值进行逐值验证，继承父代A的逐值独立帧探测思想；
        # 同时继续使用父代B风格的位串聚合估计，结合碰撞率进行重检。
        stage2_candidate_count = len(preliminary_candidates)
        dynamic_frame_size = self._bounded_int(
            self._next_power_of_two(max(16, min(256, stage2_candidate_count * 16))),
            16,
            256,
        )
        stage2_bits_per_slot = self._bounded_int(16 + 8 * min(8, stage2_candidate_count), 16, 128)

        verification_scores = {}
        stage2_round_details = []

        # 早停控制：若头部候选明显领先，可减少后续重检轮数
        stage2_max_rounds = 3
        early_stop_triggered = False

        for p in preliminary_candidates:
            verification_scores[p] = {
                "non_empty_rounds": 0.0,
                "collision_rounds": 0.0,
                "sum_non_empty_slots": 0.0,
                "sum_collision_slots": 0.0,
                "sum_L": 0.0,
                "rounds": 0,
                "verified_score": 0.0,
                "verified_est_n": 0.0,
            }

        for round_idx in range(stage2_max_rounds):
            round_summary = {"round": round_idx + 1, "frame_size": dynamic_frame_size, "bits_per_slot": stage2_bits_per_slot, "prices": []}

            for p in preliminary_candidates:
                total_frames += 1
                seed_s = generate_random_seed()
                total_bits_sent_by_reader += (
                    p.bit_length()
                    + dynamic_frame_size.bit_length()
                    + stage2_bits_per_slot.bit_length()
                    + seed_s.bit_length()
                    + 12
                )

                slot_signals, slot_counts = self._simulate_tag_value_sog_frame(
                    tags=tags,
                    target_value=p,
                    frame_size=dynamic_frame_size,
                    bits_per_slot=stage2_bits_per_slot,
                    seed=seed_s,
                )
                total_slots += dynamic_frame_size * stage2_bits_per_slot

                num_non_empty_slots = sum(1 for c in slot_counts if c > 0)
                num_collision_slots = sum(1 for c in slot_counts if c > 1)
                active_signals = [slot_signals[i] for i, c in enumerate(slot_counts) if c > 0]
                avg_L = (
                    sum(self._find_leading_ones(sig, stage2_bits_per_slot) for sig in active_signals) / len(active_signals)
                    if active_signals
                    else 0.0
                )
                est_n = omega_w * (2 ** avg_L) if avg_L > 0 else 0.0

                vs = verification_scores[p]
                vs["non_empty_rounds"] += 1 if num_non_empty_slots > 0 else 0
                vs["collision_rounds"] += 1 if num_collision_slots > 0 else 0
                vs["sum_non_empty_slots"] += num_non_empty_slots
                vs["sum_collision_slots"] += num_collision_slots
                vs["sum_L"] += avg_L
                vs["rounds"] += 1

                avg_non_empty = vs["sum_non_empty_slots"] / vs["rounds"]
                avg_collision = vs["sum_collision_slots"] / vs["rounds"]
                avg_round_L = vs["sum_L"] / vs["rounds"]
                vs["verified_est_n"] = omega_w * (2 ** avg_round_L) if avg_round_L > 0 else 0.0

                # 交叉融合：A的非空/碰撞利用 + B的L草图估计
                vs["verified_score"] = (
                    1.8 * avg_non_empty
                    + 1.5 * avg_collision
                    + 0.45 * avg_round_L
                    + 0.25 * math.log2(vs["verified_est_n"] + 1.0)
                )

                round_summary["prices"].append(
                    {
                        "price": p,
                        "non_empty_slots": num_non_empty_slots,
                        "collision_slots": num_collision_slots,
                        "avg_L": avg_L,
                    }
                )

            stage2_round_details.append(round_summary)

            # --- 突变部分：引入早停策略全新机制 ---
            ranked_now = sorted(
                preliminary_candidates,
                key=lambda x: (verification_scores[x]["verified_score"], verification_scores[x]["verified_est_n"], -x),
                reverse=True,
            )
            if len(ranked_now) >= 2:
                lead = verification_scores[ranked_now[0]]["verified_score"]
                second = verification_scores[ranked_now[1]]["verified_score"]
                if round_idx >= 1 and lead > 0 and (lead - second) >= max(1.0, 0.18 * lead):
                    early_stop_triggered = True
                    break

            # --- 突变部分：引入候选桶重检与自适应帧长调整全新机制 ---
            # 若碰撞过高，则下一轮增大帧长；若过低，则略缩减以控制通信开销
            if preliminary_candidates:
                avg_collision_density = sum(
                    verification_scores[p]["sum_collision_slots"] / max(1, verification_scores[p]["rounds"])
                    for p in preliminary_candidates
                ) / len(preliminary_candidates)
                avg_non_empty_density = sum(
                    verification_scores[p]["sum_non_empty_slots"] / max(1, verification_scores[p]["rounds"])
                    for p in preliminary_candidates
                ) / len(preliminary_candidates)

                if avg_collision_density > dynamic_frame_size * 0.18:
                    dynamic_frame_size = self._bounded_int(min(256, dynamic_frame_size * 2), 16, 256)
                elif avg_non_empty_density < max(2, dynamic_frame_size * 0.05):
                    dynamic_frame_size = self._bounded_int(max(16, dynamic_frame_size // 2), 16, 256)

        if not preliminary_candidates:
            verification_data = {
                "frame_size": stage1_frame_size,
                "bits_per_slot": stage1_bits_per_slot,
                "stage_frame_sizes": [stage1_frame_size],
                "stage_bits_per_slot": [stage1_bits_per_slot],
                "total_frames": total_frames,
                "coarse_scores": {},
                "verification_scores": {},
                "early_stop": False,
            }
            total_time_ms = self._compute_total_time_ms(total_bits_sent_by_reader, total_slots, total_frames)
            return [], 0, total_time_ms, verification_data

        ranked_final = sorted(
            preliminary_candidates,
            key=lambda x: (verification_scores[x]["verified_score"], verification_scores[x]["verified_est_n"], coarse_stats[x]["coarse_score"], -x),
            reverse=True,
        )

        best_score = verification_scores[ranked_final[0]]["verified_score"] if ranked_final else 0.0
        estimated_values = []
        if best_score > 0:
            retain_threshold = best_score * 0.92
            for p in ranked_final:
                if verification_scores[p]["verified_score"] >= retain_threshold:
                    estimated_values.append(p)

        # 保守输出，避免候选过多
        estimated_values = sorted(estimated_values[: min(3, len(estimated_values))])

        metric = 0
        if estimated_values:
            metric = int(math.ceil(max(verification_scores[p]["verified_est_n"] for p in estimated_values)))

        total_time_ms = self._compute_total_time_ms(total_bits_sent_by_reader, total_slots, total_frames)

        verification_data = {
            "frame_size": stage1_frame_size,
            "bits_per_slot": stage1_bits_per_slot,
            "stage_frame_sizes": [stage1_frame_size, dynamic_frame_size],
            "stage_bits_per_slot": [stage1_bits_per_slot, stage2_bits_per_slot],
            "total_frames": total_frames,
            "coarse_scores": {p: coarse_stats[p]["coarse_score"] for p in preliminary_candidates},
            "coarse_estimates": {p: coarse_stats[p]["est_n"] for p in preliminary_candidates},
            "verification_scores": {p: verification_scores[p]["verified_score"] for p in preliminary_candidates},
            "verification_estimates": {p: verification_scores[p]["verified_est_n"] for p in preliminary_candidates},
            "preliminary_candidates": list(preliminary_candidates),
            "stage1_rounds": stage1_rounds,
            "stage2_rounds_executed": max((verification_scores[p]["rounds"] for p in preliminary_candidates), default=0),
            "early_stop": early_stop_triggered,
            "stage1_bucket_details": stage1_round_bucket_details,
            "stage2_round_details": stage2_round_details,
        }

        return estimated_values, metric, total_time_ms, verification_data

    def _simulate_tag_bucket_sog_frame(
        self,
        tags: List[Tag],
        frame_size: int,
        bits_per_slot: int,
        seed: int,
    ) -> Tuple[List[int], List[int]]:
        """
        标签侧响应：
        标签依据自身私有属性 price 和广播 seed，选择桶并发送SOG位串。
        读写器只能看到每个桶的匿名聚合信号和计数效果。
        """
        signals = [0] * frame_size
        counts = [0] * frame_size

        for t in tags:
            bucket_idx = simple_hash(t.price, seed) % frame_size
            sog = self._build_sog_from_id(t.id, seed, bits_per_slot)
            signals[bucket_idx] |= sog
            counts[bucket_idx] += 1

        return signals, counts

    def _simulate_tag_value_sog_frame(
        self,
        tags: List[Tag],
        target_value: int,
        frame_size: int,
        bits_per_slot: int,
        seed: int,
    ) -> Tuple[List[int], List[int]]:
        """
        标签侧响应：
        仅当标签私有属性等于 target_value 时才参与该轮，并基于ID与seed匿名选择时隙并发送位串。
        """
        signals = [0] * frame_size
        counts = [0] * frame_size

        for t in tags:
            if t.price == target_value:
                slot_idx = simple_hash(t.id, seed) % frame_size
                sog = self._build_sog_from_id(t.id, seed, bits_per_slot)
                signals[slot_idx] |= sog
                counts[slot_idx] += 1

        return signals, counts

    def _build_sog_from_id(self, tag_id: Any, seed: int, sog_length: int) -> int:
        random_bits = simple_hash(tag_id, seed)
        mu = self._find_leading_zeros(random_bits, sog_length)
        if mu >= sog_length:
            return 0
        bit_pos = sog_length - 1 - mu
        return 1 << bit_pos

    def _find_leading_zeros(self, value: int, width: int) -> int:
        count = 0
        for i in range(width - 1, -1, -1):
            if (value >> i) & 1:
                return count
            count += 1
        return width

    def _find_leading_ones(self, value: int, width: int) -> int:
        count = 0
        for i in range(width - 1, -1, -1):
            if ((value >> i) & 1) == 1:
                count += 1
            else:
                break
        return count

    def _next_power_of_two(self, x: int) -> int:
        if x <= 1:
            return 1
        return 1 << (x - 1).bit_length()

    def _bounded_int(self, value: int, low: int, high: int) -> int:
        return max(low, min(high, int(value)))

    def _compute_total_time_ms(self, total_bits_sent_by_reader: int, total_slots: int, total_frames: int) -> float:
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        return T_reader + T_tag + T_overhead
