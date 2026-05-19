import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_3(IProtocol):
    """
    子代协议: 混合动态范围过滤协议 (Hybrid Dynamic Range Filtering, HDRF)
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        # 突变阶段：热点区域预扫描
        initial_scan_bins = 64
        
        # 过滤阶段
        initial_filter_frame_size = 128
        num_hash_functions = 2
        candidate_stop_threshold = 32
        max_filter_rounds = 5
        high_density_threshold = 0.8
        low_density_threshold = 0.2
        min_frame_size = 32
        stagnation_rounds_threshold = 0.05

        # 验证阶段
        verification_frame_size = 128

        # ==================================================================
        #  阶段零: [突变] 热点区域预扫描
        # ==================================================================
        total_frames += 1
        total_bits_sent_by_reader += initial_scan_bins.bit_length()
        
        # --- [物理层模拟] ---
        scan_frame = [0] * initial_scan_bins
        price_range_width = price_max - price_min + 1
        for t in tags:
            bin_index = math.floor(((t.price - price_min) / price_range_width) * initial_scan_bins)
            bin_index = min(bin_index, initial_scan_bins - 1)
            scan_frame[bin_index] += 1
        
        total_tag_reply_bits += initial_scan_bins * 1 
        # --- [物理层模拟结束] ---

        # [修正]: 移除基于精确计数值的违规逻辑。
        # 改为使用合规的“碰撞状态”来识别热点区域。
        hot_zone_indices = {i for i, count in enumerate(scan_frame) if count > 1}
        
        # 如果没有碰撞区域，则退化为寻找所有非空区域
        if not hot_zone_indices:
            hot_zone_indices = {i for i, count in enumerate(scan_frame) if count > 0}

        candidate_prices: Set[int] = set()
        for i in hot_zone_indices:
            start_price = price_min + math.floor(i * (price_range_width / initial_scan_bins))
            end_price = price_min + math.floor((i + 1) * (price_range_width / initial_scan_bins))
            for p in range(start_price, min(end_price, price_max + 1)):
                candidate_prices.add(p)
        
        if not candidate_prices:
            candidate_prices = set(range(price_min, price_max + 1))


        # ==================================================================
        #  阶段一: [交叉] 自适应迭代过滤
        # ==================================================================
        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        prev_candidate_count = len(candidate_prices)

        while len(candidate_prices) > candidate_stop_threshold and filter_rounds_done < max_filter_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32)

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] = 1 # 标记为非空
            
            total_tag_reply_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            new_candidates = set()
            for p in candidate_prices:
                is_still_candidate = True
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    if filter_frames_states[i][slot_id] == 0:
                        is_still_candidate = False
                        break
                if is_still_candidate:
                    new_candidates.add(p)

            candidate_prices = new_candidates
            filter_rounds_done += 1

            reduction_ratio = (prev_candidate_count - len(candidate_prices)) / prev_candidate_count if prev_candidate_count > 0 else 0
            if reduction_ratio < stagnation_rounds_threshold and filter_rounds_done > 1:
                break
            prev_candidate_count = len(candidate_prices)

            total_slots = num_hash_functions * current_filter_frame_size
            non_empty_slots = sum(sum(frame) for frame in filter_frames_states)
            density = non_empty_slots / total_slots if total_slots > 0 else 0
            
            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

        final_candidate_list = sorted(list(candidate_prices))

        # ==================================================================
        #  阶段二: [交叉] 并行位图精确验证
        # ==================================================================
        final_counts: Dict[int, int] = {}
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            bits_per_slot_verify = len(final_candidate_list)

            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (verification_frame_size.bit_length() + 32 +
                                          sum(p.bit_length() for p in final_candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)

            total_tag_reply_bits += verification_frame_size * bits_per_slot_verify
            # --- [物理层模拟结束] ---

            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                count = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if count > 0:
                    final_counts[price] = count
        
        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_reply_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "initial_candidates_after_scan": len(candidate_prices) if filter_rounds_done == 0 else "N/A",
            "filter_rounds_done": filter_rounds_done,
            "final_candidates_after_filter": len(final_candidate_list),
            "final_counts": final_counts,
            "final_filter_frame_size": current_filter_frame_size,
        }

        if not final_counts:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_counts.values())
        modes = sorted([p for p, s in final_counts.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data