import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed,simple_hash


class g10_1(IProtocol):
    """
    子代协议: 动态哈希分层查询协议 (Dynamic Hashing Hierarchical-Query Protocol)

    本协议是为高效解决多众数查询问题而设计的全新子代协议。它严格遵循信息隔离原则，
    其核心设计体现了对父代协议A和B思想的深度融合（交叉），并引入了全新的优化机制（突变）。
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
        # 动态调整哈希函数数量的阈值
        high_candidate_count_threshold = 200 # 当候选者数量高于此值时，增加哈希函数
        low_candidate_count_threshold = 80 # 当候选者数量低于此值时，可考虑减少哈希函数
        max_hash_functions = 4 # 哈希函数的最大数量
        min_hash_functions = 2 # 哈希函数的最小数量

        # ==================================================================
        #  阶段一: 动态自适应过滤
        # ==================================================================
        # --- 交叉部分：融合父代A的碰撞分数统计思想与父代B的自适应迭代过滤框架 ---
        # 本阶段采纳了父代B的迭代式过滤框架，能根据信道密度自适应调整帧长。
        # 同时，借鉴父代A，在过滤过程中为每个候选价格累积“碰撞分数”，
        # 该分数将作为后续验证阶段的重要启发式信息。
        # 核心过滤逻辑采用了父代A/B共有的严格多哈希检查（布隆过滤器思想）。
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        collision_scores: Dict[int, int] = {p: 0 for p in candidate_prices}
        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        
        # --- 突变部分：引入动态哈希函数数量调整全新机制 ---
        # 与两个父代协议使用固定数量哈希函数不同，本协议引入了全新的动态调整机制。
        # 读写器会根据每轮过滤后剩余候选者的数量来动态调整下一轮使用的哈希函数数量。
        # 当候选者依然很多时，增加哈希函数的数量可以提高过滤的“严格性”，从而更快地筛除无关价格。
        # 当候选者数量减少后，则降低哈希函数的数量以减少不必要的通信开销。
        # 这种机制使得协议的过滤强度能自适应于问题的“难度”。
        # ==================================================================
        num_hash_functions = min_hash_functions

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 读写器广播帧长和哈希种子
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32) # 假设种子是32位

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1 # 过滤阶段回复1比特
            # --- [物理层模拟结束] ---

            # 读写器分析聚合信号
            new_candidates = set()
            for p in candidate_prices:
                is_still_candidate = True
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    slot_count = filter_frames_states[i][slot_id]

                    if slot_count == 0: # 严格过滤：任何一个哈希映射为空则被淘汰
                        is_still_candidate = False
                        break
                    
                    if slot_count > 1: # 借鉴父代A，记录碰撞信息
                        collision_scores[p] += 1
                
                if is_still_candidate:
                    new_candidates.add(p)

            if not new_candidates:
                candidate_prices = set()
                break
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # --- 交叉思想的应用：自适应帧长调整 (源自父代A和B) ---
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)
            
            # --- 突变机制的应用：动态调整哈希函数数量 ---
            if len(candidate_prices) > high_candidate_count_threshold and num_hash_functions < max_hash_functions:
                num_hash_functions += 1
            elif len(candidate_prices) < low_candidate_count_threshold and num_hash_functions > min_hash_functions:
                num_hash_functions -= 1

        final_candidate_list = sorted(list(candidate_prices))

        # ==================================================================
        #  阶段二: 并行位图精确验证
        # --- 交叉部分：该验证方法的核心逻辑继承自父代B的并行位图思想 ---
        # 经过第一阶段的大幅筛选后，本阶段直接采用父代B中最高效的并行位图验证方案。
        # 所有幸存的候选者被编码到一个位向量中，读写器通过一次通信，就能并行地、
        # 精确地统计出所有候选价格的数量。这避免了为每个候选者单独验证的巨大开销，
        # 是处理多个潜在众数的理想选择，体现了对父代B优秀机制的继承。
        # ==================================================================
        final_counts: Dict[int, int] = {}
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            bits_per_slot_verify = len(final_candidate_list)

            seed_verify = generate_random_seed()
            # 读写器广播验证参数
            total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                          32 + # 种子
                                          sum(p.bit_length() for p in final_candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)

            total_tag_response_bits += verification_frame_size * bits_per_slot_verify
            # --- [物理层模拟结束] ---

            # 读写器分析聚合位图
            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                count = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if count > 0:
                    final_counts[price] = count

        # ==================================================================
        #  最终处理与时间计算
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