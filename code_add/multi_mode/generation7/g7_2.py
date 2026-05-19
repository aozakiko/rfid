import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g7_2(IProtocol):
    """
    子代协议: 具有精确估算的混合自适应协议
    (Hybrid Adaptive Protocol with Refined Estimation)

    本协议是一个全新的子代协议，旨在高效且准确地解决具有挑战性的多众数查询问题。
    它深度融合了两个父代协议的优点，并引入了全新的“碰撞感知分数精炼”机制作为其突变，
    以在复杂场景下提供更准确的众数估计。

    协议流程:
    1.  第一阶段 (自适应过滤): 采用父代A的迭代式自适应帧长调整策略，结合父代B的多哈希过滤思想，
        快速、动态地将价格候选集缩小到一个很小的范围。
    2.  第二阶段 (精确验证与分数精炼): 对第一阶段产生的少数候选者，采用父代B的为每个候选者
        启动独立验证帧的鲁棒方法。在此基础上，引入全新的分数计算逻辑（突变），通过同时
        考虑“非空时隙”和“碰撞时隙”来更精确地估算每个候选价格的标签数量。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        # 协议所有阶段的时隙回复均为1bit
        bits_per_slot = 1

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 128
        num_hash_functions = 2
        candidate_threshold = 20      # 当候选者数量小于此值时，停止过滤
        max_refinement_rounds = 5     # 最大过滤轮次
        high_density_threshold = 0.8  # 帧密度过高阈值
        low_density_threshold = 0.3   # 帧密度过低阈值
        min_frame_size = 32

        # 阶段二: 验证相关
        verification_frame_size = 128

        # ==================================================================
        #  阶段一: 基于碰撞率的自适应迭代过滤
        # --- 交叉部分：融合父代A的自适应帧长调整思想与父代B的多哈希过滤流程 ---
        # 本阶段完整地继承了父代A的“迭代式过滤”和“基于帧密度动态调整帧长”的核心架构。
        # 同时，其内部的过滤逻辑——即一个价格必须通过多个哈希函数的检验才能成为候选者——
        # 也正是父代B的核心过滤思想。这种融合使得过滤阶段既高效又具有动态适应性。
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + filter_seeds[0].bit_length())

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            total_slots += current_filter_frame_size * num_hash_functions
            # --- [物理层模拟结束] ---

            # 读写器根据聚合信号（帧状态）筛选候选者
            new_candidates = {p for p in candidate_prices if all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % current_filter_frame_size] > 0
                for i in range(num_hash_functions)
            )}
            
            # 如果没有候选者幸存，则提前终止
            if not new_candidates:
                break
            candidate_prices = new_candidates
            refinement_rounds_done += 1

            # 读写器分析帧密度以自适应调整帧长
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

        # ==================================================================
        #  阶段二: 独立验证与碰撞感知分数精炼
        # --- 交叉部分：采用父代B为每个候选者启动独立验证帧的精确评估思想 ---
        # 与父代A的并行位图验证不同，本阶段借鉴了父代B更为鲁棒的验证策略：
        # 为每一个通过过滤的候选价格p启动一次独立的帧通信。这确保了不同价格的
        # 标签不会在验证阶段互相干扰，从而为精确估算数量打下基础。
        # ==================================================================
        final_scores: Dict[int, int] = {}
        
        for p in candidate_prices:
            # --- 读写器行为: 广播对候选价格 p 的探测指令 ---
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

            # --- 突变部分：引入基于“非空时隙+碰撞时隙”的全新分数精炼机制 ---
            # 这是对父代B验证逻辑的重大优化。父代B仅使用“非空时隙数”作为分数，
            # 这在标签数量多、碰撞严重时会严重低估真实数量。本协议引入了一种更精确的
            # 估算方法（Lower Bound Estimator的一种简化形式），通过将“非空时隙数”
            # 与“碰撞时隙数”（即响应标签数>1的时隙）相加，得到一个更接近真实值的
            # 分数，从而在面对频率相近的众数时，能做出更准确的区分。
            num_non_empty = sum(1 for count in verification_slot_states if count > 0)
            if num_non_empty > 0:
                num_collisions = sum(1 for count in verification_slot_states if count > 1)
                # 精炼后的分数为：非空时隙数 + 碰撞时隙数
                refined_score = num_non_empty + num_collisions
                final_scores[p] = refined_score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "refinement_rounds_done": refinement_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, verification_data
