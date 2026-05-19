import math
import random
from typing import List, Tuple, Dict, Any

from base import Tag, IProtocol


class web_ai_candidate(IProtocol):
    """
    面向离散属性众数/多众数查询的自适应 RFID 协议。
    目标：在遵守信息隔离原则下，利用匿名聚合帧返回保守的众数候选集合。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        prices = list(range(price_min, price_max + 1))
        total_price_count = len(prices)

        if total_price_count <= 0:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 0,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0
            }
            return [], 0, 0.0, verification_data

        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        total_tag_bits = 0

        stage_frame_sizes = []
        stage_bits_per_slot = []
        stage_names = []

        # -----------------------------
        # 参数设计：复用“协议基因”
        # -----------------------------
        existence_batch_size = min(128, total_price_count)
        existence_bits_per_slot = existence_batch_size
        existence_frame_size = self._choose_frame_size(len(tags), minimum=64, maximum=2048)

        scoring_batch_size = min(64, total_price_count)
        scoring_bits_per_slot = scoring_batch_size
        scoring_frame_size = self._choose_frame_size(len(tags), minimum=128, maximum=4096)

        verify_batch_size = min(16, total_price_count)
        verify_bits_per_slot = verify_batch_size
        verify_frame_size = self._choose_frame_size(len(tags), minimum=256, maximum=8192)

        # 保证硬性约束
        existence_bits_per_slot = min(256, max(1, existence_bits_per_slot))
        scoring_bits_per_slot = min(256, max(1, scoring_bits_per_slot))
        verify_bits_per_slot = min(256, max(1, verify_bits_per_slot))

        existence_frame_size = min(32768, max(1, existence_frame_size))
        scoring_frame_size = min(32768, max(1, scoring_frame_size))
        verify_frame_size = min(32768, max(1, verify_frame_size))

        # --- 交叉部分：融合父代 A 的“逐步缩小候选空间/后续精确验证”思想与父代 B 的“分批位图并行查询 + 非空时隙启发式评分”思想 ---
        # 阶段1：存在性剪枝
        candidate_prices = set()
        existence_batches = [prices[i:i + existence_batch_size] for i in range(0, len(prices), existence_batch_size)]

        existence_stage_non_empty_counts = {}
        for batch in existence_batches:
            seed = self._generate_seed()
            frame = self._simulate_tag_response_bitmap_frame(
                tags=tags,
                candidate_prices=batch,
                frame_size=existence_frame_size,
                bits_per_slot=existence_bits_per_slot,
                seed=seed
            )

            total_frames += 1
            total_bits_sent_by_reader += self._reader_command_bits(seed, existence_frame_size, len(batch))
            total_slots += existence_frame_size
            total_tag_bits += existence_frame_size * existence_bits_per_slot

            stage_frame_sizes.append(existence_frame_size)
            stage_bits_per_slot.append(existence_bits_per_slot)
            stage_names.append("existence_pruning")

            price_to_bit = {p: idx for idx, p in enumerate(batch)}
            for p, bit_pos in price_to_bit.items():
                logical_vector = self._extract_logical_bit_vector(frame, bit_pos)
                non_empty = sum(logical_vector)
                existence_stage_non_empty_counts[p] = non_empty
                if non_empty > 0:
                    candidate_prices.add(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            total_time_ms = T_reader + T_tag + T_overhead
            verification_data = {
                "frame_size": existence_frame_size,
                "bits_per_slot": existence_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "stage_names": stage_names,
                "total_frames": total_frames,
                "candidate_prices_after_stage1": [],
                "existence_non_empty_counts": existence_stage_non_empty_counts
            }
            return [], 0, total_time_ms, verification_data

        # 阶段2：评分
        candidate_list = sorted(candidate_prices)
        scoring_batches = [candidate_list[i:i + scoring_batch_size] for i in range(0, len(candidate_list), scoring_batch_size)]
        score_stats = {}

        for batch in scoring_batches:
            seed = self._generate_seed()
            frame = self._simulate_tag_response_bitmap_frame(
                tags=tags,
                candidate_prices=batch,
                frame_size=scoring_frame_size,
                bits_per_slot=scoring_bits_per_slot,
                seed=seed
            )

            total_frames += 1
            total_bits_sent_by_reader += self._reader_command_bits(seed, scoring_frame_size, len(batch))
            total_slots += scoring_frame_size
            total_tag_bits += scoring_frame_size * scoring_bits_per_slot

            stage_frame_sizes.append(scoring_frame_size)
            stage_bits_per_slot.append(scoring_bits_per_slot)
            stage_names.append("batched_scoring")

            price_to_bit = {p: idx for idx, p in enumerate(batch)}
            for p, bit_pos in price_to_bit.items():
                logical_vector = self._extract_logical_bit_vector(frame, bit_pos)
                non_empty = sum(logical_vector)
                collision = self._count_collisions_for_bit(frame, bit_pos)
                singleton = self._count_singletons_for_bit(frame, bit_pos)
                occupancy_est = self._estimate_count_from_occupancy(non_empty, scoring_frame_size)

                score_stats[p] = {
                    "non_empty": non_empty,
                    "collisions": collision,
                    "singletons": singleton,
                    "occupancy_est": occupancy_est
                }

        if not score_stats:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            total_time_ms = T_reader + T_tag + T_overhead
            verification_data = {
                "frame_size": scoring_frame_size,
                "bits_per_slot": scoring_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "stage_names": stage_names,
                "total_frames": total_frames,
                "candidate_prices_after_stage1": candidate_list,
                "score_stats": {}
            }
            return [], 0, total_time_ms, verification_data

        # --- 突变部分：引入“自适应候选桶重检 + 早停阈值 + 双指标置信修正”全新机制 ---
        # 机制说明：
        # 1) 使用 occupancy_est 与 non_empty 的组合进行初筛，避免仅靠单一评分。
        # 2) 仅对顶部少量候选执行重检，控制通信开销。
        # 3) 根据重检后置信边界保守返回候选集合；若领先优势明显则提前终止。
        ranked = sorted(
            score_stats.items(),
            key=lambda kv: (kv[1]["occupancy_est"], kv[1]["non_empty"], kv[1]["singletons"], -kv[1]["collisions"]),
            reverse=True
        )

        top_k = min(max(3, int(math.sqrt(len(ranked))) if ranked else 0), len(ranked))
        top_candidates = [p for p, _ in ranked[:top_k]]

        early_stop = False
        if len(ranked) >= 2:
            top1 = ranked[0][1]["occupancy_est"]
            top2 = ranked[1][1]["occupancy_est"]
            if top1 >= top2 * 1.25 and ranked[0][1]["non_empty"] >= ranked[1][1]["non_empty"]:
                early_stop = True

        verification_rounds = 0
        verify_stats = {}
        final_candidates = []

        if early_stop:
            best_price = ranked[0][0]
            final_candidates = [best_price]
        else:
            verify_batches = [top_candidates[i:i + verify_batch_size] for i in range(0, len(top_candidates), verify_batch_size)]
            for batch in verify_batches:
                seed = self._generate_seed()
                frame = self._simulate_tag_response_bitmap_frame(
                    tags=tags,
                    candidate_prices=batch,
                    frame_size=verify_frame_size,
                    bits_per_slot=verify_bits_per_slot,
                    seed=seed
                )

                total_frames += 1
                total_bits_sent_by_reader += self._reader_command_bits(seed, verify_frame_size, len(batch))
                total_slots += verify_frame_size
                total_tag_bits += verify_frame_size * verify_bits_per_slot

                stage_frame_sizes.append(verify_frame_size)
                stage_bits_per_slot.append(verify_bits_per_slot)
                stage_names.append("candidate_recheck")
                verification_rounds += 1

                price_to_bit = {p: idx for idx, p in enumerate(batch)}
                for p, bit_pos in price_to_bit.items():
                    logical_vector = self._extract_logical_bit_vector(frame, bit_pos)
                    non_empty = sum(logical_vector)
                    collision = self._count_collisions_for_bit(frame, bit_pos)
                    singleton = self._count_singletons_for_bit(frame, bit_pos)
                    occupancy_est = self._estimate_count_from_occupancy(non_empty, verify_frame_size)

                    base = score_stats.get(p, {})
                    combined_est = 0.4 * base.get("occupancy_est", 0.0) + 0.6 * occupancy_est
                    confidence_width = self._confidence_width(non_empty, verify_frame_size)

                    verify_stats[p] = {
                        "stage2_est": base.get("occupancy_est", 0.0),
                        "stage3_est": occupancy_est,
                        "combined_est": combined_est,
                        "non_empty_stage3": non_empty,
                        "collision_stage3": collision,
                        "singleton_stage3": singleton,
                        "confidence_width": confidence_width,
                        "lower_bound": max(0.0, combined_est - confidence_width),
                        "upper_bound": combined_est + confidence_width
                    }

            if verify_stats:
                sorted_verify = sorted(verify_stats.items(), key=lambda kv: kv[1]["combined_est"], reverse=True)
                best_est = sorted_verify[0][1]["combined_est"]
                best_lb = sorted_verify[0][1]["lower_bound"]

                # 保守返回：只保留与最优值高度相关且置信区间重叠的少量候选
                for p, stat in sorted_verify:
                    if stat["upper_bound"] >= best_lb and stat["combined_est"] >= best_est * 0.92:
                        final_candidates.append(p)

                final_candidates = sorted(final_candidates[:max(1, min(3, len(final_candidates)))])
            else:
                final_candidates = [ranked[0][0]]

        metric = 0
        if final_candidates:
            if verify_stats:
                metric = int(round(max(verify_stats[p]["combined_est"] for p in final_candidates)))
            else:
                metric = int(round(max(score_stats[p]["occupancy_est"] for p in final_candidates)))

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 1 * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": verify_frame_size if not early_stop else scoring_frame_size,
            "bits_per_slot": verify_bits_per_slot if not early_stop else scoring_bits_per_slot,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "stage_names": stage_names,
            "total_frames": total_frames,
            "candidate_prices_after_stage1": candidate_list,
            "top_candidates_for_recheck": top_candidates,
            "existence_non_empty_counts": existence_stage_non_empty_counts,
            "score_stats": score_stats,
            "verify_stats": verify_stats,
            "early_stop": early_stop,
            "verification_rounds": verification_rounds
        }

        return final_candidates, metric, total_time_ms, verification_data

    def _generate_seed(self) -> int:
        return random.randint(1, 2**31 - 1)

    def _choose_frame_size(self, tag_count: int, minimum: int, maximum: int) -> int:
        if tag_count <= 0:
            return minimum
        target = 1
        rough = max(minimum, min(maximum, int(tag_count * 2)))
        while target < rough:
            target <<= 1
        return min(maximum, max(minimum, target))

    def _reader_command_bits(self, seed: int, frame_size: int, batch_len: int) -> int:
        return max(1, seed.bit_length()) + max(1, frame_size.bit_length()) + max(1, batch_len.bit_length())

    def _simple_hash(self, x: int, seed: int) -> int:
        v = (x ^ seed) & 0xFFFFFFFF
        v = (v * 2654435761) & 0xFFFFFFFF
        v ^= (v >> 16)
        return v & 0xFFFFFFFF

    def _simulate_tag_response_bitmap_frame(
        self,
        tags: List[Tag],
        candidate_prices: List[int],
        frame_size: int,
        bits_per_slot: int,
        seed: int
    ) -> List[Dict[str, Any]]:
        frame = [{"or_bits": 0, "count_bits": [0] * bits_per_slot} for _ in range(frame_size)]
        price_to_bit = {p: idx for idx, p in enumerate(candidate_prices[:bits_per_slot])}
        candidate_set = set(price_to_bit.keys())

        for tag in tags:
            self._tag_response_bitmap_aggregate(tag, candidate_set, price_to_bit, frame, frame_size, seed)

        return frame

    def _tag_response_bitmap_aggregate(
        self,
        tag: Tag,
        candidate_set: set,
        price_to_bit: Dict[int, int],
        frame: List[Dict[str, Any]],
        frame_size: int,
        seed: int
    ) -> None:
        tag_price = getattr(tag, "price", None)
        if tag_price not in candidate_set:
            return

        bit_pos = price_to_bit[tag_price]
        slot_id = self._simple_hash(getattr(tag, "id"), seed) % frame_size
        frame[slot_id]["or_bits"] |= (1 << bit_pos)
        frame[slot_id]["count_bits"][bit_pos] += 1

    def _extract_logical_bit_vector(self, frame: List[Dict[str, Any]], bit_pos: int) -> List[int]:
        result = []
        for slot in frame:
            result.append((slot["or_bits"] >> bit_pos) & 1)
        return result

    def _count_collisions_for_bit(self, frame: List[Dict[str, Any]], bit_pos: int) -> int:
        count = 0
        for slot in frame:
            if slot["count_bits"][bit_pos] > 1:
                count += 1
        return count

    def _count_singletons_for_bit(self, frame: List[Dict[str, Any]], bit_pos: int) -> int:
        count = 0
        for slot in frame:
            if slot["count_bits"][bit_pos] == 1:
                count += 1
        return count

    def _estimate_count_from_occupancy(self, non_empty_slots: int, frame_size: int) -> float:
        if frame_size <= 0:
            return 0.0
        if non_empty_slots <= 0:
            return 0.0
        if non_empty_slots >= frame_size:
            return float(frame_size * 4)
        ratio = 1.0 - (non_empty_slots / float(frame_size))
        ratio = max(1e-12, ratio)
        return -frame_size * math.log(ratio)

    def _confidence_width(self, non_empty_slots: int, frame_size: int) -> float:
        if frame_size <= 0:
            return 0.0
        p = non_empty_slots / float(frame_size)
        variance = max(1e-9, p * (1.0 - p) / frame_size)
        return 1.96 * math.sqrt(variance) * frame_size
