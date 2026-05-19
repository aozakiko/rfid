import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g4_1(IProtocol):
    """
    子代协议：基于碰撞率自适应的混合众数查询协议 (Hybrid Adaptive Collision-aware Protocol)

    本协议旨在高效且准确地解决具有挑战性的多众数查询问题。
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
        candidate_threshold = 32  # 当候选者数量小于此值时，停止过滤，进入验证
        max_refinement_rounds = 5 # 最大精炼轮次，防止无限循环
        # 阶段二：验证相关
        verification_frame_size = 64
        # --- 突变部分引入的超参数 ---
        high_density_threshold = 0.9  # 帧密度过高阈值
        low_density_threshold = 0.1   # 帧密度过低阈值
        min_frame_size = 128          # 过滤帧的最小尺寸

        # ==================================================================
        #  阶段一: 自适应迭代过滤
        # ==================================================================

        # --- 交叉部分：融合父代A的两阶段架构与父代B的自适应迭代过滤思想 ---
        # 本协议采纳了父代A经典的“先粗粒度过滤，后精确验证”的两阶段架构。
        # 在过滤阶段，我们借鉴了父代B的核心亮点：通过多轮迭代，不断精炼候选价格集合。
        # 这种设计结合了父代A的结构清晰性与父代B在处理复杂数据分布时的灵活性。

        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 广播当前轮次的帧长和哈希种子
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

            # --- 突变部分：引入基于帧碰撞率的过滤帧长动态调整机制 ---
            # 这是父代A和B都未曾具备的全新机制。在每一轮过滤后，读写器会分析接收到的
            # 帧的“密度”（非空时隙占比）。
            # 1. 如果密度过高，说明碰撞严重，过滤效果差，下一轮将自动增大帧长以稀疏信号。
            # 2. 如果密度过低，说明帧长过于浪费，下一轮将自动减小帧长以节约通信开销。
            # 这种“感知-调整”的闭环优化，使得协议能根据实时反馈动态调整关键参数，
            # 显著提升了在不同数据分布下的适应性和效率。
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)
            # 如果密度在健康区间，则帧长保持不变

        # ==================================================================
        #  阶段二: 独立精确验证
        # ==================================================================

        # --- 交叉部分：融合父代A的独立精确验证思想 ---
        # 在候选者数量被大幅削减后，本协议回归到父代A的验证模式。
        # 为每一个候选价格启动一次独立的验证通信，通过哈希标签ID来统计非空时隙数。
        # 这种方法虽然单位成本高于父代B的并行验证，但其评分结果更为精确可靠，
        # 且由于候选者极少，总体验证开销得到了有效控制，实现了准确性与效率的平衡。

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
            "final_filter_frame_size": current_filter_frame_size, # 记录最终帧长
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
