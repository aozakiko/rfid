import math
from typing import List, Tuple, Dict, Set
# 假设这些基础模块已存在于环境中
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g8_1(IProtocol):
    """
    子代协议：动态碰撞阈值自适应众数查询协议
    (Dynamic Collision Thresholding Adaptive Protocol for Mode Query)

    本协议是一个全新的子代协议，旨在解决复杂的多众数查询问题。
    它通过深度融合两个父代协议的优点，并引入创新的动态筛选机制，
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
        candidate_stop_threshold = 40  # 当候选者数量小于此值时，停止过滤循环
        max_filter_rounds = 6          # 最大过滤轮次，防止无限循环
        high_density_threshold = 0.8   # 帧密度过高阈值 (用于扩大帧)
        low_density_threshold = 0.3    # 帧密度过低阈值 (用于缩小帧)
        min_frame_size = 32            # 过滤帧的最小尺寸

        # 阶段二: 验证相关
        verification_frame_size = 128

        # --- 突变部分引入的超参数 ---
        # 用于计算动态阈值的标准差倍数
        std_dev_multiplier = 1.0

        # ==================================================================
        #  阶段一: 迭代自适应过滤 (Iterative Adaptive Filtering)
        # --- 交叉部分：融合父代A的自适应帧长调整思想与父代B的迭代过滤循环结构 ---
        # 本阶段采用父代B的“当候选者数量 > 阈值时持续过滤”的宏观循环结构，
        # 并在每次循环内部，应用了父代A的“根据时隙密度动态调整下一轮帧长”的微观优化策略。
        # 同时，本阶段也借鉴了父代A的碰撞分数记录思想，为后续的突变机制做准备。
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        # 为每个潜在价格维护一个“碰撞分数”
        collision_scores: Dict[int, int] = {p: 0 for p in candidate_prices}

        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_stop_threshold and filter_rounds_done < max_filter_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 读写器广播帧长和哈希种子
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32) # 假设种子是32位

            # --- [物理层模拟] ---
            # 读写器准备接收帧，每个slot记录price命中次数
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
            for p in candidate_prices:
                is_still_candidate = True
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    slot_count = filter_frames_states[i][slot_id]

                    if slot_count == 0:
                        is_still_candidate = False
                        break
                    if slot_count > 1: # 如果价格p映射到了一个碰撞时隙
                        collision_scores[p] += 1

                if is_still_candidate:
                    new_candidates.add(p)

            if not new_candidates:
                break
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # 自适应调整帧长 (源自父代A)
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2 # 碰撞太严重，扩大帧
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size) # 太稀疏，缩小帧


        # ==================================================================
        #  阶段 1.5: 动态碰撞阈值预筛选
        # --- 突变部分：引入基于碰撞分数统计分布的动态候选者筛选机制 ---
        # 此机制为全新设计，在父代A和B中均未出现。父代A使用了固定的碰撞阈值(e.g., 1)，
        # 而本协议通过计算所有候选者碰撞分数的均值和标准差，动态生成一个筛选阈值。
        # 这种方法能更好地适应不同数据分布，仅保留那些碰撞表现“异常突出”的候选者，
        # 从而更精准地定位潜在的众数，提高最终验证阶段的效率。
        # ==================================================================
        final_candidate_list = sorted(list(candidate_prices))

        if final_candidate_list:
            scores = [collision_scores.get(p, 0) for p in final_candidate_list if collision_scores.get(p, 0) > 0]
            if len(scores) > 1:
                mean_score = sum(scores) / len(scores)
                variance = sum([(s - mean_score) ** 2 for s in scores]) / len(scores)
                std_dev_score = math.sqrt(variance)
                dynamic_threshold = mean_score + std_dev_multiplier * std_dev_score

                prescreened_candidates = {p for p in final_candidate_list if collision_scores.get(p, 0) >= dynamic_threshold}
                
                # 如果预筛选后仍有候选者，则使用筛选结果
                if prescreened_candidates:
                    final_candidate_list = sorted(list(prescreened_candidates))

        # ==================================================================
        #  阶段二: 并行位图精确验证
        # (该验证方法的核心逻辑继承自两个父代的共同思想，特别是父代B的并行位图实现)
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

        debug_data = {
            "filter_rounds_done": filter_rounds_done,
            "candidates_after_filter": len(candidate_prices),
            "candidates_after_prescreen": len(final_candidate_list),
            "std_dev_multiplier": std_dev_multiplier,
            "final_counts": final_counts,
        }

        if not final_counts:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_counts.values())
        modes = sorted([p for p, s in final_counts.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data
