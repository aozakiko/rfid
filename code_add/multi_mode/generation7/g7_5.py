import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g7_5(IProtocol):
    """
    子代协议：混合自适应停滞感知协议 (Hybrid Adaptive Stagnation-aware Protocol, HASP)

    本协议通过深度融合父代协议的核心思想，并引入全新的“停滞感知”提前终止机制，
    旨在以更高的效率和适应性，准确地解决具有挑战性的多众数查询问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1  # 协议所有阶段的时隙回复均为1bit

        # --- 协议超参数 ---
        # 阶段一：过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 32      # 当候选者数量小于此值时，停止过滤
        max_refinement_rounds = 5     # 最大精炼轮次，防止无限循环
        # 阶段二：验证相关
        verification_frame_size = 64
        # 父代B的自适应帧长调整参数
        high_density_threshold = 0.9
        low_density_threshold = 0.1
        min_frame_size = 128
        # --- 突变部分引入的全新超参数 ---
        stagnation_threshold = 0.05   # 候选集缩减率低于5%时，视为停滞
        min_rounds_before_stagnation_check = 2 # 至少执行2轮过滤再进行停滞检查

        # ==================================================================
        #  阶段一: 基于停滞感知的自适应迭代过滤
        # ==================================================================

        # --- 交叉部分：融合父代A的两阶段架构思想与父代B的自适应迭代过滤思想 ---
        # 本协议采纳了父代A经典的“先粗粒度过滤，后精确验证”的两阶段核心架构。
        # 在至关重要的过滤阶段，我们深度融合了父代B的精髓：通过多轮迭代通信，
        # 动态地精炼候选价格集合，并根据实时的帧碰撞率自适应地调整帧长。
        # 这种设计结合了父代A的结构清晰性与父代B在处理复杂数据分布时的灵活性与效率。

        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

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
            # 标签仅当其价格在当前候选集内时才响应
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            total_slots += current_filter_frame_size * num_hash_functions
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

            # 继承自父代B的帧碰撞率自适应调整逻辑
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

            # --- 突变部分：引入基于候选集缩减率的“精炼停滞”提前终止机制 ---
            # 这是父代A和B都未曾具备的全新优化。在每轮过滤后，读写器会评估本轮过滤的
            # “有效性”，即候选集缩小的幅度。如果候选集的数量相比上一轮缩减得非常缓慢
            # (例如，少于5%)，则协议判定过滤已达到瓶颈，继续迭代的收益很低。此时，
            # 协议将提前终止过滤阶段，直接进入验证阶段。这种机制避免了在过滤效果不佳时
            # 浪费宝贵的通信资源，显著增强了协议在面对“顽固”哈希碰撞时的执行效率。
            if refinement_rounds_done >= min_rounds_before_stagnation_check:
                if prev_candidate_count > 0:
                    reduction_ratio = (prev_candidate_count - len(candidate_prices)) / prev_candidate_count
                    if reduction_ratio < stagnation_threshold:
                        break # 过滤效果停滞，提前跳出循环

        # ==================================================================
        #  阶段二: 独立精确验证
        # ==================================================================

        # --- 交叉部分：融合父代A的独立精确验证思想 ---
        # 经过第一阶段高效的自适应过滤后，候选者数量已被大幅削减。此时，本协议
        # 回归到父代A和B共同采用的、最为精确可靠的验证模式：为每一个候选价格启动一次
        # 独立的验证通信，通过哈希每个标签唯一的ID来统计其在该价格上的非空时隙数。
        # 该方法确保了最终评分的准确性，实现了整体协议的准确性与效率的完美平衡。

        final_scores = {}
        for p in sorted(list(candidate_prices)): # 排序以保证执行顺序确定性
            total_frames += 1
            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += p.bit_length() + verification_frame_size.bit_length() + seed_verify.bit_length()

            # --- [物理层模拟] ---
            verification_slot_states = [0] * verification_frame_size
            for t in tags:
                if t.price == p:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    verification_slot_states[slot_id] += 1
            total_slots += verification_frame_size
            # --- [物理层模拟结束] ---

            # --- 读写器分析聚合信号 ---
            num_non_empty = sum(1 for count in verification_slot_states if count > 0)
            if num_non_empty > 0:
                final_scores[p] = num_non_empty

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "initial_filter_frame_size": initial_filter_frame_size,
            "refinement_rounds_done": refinement_rounds_done,
            "final_filter_frame_size": current_filter_frame_size,
            "num_final_candidates": len(final_scores),
            "verification_frame_size": verification_frame_size,
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = max(final_scores.values())
        modes = sorted([
            price for price, score in final_scores.items()
            if score == max_score
        ])

        return modes, max_score, total_time_ms, verification_data
