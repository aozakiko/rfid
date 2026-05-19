import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash


class g10_1_g10_2(IProtocol):
    """
    子代协议: 混合自适应哈希与ID冲突预判查询协议 (Hybrid Adaptive Hashing & ID Collision-Anticipation Query Protocol, HAHICAP)

    本协议为解决复杂多众数查询问题而全新设计，结合了父代协议A和父代协议B的优点，并引入了全新的冲突预判机制。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0

        # --- 协议超参数 ---
        initial_filter_frame_size = 256
        candidate_threshold = 40
        max_filter_rounds = 6
        high_density_threshold = 0.8
        low_density_threshold = 0.3
        min_frame_size = 32
        verification_frame_size = 128

        # --- 突变部分引入的超参数 ---
        high_candidate_count_threshold = 200
        low_candidate_count_threshold = 80
        max_hash_functions = 4
        min_hash_functions = 2
        id_frame_density_scaling_factor = 1.5  # 用于基于ID帧密度调整帧长的增强因子

        # ==================================================================
        # 阶段一: 自适应过滤
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        collision_scores: Dict[int, int] = {p: 0 for p in candidate_prices}
        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        num_hash_functions = min_hash_functions

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32)

            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            id_frame = [0] * current_filter_frame_size  # ID哈希帧用于冲突预判

            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1

                    # ID哈希帧响应
                    id_slot = simple_hash(t.id, generate_random_seed()) % current_filter_frame_size
                    id_frame[id_slot] = 1

            total_tag_response_bits += current_filter_frame_size * num_hash_functions
            total_tag_response_bits += current_filter_frame_size  # ID哈希帧也回复

            # 读写器分析聚合信号
            new_candidates = set()
            for p in candidate_prices:
                is_still_candidate = True
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    if filter_frames_states[i][slot_id] == 0:
                        is_still_candidate = False
                        break
                    if filter_frames_states[i][slot_id] > 1:
                        collision_scores[p] += 1

                if is_still_candidate:
                    new_candidates.add(p)

            if not new_candidates:
                candidate_prices = set()
                break
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # 根据密度调整帧长
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

            # 突变部分：基于ID帧密度调整帧长
            id_frame_density = sum(id_frame) / current_filter_frame_size
            if id_frame_density > high_density_threshold:
                new_size = int(current_filter_frame_size * (1 + (id_frame_density - high_density_threshold) * id_frame_density_scaling_factor))
                current_filter_frame_size = new_size

            # 动态调整哈希函数数量
            if len(candidate_prices) > high_candidate_count_threshold and num_hash_functions < max_hash_functions:
                num_hash_functions += 1
            elif len(candidate_prices) < low_candidate_count_threshold and num_hash_functions > min_hash_functions:
                num_hash_functions -= 1

        final_candidate_list = sorted(list(candidate_prices))

        # ==================================================================
        # 阶段二: 并行位图精确验证
        # ==================================================================
        final_counts: Dict[int, int] = {}
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (verification_frame_size.bit_length() + 32 + sum(p.bit_length() for p in final_candidate_list))

            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)

            total_tag_response_bits += verification_frame_size * len(final_candidate_list)

            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                count = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if count > 0:
                    final_counts[price] = count

        # ==================================================================
        # 最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "filter_rounds_done": filter_rounds_done,
            "num_candidates_after_filter": len(final_candidate_list),
            "final_counts": final_counts
        }

        if not final_counts:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_counts.values())
        modes = sorted([p for p, s in final_counts.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data