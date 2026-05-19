import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g7_3(IProtocol):
    """
    子代协议：混合启发式RFID众数查询协议 (Hybrid Heuristic RFID Mode Query Protocol)

    本协议是一个全新的子代协议，通过深度融合父代协议的优点，并引入创新的
    “启发式初始帧长预测”机制，旨在高效且准确地解决复杂的多众数查询问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0

        # --- 协议超参数 ---
        # 突变部分: 预估阶段相关
        estimation_frame_size = 128
        # 交叉部分: 过滤阶段相关
        num_hash_functions = 2
        candidate_threshold = 35
        max_refinement_rounds = 5
        high_density_threshold = 0.8
        low_density_threshold = 0.2
        min_frame_size = 64
        # 交叉部分: 验证阶段相关
        # [修正] 移除 initial_verification_frame_size 和 max_verification_attempts
        # 改为使用固定的并行验证帧长
        parallel_verification_frame_size = 256

        # ==================================================================
        #  阶段零: 启发式初始帧长预测
        # ==================================================================
        total_frames += 1
        estimation_seed = generate_random_seed()
        total_bits_sent_by_reader += estimation_frame_size.bit_length() + estimation_seed.bit_length()

        # --- [物理层模拟] ---
        estimation_frame = [0] * estimation_frame_size
        responded_prices = set()
        for t in tags:
            if t.price not in responded_prices:
                slot_id = simple_hash(t.price, estimation_seed) % estimation_frame_size
                estimation_frame[slot_id] += 1
                responded_prices.add(t.price)
        total_tag_response_bits += estimation_frame_size * 1
        # --- [物理层模拟结束] ---

        empty_slots = estimation_frame.count(0)
        if empty_slots < estimation_frame_size:
            estimated_cardinality = -estimation_frame_size * math.log(max(1, empty_slots) / estimation_frame_size)
        else:
            estimated_cardinality = 0

        current_filter_frame_size = max(min_frame_size, int(estimated_cardinality * 1.5))


        # ==================================================================
        #  阶段一: 基于碰撞率的自适应迭代过滤
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + filter_seeds[0].bit_length())

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            new_candidates = {p for p in candidate_prices if all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % current_filter_frame_size] > 0
                for i in range(num_hash_functions)
            )}

            if not new_candidates:
                break
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
        #  阶段二: [修正] 并行位图验证
        # ==================================================================
        final_scores: Dict[int, int] = {}
        if candidate_prices:
            total_frames += 1
            candidate_list = sorted(list(candidate_prices))
            price_to_bit_pos_map = {price: i for i, price in enumerate(candidate_list)}
            bits_per_slot = len(candidate_list)
            
            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (parallel_verification_frame_size.bit_length() +
                                          seed_verify.bit_length() +
                                          sum(p.bit_length() for p in candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * parallel_verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % parallel_verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)
            
            total_tag_response_bits += parallel_verification_frame_size * bits_per_slot
            # --- [物理层模拟结束] ---

            # --- 读写器分析聚合信号 ---
            for price, bit_pos in price_to_bit_pos_map.items():
                score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                if score > 0:
                    final_scores[price] = score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "estimated_cardinality": estimated_cardinality,
            "initial_filter_frame_size": current_filter_frame_size,
            "refinement_rounds_done": refinement_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, verification_data