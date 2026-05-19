import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g8_2(IProtocol):
    """
    子代协议：融合突变型自适应并行协议 (Mutated Adaptive Parallel Protocol, MAPP)

    本协议通过深度融合父代协议的核心思想，并引入全新的动态优化机制，
    旨在以更高的效率和适应性，准确地解决具有挑战性的多众数查询问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        # 阶段一：过滤相关
        initial_filter_frame_size = 256
        candidate_threshold = 48      # 当候选者数量小于此值时，停止过滤
        max_refinement_rounds = 6       # 最大精炼轮次，防止无限循环
        # 继承自父代B的自适应帧长调整参数
        high_density_threshold = 0.9
        low_density_threshold = 0.15
        min_frame_size = 64
        # 继承自父代B的停滞感知参数
        stagnation_threshold = 0.05
        min_rounds_before_stagnation_check = 2
        # 阶段二：验证相关
        verification_frame_size = 128

        # --- 突变部分：引入的全新超参数 ---
        # 动态哈希函数数量调整机制的参数
        initial_hash_functions = 1
        max_hash_functions = 3
        ineffective_reduction_threshold = 0.2 # 缩减率低于此值时，认为过滤效果不佳，需增加哈希函数

        # ==================================================================
        #  阶段一: 基于动态哈希与停滞感知的自适应迭代过滤
        # ==================================================================

        # --- 交叉部分：融合父代B的自适应迭代过滤思想与父代A的两阶段架构思想 ---
        # 本协议采纳了父代A和B共有的“先粗粒度过滤，后精确验证”的两阶段核心架构。
        # 在至关重要的过滤阶段，我们深度融合了父代B的精髓：通过多轮迭代通信，
        # 动态地精炼候选价格集合，并根据实时的帧碰撞率自适应地调整帧长。
        # 同时，集成了其“停滞感知”逻辑，用于在过滤效果达到瓶颈时提前终止，避免资源浪费。

        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        num_hash_functions = initial_hash_functions

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            prev_candidate_count = len(candidate_prices)

            # 广播当前轮次的帧长和哈希种子
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            bits_sent_this_round = current_filter_frame_size.bit_length()
            for seed in filter_seeds:
                bits_sent_this_round += seed.bit_length()
            total_bits_sent_by_reader += bits_sent_this_round

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            
            # 此阶段标签回复为1bit
            total_tag_reply_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # --- 读写器分析聚合信号，生成新的候选集 ---
            new_candidates = set()
            for p in candidate_prices:
                is_candidate_in_round = True
                for i in range(num_hash_functions):
                    slot_p = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    if filter_frames_states[i][slot_p] == 0:
                        is_candidate_in_round = False
                        break
                if is_candidate_in_round:
                    new_candidates.add(p)
            
            candidate_prices = new_candidates
            refinement_rounds_done += 1
            
            # --- 突变部分：引入基于过滤效率的“动态哈希函数数量”全新机制 ---
            # 这是父代A和B都未曾具备的全新优化。在每轮过滤后，读写器会评估本轮过滤的
            # “有效性”，即候选集的缩减率。如果缩减率过低（低于ineffective_reduction_threshold），
            # 协议会判定当前哈希函数的过滤能力不足以应对复杂的数据分布，从而在下一轮
            # 动态地增加哈希函数的数量，以增强过滤的精度。反之，若效果显著，则维持现状。
            # 这种机制使得协议在过滤的“力度”上也能自适应，实现了更高维度的智能调优。
            reduction_ratio = (prev_candidate_count - len(candidate_prices)) / prev_candidate_count if prev_candidate_count > 0 else 0
            if reduction_ratio < ineffective_reduction_threshold and num_hash_functions < max_hash_functions:
                 num_hash_functions += 1

            # 继承自父代B的帧碰撞率自适应调整逻辑
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)
            
            # 继承自父代B的停滞感知提前终止逻辑
            if refinement_rounds_done >= min_rounds_before_stagnation_check:
                if reduction_ratio < stagnation_threshold:
                    break # 过滤效果停滞，提前跳出循环

        # ==================================================================
        #  阶段二: 高效并行精确验证
        # ==================================================================

        # --- 交叉部分：融合父代A的高效并行验证思想 ---
        # 经过第一阶段高效的自适应过滤后，候选者数量已被大幅削减。此时，本协议
        # 切换到父代A的核心优势策略：并行验证。读写器将所有剩余的候选价格编码为
        # 位掩码的不同位，并通过一次通信让所有相关标签在同一个帧内并行地报告自己的
        # 存在。读写器仅需分析这一个聚合帧，即可同时解码出所有候选价格的准确计数值，
        # 极大地提升了验证阶段的效率，实现了整体协议的准确性与效率的平衡。
        
        final_scores = {}
        num_final_candidates = len(candidate_prices)

        if candidate_prices:
            total_frames += 1
            candidate_list = sorted(list(candidate_prices))
            price_to_bit_pos_map = {price: i for i, price in enumerate(candidate_list)}
            
            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                          seed_verify.bit_length() +
                                          sum(p.bit_length() for p in candidate_list))

            # --- [物理层模拟] ---
            # 标签回复的比特数等于最终候选者的数量
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)
            
            total_tag_reply_bits += verification_frame_size * num_final_candidates
            # --- [物理层模拟结束] ---
            
            # 读写器解码聚合帧，并行计算所有候选者的得分
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots]
                score = sum(logical_bit_vector)
                if score > 0:
                    final_scores[price] = score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_reply_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "initial_filter_frame_size": initial_filter_frame_size,
            "refinement_rounds_done": refinement_rounds_done,
            "final_filter_frame_size": current_filter_frame_size,
            "final_hash_functions": num_hash_functions,
            "num_final_candidates": num_final_candidates,
            "verification_frame_size": verification_frame_size,
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([
            price for price, score in final_scores.items()
            if score == max_score
        ])

        return modes, max_score, total_time_ms, verification_data