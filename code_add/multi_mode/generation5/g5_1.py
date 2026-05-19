import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g5_1(IProtocol):
    """
    子代协议：混合验证模式下的自适应RFID众数查询协议 (Adaptive Protocol with Hybrid Verification)

    本协议是一个全新的子代协议，旨在解决复杂的多众数查询问题。它通过深度融合两个父代协议的优点，
    并引入创新的“强/弱模式分离”验证机制，实现了在保证高准确率的同时，兼顾执行效率。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        # --- 协议超参数 ---
        # 阶段一：过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 48      # 当候选者数量小于此值时，停止过滤
        max_refinement_rounds = 5     # 最大过滤轮次
        # 阶段1.5 (突变)：强模式筛选相关
        screening_frame_size = 128
        strong_mode_threshold_factor = 2.0 # 槽位计数值超过平均值的多少倍被认为是强模式
        # 阶段二：验证相关
        independent_verification_frame_size = 64 # 用于强模式的独立验证帧长
        parallel_verification_frame_size = 128   # 用于弱模式的并行验证帧长


        # ==================================================================
        #  阶段一: 基于碰撞率的自适应迭代过滤
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        high_density_threshold = 0.8  # 帧密度过高阈值 (借鉴自父代A)
        low_density_threshold = 0.2   # 帧密度过低阈值 (借鉴自父代A)
        min_frame_size = 128

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += current_filter_frame_size.bit_length() + sum(s.bit_length() for s in filter_seeds)

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            total_slots += current_filter_frame_size * num_hash_functions
            # --- [物理层模拟结束] ---

            new_candidates = {p for p in candidate_prices if all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % current_filter_frame_size] > 0
                for i in range(num_hash_functions)
            )}
            candidate_prices = new_candidates
            refinement_rounds_done += 1

            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

        # ==================================================================
        #  阶段1.5: 强/弱模式筛选 (突变)
        # ==================================================================
        strong_candidates: Set[int] = set()
        weak_candidates: Set[int] = set()

        if candidate_prices:
            total_frames += 1
            screening_seed = generate_random_seed()
            total_bits_sent_by_reader += screening_frame_size.bit_length() + screening_seed.bit_length()

            # --- [物理层模拟] ---
            screening_slot_counts = [0] * screening_frame_size
            for t in tags:
                if t.price in candidate_prices:
                    slot_id = simple_hash(t.price, screening_seed) % screening_frame_size
                    screening_slot_counts[slot_id] += 1
            total_slots += screening_frame_size
            # --- [物理层模拟结束] ---

            # [修正]: 移除基于精确计数值的违规逻辑。
            # 改为使用合规的“碰撞状态”来识别强模式时隙。
            # 读写器可以合法地区分 count > 1 (碰撞) 的情况。
            strong_mode_slots = {
                i for i, count in enumerate(screening_slot_counts) if count > 1
            }

            for p in candidate_prices:
                slot_id = simple_hash(p, screening_seed) % screening_frame_size
                if slot_id in strong_mode_slots:
                    strong_candidates.add(p)

            weak_candidates = candidate_prices - strong_candidates


        # ==================================================================
        #  阶段二: 混合模式验证
        # ==================================================================
        final_scores: Dict[int, int] = {}

        # --- Part A: 对强候选者进行独立精确验证 (继承自父代A) ---
        if strong_candidates:
            for p in sorted(list(strong_candidates)):
                total_frames += 1
                seed_verify_ind = generate_random_seed()
                total_bits_sent_by_reader += p.bit_length() + independent_verification_frame_size.bit_length() + seed_verify_ind.bit_length()

                verification_slot_states = [0] * independent_verification_frame_size
                for t in tags:
                    if t.price == p:
                        slot_id = simple_hash(t.id, seed_verify_ind) % independent_verification_frame_size
                        verification_slot_states[slot_id] += 1
                total_slots += independent_verification_frame_size

                num_non_empty = sum(1 for count in verification_slot_states if count > 0)
                if num_non_empty > 0:
                    final_scores[p] = num_non_empty

        # --- Part B: 对弱候选者进行并行位图验证 (继承自父代B) ---
        if weak_candidates:
            total_frames += 1
            candidate_list = sorted(list(weak_candidates))
            price_to_bit_pos_map = {price: i for i, price in enumerate(candidate_list)}
            num_final_candidates = len(candidate_list)
            bits_per_slot = num_final_candidates

            seed_verify_par = generate_random_seed()
            total_bits_sent_by_reader += (parallel_verification_frame_size.bit_length() +
                                          seed_verify_par.bit_length() +
                                          sum(p.bit_length() for p in candidate_list))

            verification_frame_slots = [0] * parallel_verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify_par) % parallel_verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)
            total_slots += parallel_verification_frame_size

            for price, bit_pos in price_to_bit_pos_map.items():
                score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                if score > 0:
                    final_scores[price] = score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag_filter_screening_independent = (total_slots - (parallel_verification_frame_size if weak_candidates else 0)) * 1 * 0.4
        T_tag_parallel = (parallel_verification_frame_size if weak_candidates else 0) * (len(weak_candidates) if weak_candidates else 1) * 0.4
        T_tag = T_tag_filter_screening_independent + T_tag_parallel

        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "refinement_rounds_done": refinement_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "num_strong_candidates": len(strong_candidates),
            "num_weak_candidates": len(weak_candidates),
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, verification_data