import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_4_g10_5(IProtocol):
    """
    子代协议: 混合动态阈值优先级协议 (Hybrid Dynamic Threshold Priority Protocol, HDTP)

    本协议为了解决复杂多众数查询问题而设计，继承并融合了父代A和父代B的思想，
    同时引入了全新的动态碰撞阈值机制，以应对频率相近的“热门”价格众数问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        initial_scan_bins = 64
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_stop_threshold = 40
        max_filter_rounds = 6
        min_frame_size = 64
        high_density_threshold = 0.8
        low_density_threshold = 0.2
        stagnation_threshold = 0.05

        high_collision_rate_threshold = 0.5  # 高碰撞率场景阈值
        low_collision_rate_threshold = 0.1   # 低碰撞率场景阈值
        verification_frame_size = 128

        # ==================================================================
        #  阶段一: 热点区域预扫描 (Crossover from Parent A)
        # ==================================================================
        total_frames += 1
        total_bits_sent_by_reader += initial_scan_bins.bit_length()
        
        scan_frame_counts = [0] * initial_scan_bins
        price_range_width = price_max - price_min + 1
        for t in tags:
            bin_index = math.floor(((t.price - price_min) / price_range_width) * initial_scan_bins)
            bin_index = min(bin_index, initial_scan_bins - 1)
            scan_frame_counts[bin_index] += 1
        
        total_tag_reply_bits += initial_scan_bins * 1 
        
        hot_zone_indices = {i for i, count in enumerate(scan_frame_counts) if count > 1}
        if not hot_zone_indices:
            hot_zone_indices = {i for i, count in enumerate(scan_frame_counts) if count > 0}

        candidate_prices: Set[int] = set()
        if hot_zone_indices:
            for i in hot_zone_indices:
                start_price = price_min + math.floor(i * (price_range_width / initial_scan_bins))
                end_price = price_min + math.floor((i + 1) * (price_range_width / initial_scan_bins))
                for p in range(start_price, min(end_price, price_max + 1)):
                    candidate_prices.add(p)
        
        if not candidate_prices:
            candidate_prices = set(range(price_min, price_max + 1))

        # ==================================================================
        #  阶段二: 动态阈值迭代过滤 (Crossover from B + Mutation)
        # ==================================================================
        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_stop_threshold and filter_rounds_done < max_filter_rounds:
            prev_candidate_count = len(candidate_prices)
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32)

            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            
            total_tag_reply_bits += current_filter_frame_size * num_hash_functions * 1

            total_non_empty_slots = sum(1 for frame in filter_frames_states for count in frame if count > 0)
            total_collision_slots = sum(1 for frame in filter_frames_states for count in frame if count > 1)
            collision_rate = total_collision_slots / total_non_empty_slots if total_non_empty_slots > 0 else 0

            required_collisions = 1 
            if collision_rate > high_collision_rate_threshold:
                required_collisions = 2 
            elif collision_rate < low_collision_rate_threshold:
                required_collisions = 0

            new_candidates = set()
            for p in candidate_prices:
                is_present_in_all_frames = True
                collision_count = 0
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    slot_state = filter_frames_states[i][slot_id]
                    if slot_state == 0:
                        is_present_in_all_frames = False
                        break
                    if slot_state > 1:
                        collision_count += 1
                
                if is_present_in_all_frames and collision_count >= required_collisions:
                    new_candidates.add(p)

            if not new_candidates and len(candidate_prices) > 0:
                break 
            candidate_prices = new_candidates
            filter_rounds_done += 1

            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size) if current_filter_frame_size > 0 else 0
            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

            reduction_ratio = (prev_candidate_count - len(candidate_prices)) / prev_candidate_count if prev_candidate_count > 0 else 0
            if filter_rounds_done > 1 and reduction_ratio < stagnation_threshold:
                break

        # ==================================================================
        #  阶段三: 并行位图精确验证 (Crossover from Parent A & B)
        # ==================================================================
        final_counts: Dict[int, int] = {}
        final_candidate_list = sorted(list(candidate_prices))
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            bits_per_slot_verify = len(final_candidate_list)
            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (verification_frame_size.bit_length() + 32 +
                                          sum(p.bit_length() for p in final_candidate_list))

            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)

            total_tag_reply_bits += verification_frame_size * bits_per_slot_verify

            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                count = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if count > 0:
                    final_counts[price] = count

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_reply_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "filter_rounds_done": filter_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "final_counts": final_counts
        }

        if not final_counts:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_counts.values())
        modes = sorted([p for p, s in final_counts.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data