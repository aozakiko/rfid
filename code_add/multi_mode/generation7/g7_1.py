import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g7_1(IProtocol):
    """
    子代协议: 基于碰撞感知的自适应混合众数查询协议
    (Collision-Aware Adaptive Hybrid Protocol for Mode Query)

    本协议是一个全新的子代协议，旨在解决复杂的多众数查询问题。
    它通过深度融合两个父代协议的优点，并引入创新的机制，
    实现了在保证高准确率的同时，对通信开销和计算复杂度进行优化。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 40      # 当候选者数量小于此值时，停止过滤
        max_filter_rounds = 5         # 最大过滤轮次
        high_density_threshold = 0.8  # 帧密度过高阈值 (用于扩大帧)
        low_density_threshold = 0.3   # 帧密度过低阈值 (用于缩小帧)
        min_frame_size = 32           # 过滤帧的最小尺寸

        # 阶段二: 验证相关
        verification_frame_size = 128

        # --- 突变部分引入的超参数 ---
        collision_score_threshold = 1 # 候选者至少需要引发一次碰撞才能进入最终验证

        # ==================================================================
        #  阶段一: 自适应过滤与碰撞感知
        # --- 交叉部分：融合父代A的自适应帧长调整思想与父代B的多哈希迭代过滤流程 ---
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        # --- 突变部分：引入基于碰撞感知的候选者预筛选机制 ---
        # 为每个潜在价格维护一个“碰撞分数”
        collision_scores: Dict[int, int] = {p: 0 for p in candidate_prices}

        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 读写器广播帧长和哈希种子
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32) # 假设种子是32位

            # --- [物理层模拟] ---
            # 读写器准备接收帧，每个slot记录碰撞次数
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            # 在过滤阶段，标签只回复1比特表示存在性
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # 读写器分析聚合信号
            new_candidates = set()
            non_empty_slots_count = 0
            for p in candidate_prices:
                is_still_candidate = True
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    slot_count = filter_frames_states[i][slot_id]

                    if slot_count == 0:
                        is_still_candidate = False
                        break
                    # --- 突变部分：更新碰撞分数 ---
                    if slot_count > 1: # 如果价格p映射到了一个碰撞时隙
                        collision_scores[p] += 1

                if is_still_candidate:
                    new_candidates.add(p)

            # 如果没有候选者幸存，则提前终止
            if not new_candidates:
                break
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # --- 交叉部分：自适应调整帧长 (源自父代A) ---
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2 # 碰撞太严重，扩大帧
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size) # 太稀疏，缩小帧

        # ==================================================================
        #  阶段 1.5: 基于碰撞分数的预筛选
        # --- 突变部分：引入基于碰撞感知的候选者预筛选机制 ---
        # ==================================================================
        # 仅保留那些在过滤阶段至少引发一次碰撞的候选者
        prescreened_candidates = {p for p in candidate_prices if collision_scores.get(p, 0) >= collision_score_threshold}
        
        # 如果预筛选后没有候选者，则回退到不过滤的结果，以保证至少有候选者进入下一阶段
        if not prescreened_candidates and candidate_prices:
            final_candidate_list = sorted(list(candidate_prices))
        else:
            final_candidate_list = sorted(list(prescreened_candidates))


        # ==================================================================
        #  阶段二: 并行位图精确验证
        # --- 交叉部分：该验证方法的核心逻辑继承自父代B的并行位图思想 ---
        # ==================================================================
        final_counts: Dict[int, int] = {}
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            bits_per_slot = len(final_candidate_list)

            seed_verify = generate_random_seed()
            # 读写器广播验证参数：帧长、种子、以及最终候选价格列表
            total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                          32 + # 假设种子是32位
                                          sum(p.bit_length() for p in final_candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)

            total_tag_response_bits += verification_frame_size * bits_per_slot
            # --- [物理层模拟结束] ---

            # 读写器分析聚合位图，计算每个价格的精确计数值
            for price, bit_pos in price_to_bit_pos_map.items():
                count = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                if count > 0:
                    final_counts[price] = count

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        # 准备返回的调试信息
        debug_data = {
            "filter_rounds_done": filter_rounds_done,
            "candidates_after_filter": len(candidate_prices),
            "candidates_after_prescreen": len(final_candidate_list),
            "collision_score_threshold": collision_score_threshold,
            "final_counts": final_counts,
        }

        if not final_counts:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_counts.values())
        modes = sorted([p for p, s in final_counts.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data
