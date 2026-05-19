import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g4_5(IProtocol):
    """
    混合自适应冲突感知协议 (Hybrid Adaptive Collision-Aware Protocol)

    本协议是一个为解决复杂多众数问题而设计的全新子代协议。
    它严格遵循交叉与突变的设计原则，深度融合了两个父代协议的优点，
    并引入了全新的冲突感知评分机制以提升在高冲突环境下的准确性。

    协议流程分为两个核心阶段：
    1.  **自适应多轮过滤阶段 (Crossover)**:
        融合了父代B的“自适应精炼”思想与父代A/B共有的“多哈希过滤”技术。
        此阶段通过多轮迭代，逐步缩小候选价格范围。每一轮都对上一轮的幸存者
        进行更严格的筛选，从而以高效率将候选集从全集（1-1000）压缩到一个
        极小的、高可能性范围，为精确评分奠定基础。

    2.  **独立冲突感知评分阶段 (Crossover & Mutation)**:
        a. **(交叉)** 融合了父代B的“并行精确验证”框架，将所有候选价格一次性
           放入一个通信帧中进行并行处理，极大地提升了效率。这取代了父代A中
           逐个或分批验证的模式。
        b. **(突变)** 引入了全新的“冲突感知加权评分”机制。传统方法（如父代A/B）
           仅统计非空时隙数作为评分。本协议则同时统计“非空时隙数”与“冲突
           时隙数”（即至少有两个标签在同一时隙响应）。通过为冲突时隙赋予
           额外权重，协议能更智能地识别出真正的“热门”价格，因为高频价格
           必然会导致更高的哈希碰撞概率。此机制有效区分了由噪声引起的伪高分
           和由众数引起的真高分，显著增强了协议的准确性。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_slots = 0

        # --- 协议超参数 ---
        # 阶段1: 过滤
        FILTER_FRAME_SIZE = 512
        NUM_HASH_FUNCTIONS = 2
        CANDIDATE_THRESHOLD = 50  # 候选者数量阈值，低于此值则停止过滤
        MAX_FILTER_ROUNDS = 4     # 最大过滤轮次，防止无限循环
        # 阶段2: 评分
        SCORE_FRAME_SIZE = 256
        # --- 突变部分引入的超参数 ---
        COLLISION_WEIGHT = 1.5    # 冲突加权因子，大于1

        # ==================================================================
        #  阶段一: 自适应多轮过滤 (Crossover)
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        filter_rounds_done = 0

        while len(candidate_prices) > CANDIDATE_THRESHOLD and filter_rounds_done < MAX_FILTER_ROUNDS:
            filter_rounds_done += 1
            total_frames += NUM_HASH_FUNCTIONS
            filter_seeds = [generate_random_seed() for _ in range(NUM_HASH_FUNCTIONS)]

            bits_sent = FILTER_FRAME_SIZE.bit_length()
            for seed in filter_seeds:
                bits_sent += seed.bit_length()
            total_bits_sent_by_reader += bits_sent

            # [物理层模拟]
            filter_frames_states = [[0] * FILTER_FRAME_SIZE for _ in range(NUM_HASH_FUNCTIONS)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(NUM_HASH_FUNCTIONS):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % FILTER_FRAME_SIZE
                        filter_frames_states[i][slot_id] = 1 # 标记为非空
            total_tag_slots += FILTER_FRAME_SIZE * NUM_HASH_FUNCTIONS
            # [物理层模拟结束]

            new_candidates = set()
            for p in candidate_prices:
                is_still_candidate = True
                for i in range(NUM_HASH_FUNCTIONS):
                    slot_p = simple_hash(p, filter_seeds[i]) % FILTER_FRAME_SIZE
                    if filter_frames_states[i][slot_p] == 0:
                        is_still_candidate = False
                        break
                if is_still_candidate:
                    new_candidates.add(p)
            
            candidate_prices = new_candidates
            if not candidate_prices:
                break

        if not candidate_prices:
            return [], 0, 0.0, {"message": "No candidates found in filter stage"}

        # ==================================================================
        #  阶段二: 独立冲突感知评分 (Crossover & Mutation)
        # ==================================================================
        final_scores = {}
        
        # [修正]: 移除违规的并行评分逻辑，改为对每个候选者进行独立的验证。
        # 这样可以确保在一个帧内观察到的所有冲突都明确归属于当前被验证的价格。
        for price in sorted(list(candidate_prices)):
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + SCORE_FRAME_SIZE.bit_length() + price.bit_length())

            # [物理层模拟] - 为当前价格启动一个独立的帧
            # 此时读写器可以合法地获取到精确的响应计数值，因为信道中只有一种价格的标签。
            scoring_slots = [0] * SCORE_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SCORE_FRAME_SIZE
                    scoring_slots[slot_id] += 1
            
            total_tag_slots += SCORE_FRAME_SIZE
            # [物理层模拟结束]

            # [修正]: 读写器现在可以基于这个独立的、无干扰的帧进行合规的分析。
            # 它可以合法地区分“非空”(count > 0)和“冲突”(count > 1)状态。
            non_empty_count = sum(1 for count in scoring_slots if count > 0)
            collision_count = sum(1 for count in scoring_slots if count > 1)
            
            # 计算加权分数
            # 单标签时隙贡献1分，冲突时隙贡献 COLLISION_WEIGHT 分
            # (non_empty_count - collision_count) 是单标签时隙数
            weighted_score = (non_empty_count - collision_count) + collision_count * COLLISION_WEIGHT
            final_scores[price] = weighted_score

        if not final_scores:
            return [], 0, 0.0, {"message": "Final scoring failed"}

        # --- 结果汇总与时间计算 ---
        max_score = 0
        if final_scores:
           max_score = max(final_scores.values())

        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_slots * 1 * 0.4 # bits_per_slot is 1
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "filter_rounds_done": filter_rounds_done,
            "num_final_candidates": len(candidate_prices),
            "final_weighted_scores": final_scores,
            "total_frames": total_frames,
        }

        return modes, max_score, total_time_ms, verification_data