import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g6_2(IProtocol):
    """
    子代协议：混合自适应分组验证协议 (Hybrid Adaptive Group Verification Protocol)

    本协议通过深度融合父代协议的精华，并引入创新的分组验证与自适应重估机制，
    旨在以更高的效率和鲁棒性解决复杂的多众数查询问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1

        # --- 协议超参数 ---
        # 阶段一：过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 32
        max_refinement_rounds = 5
        high_density_threshold = 0.85
        low_density_threshold = 0.15
        min_frame_size = 128

        # 阶段二：验证相关 (突变部分)
        verification_group_size = 4
        verification_slots_per_candidate = 32
        grouped_verification_frame_size = verification_group_size * verification_slots_per_candidate
        
        # 优化：为解决准确率问题引入的新参数
        reverification_contention_threshold = 0.9 # 竞争激烈程度阈值
        reverification_frame_size = 256           # 重估时使用更大的帧

        # ==================================================================
        #  阶段一: 自适应迭代过滤 (交叉)
        # ==================================================================

        # --- 交叉部分：融合父代A的两阶段架构思想与父代B的自适应迭代过滤思想 ---
        # 本协议采纳了父代A和B共有的“先过滤，后验证”的高效两阶段架构。
        # 过滤阶段的核心逻辑直接借鉴了父代B的亮点：通过多轮迭代通信，不断精炼
        # 候选价格集合，并根据实时的帧碰撞率动态调整帧长。这种设计完美结合了
        # 父代A的结构化流程与父代B在应对复杂数据分布时的灵活性与适应性。

        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
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
            total_slots += current_filter_frame_size * num_hash_functions
            # --- [物理层模拟结束] ---

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

            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size) if (num_hash_functions * current_filter_frame_size) > 0 else 0


            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

        # ==================================================================
        #  阶段二: 分组验证与自适应重估 (突变)
        # ==================================================================

        # --- 突变部分：引入“分组并行验证”与“高竞争自适应重估”双重机制 ---
        # 机制一“分组验证”：将多个候选价格打包，在单次通信中并行完成验证，减少帧切换开销。
        # 机制二“自适应重估” (为提升准确率而优化)：在分组验证后，协议会识别出那些响应
        # 过于拥挤（非空时隙占比极高）的候选价格。对于这些“高竞争”价格，其初次评分不可靠。
        # 协议将为它们启动一次独立的、使用更大帧的二次验证，从而获得一个更精确的计数值，
        # 确保在高密度众数下的最终结果准确性。

        final_scores = {}
        sorted_candidates = sorted(list(candidate_prices))
        
        candidate_groups = [
            sorted_candidates[i:i + verification_group_size]
            for i in range(0, len(sorted_candidates), verification_group_size)
        ]

        for group in candidate_groups:
            total_frames += 1
            seed_verify = generate_random_seed()
            
            bits_sent = grouped_verification_frame_size.bit_length() + seed_verify.bit_length()
            for p in group:
                bits_sent += p.bit_length()
            total_bits_sent_by_reader += bits_sent

            # --- [物理层模拟] ---
            grouped_verification_states = [0] * grouped_verification_frame_size
            for t in tags:
                if t.price in group:
                    try:
                        price_index_in_group = group.index(t.price)
                        slot_offset = price_index_in_group * verification_slots_per_candidate
                        sub_slot_id = simple_hash(t.id, seed_verify) % verification_slots_per_candidate
                        final_slot = slot_offset + sub_slot_id
                        grouped_verification_states[final_slot] += 1
                    except ValueError:
                        continue # Should not happen if logic is correct
            total_slots += grouped_verification_frame_size
            # --- [物理层模拟结束] ---
            
            for i, p in enumerate(group):
                sub_frame_start = i * verification_slots_per_candidate
                sub_frame_end = sub_frame_start + verification_slots_per_candidate
                sub_frame_states = grouped_verification_states[sub_frame_start:sub_frame_end]
                
                num_non_empty = sum(1 for count in sub_frame_states if count > 0)
                if num_non_empty > 0:
                    final_scores[p] = num_non_empty

        # --- 高竞争候选者识别与重估 (为提升准确率而优化的新逻辑) ---
        high_contention_prices = []
        for p, score in final_scores.items():
            if score / verification_slots_per_candidate > reverification_contention_threshold:
                high_contention_prices.append(p)

        for p in high_contention_prices:
            total_frames += 1
            seed_reverify = generate_random_seed()
            total_bits_sent_by_reader += p.bit_length() + reverification_frame_size.bit_length() + seed_reverify.bit_length()

            reverification_slot_states = [0] * reverification_frame_size
            for t in tags:
                if t.price == p:
                    slot_id = simple_hash(t.id, seed_reverify) % reverification_frame_size
                    reverification_slot_states[slot_id] += 1
            total_slots += reverification_frame_size
            
            new_score = sum(1 for count in reverification_slot_states if count > 0)
            final_scores[p] = new_score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "refinement_rounds_done": refinement_rounds_done,
            "num_final_candidates": len(candidate_prices),
            "num_high_contention": len(high_contention_prices),
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

