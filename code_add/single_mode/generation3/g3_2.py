import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g3_2(IProtocol):
    """
    竞争性剪枝众数查询协议 (Competitive Pruning Mode Query Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTERING_FRAME_SIZE = 1024      # 过滤阶段帧长 (源于父代B)
        SCORING_FRAME_SIZE = 128         # 单个价格评分的帧长 (源于父代A/B)
        GROUP_CHECK_FRAME_SIZE = 256     # 组检查帧长 (突变部分)

        # --- 交叉部分：融合父代B(g2_5)的“过滤-评分”模型与父代A(ModeQueryTCS)的“启发式评分”核心 ---

        # 阶段一：全局槽位过滤 (Inherited from Parent B: g2_5)
        # 采用父代B的确定性槽位过滤机制，通过一次通信快速识别出所有可能存在标签的价格，
        # 形成一个候选列表，避免了父代A对全价格范围的暴力扫描。
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTERING_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        filtering_frame = Frame(num_slots=FILTERING_FRAME_SIZE, bits_per_slot=1)
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTERING_FRAME_SIZE
            filtering_frame.aggregate(slot_id, 1)
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += FILTERING_FRAME_SIZE * 1
        
        active_slot_indices = {i for i, data in enumerate(filtering_frame.slots) if data > 0}
        
        candidate_prices = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTERING_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)

        # 阶段二：迭代评分与前瞻性剪枝
        # 对过滤出的候选价格，借鉴父代A的思想，使用“非空时隙数”作为启发式分数。
        price_to_score_map = {}
        current_max_score = 0
        
        remaining_candidates = sorted(list(candidate_prices))
        terminated_early = False

        # --- 突变部分：引入“竞争性提前终止”全新机制 ---
        # 两个父代协议都需要对所有候选者（或所有价格）完成探测后才能决策。
        # 本协议引入一个核心创新：在逐个评分的过程中，动态地对所有“剩下”的候选者进行一次
        # 低成本的“集体点名”。通过这次集体点名的结果（非空时隙数），可以得到剩余群体规模的
        # 一个上限。如果这个上限已经低于当前找到的最高分，就意味着剩下的任何单个价格都
        # 不可能超越当前胜出者，因此可以立即终止评分阶段，节省大量通信时间。
        
        while remaining_candidates:
            # 1. 对下一个候选者进行单独评分
            price_to_score = remaining_candidates.pop(0)
            
            total_frames += 1
            scoring_seed = generate_random_seed()
            total_bits_sent_by_reader += (price_to_score.bit_length() + SCORING_FRAME_SIZE.bit_length() + scoring_seed.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price_to_score:
                    slot_id = simple_hash(t.id, scoring_seed) % SCORING_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1
            
            score = sum(1 for count in slot_states if count > 0)
            if score > 0:
                price_to_score_map[price_to_score] = score
                if score > current_max_score:
                    current_max_score = score
            
            # 2. 如果还有剩余候选者，则执行前瞻性剪枝检查
            if not remaining_candidates:
                break

            total_frames += 1
            group_seed = generate_random_seed()
            total_bits_sent_by_reader += (GROUP_CHECK_FRAME_SIZE.bit_length() + group_seed.bit_length())
            
            # --- [物理层模拟 - 组检查] ---
            group_slot_states = [0] * GROUP_CHECK_FRAME_SIZE
            remaining_set = set(remaining_candidates)
            for t in tags:
                if t.price in remaining_set:
                    slot_id = simple_hash(t.id, group_seed) % GROUP_CHECK_FRAME_SIZE
                    group_slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += GROUP_CHECK_FRAME_SIZE * 1
            
            # 剩余群体的分数上限
            group_score_upper_bound = sum(1 for count in group_slot_states if count > 0)

            # 3. 决策：是否提前终止
            if group_score_upper_bound < current_max_score:
                terminated_early = True
                break

        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        if not price_to_score_map:
            return [], 0, total_time_ms, {"candidates": candidate_prices, "scores": {}}

        # 最终决策时，仍使用已记录的最高分，以防万一
        final_max_score = max(price_to_score_map.values())
        modes = sorted([p for p, score in price_to_score_map.items() if score == final_max_score])

        verification_data = {
            "candidates_after_filtering": len(candidate_prices),
            "prices_scored": len(price_to_score_map),
            "terminated_early": terminated_early,
            "final_scores": price_to_score_map
        }

        return modes, final_max_score, total_time_ms, verification_data
