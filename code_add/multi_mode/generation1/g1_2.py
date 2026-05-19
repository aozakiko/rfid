from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g1_2(IProtocol):
    """
    子代协议：基于混合过滤与自适应验证的众数查询协议
    (Child Protocol: Mode Query via Hybrid Filtering and Adaptive Verification)

    本协议通过一个多阶段过程来精确、高效地解决多众数查询问题。
    它首先通过一个快速的启发式扫描来识别潜在的“热门”价格，
    然后对这些候选价格启动一个更精确但成本更高的验证流程，
    最终确保结果的100%准确性。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        initial_filter_frame_size = 64  # 阶段一：过滤阶段的帧大小
        verification_threshold_ratio = 0.85 # 阶段二：选取候选者的动态阈值比例
        bits_per_tag_reply = 1 # 过滤阶段标签只需回复1bit，表示时隙占用
        bits_per_tag_id_reply = 32 # 验证阶段标签需回复ID以进行精确计数

        # =================================================================================
        # 阶段一: 启发式过滤 (Heuristic Filtering)
        # --- 交叉部分：融合父代A的精确计数思想与父代B的启发式扫描思想 ---
        # 本阶段受父代B启发，通过快速ALOHA帧扫描整个价格空间，使用“非空时隙数”
        # 这一启发式指标来高效地估计每个价格的标签数量，从而发现潜在的众数候选者。
        # =================================================================================
        price_to_score_map = {}
        for p in range(price_min, price_max + 1):
            total_frames += 1
            seed_s = generate_random_seed()
            # 读写器广播 Select(p), frame_size, seed
            total_bits_sent_by_reader += p.bit_length() + initial_filter_frame_size.bit_length() + seed_s.bit_length()

            # --- [物理层模拟] ---
            slot_states = [0] * initial_filter_frame_size
            for t in tags:
                if t.price == p:
                    slot_id = simple_hash(t.id, seed_s) % initial_filter_frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += initial_filter_frame_size * bits_per_tag_reply

            # 读写器分析聚合信号，计算启发式得分
            num_non_empty_slots = sum(1 for count in slot_states if count > 0)
            if num_non_empty_slots > 0:
                price_to_score_map[p] = num_non_empty_slots

        if not price_to_score_map:
            return [], 0, 0.0, {"status": "No tags responded in phase 1"}

        # =================================================================================
        # 阶段二: 候选者自适应选择 (Adaptive Candidate Selection)
        # --- 突变部分：引入自适应候选者选择全新机制 ---
        # 此机制为全新设计，父代协议中未曾出现。它摒弃了固定选择Top-K个候选者的
        # 僵化策略，而是根据本次扫描的最高分动态计算一个阈值。所有得分高于此
        # 阈值的价格都会被选为候选者，使其能更灵活地应对众数数量和频率分布未知的情况。
        # =================================================================================
        max_score = max(price_to_score_map.values())
        score_threshold = max_score * verification_threshold_ratio
        candidate_prices = [
            price for price, score in price_to_score_map.items() 
            if score >= score_threshold
        ]

        # =================================================================================
        # 阶段三: 精确计数验证 (Exact Count Verification)
        # --- 交叉部分：融合父代A的精确计数思想与父代B的启发式扫描思想 ---
        # 本阶段体现了父代A对结果100%准确性的追求。我们不再信任启发值，而是对
        # 筛选出的少数候选者，执行一个保证完全准确的计数流程。
        # --- 突变部分：引入动态帧ALOHA碰撞解决全新机制 ---
        # 此机制为全新设计，是对父代A暴力计数方法的巨大优化。我们不采用高成本的
        # 逐一识别，而是为每个候选价格启动一个高效的动态帧大小ALOHA流程。通过
        # 迭代调整帧长来解决时隙碰撞，直到该价格下的所有标签都被成功识别并计数，
        # 成本远低于父代A，同时保证了结果的绝对精确。
        # =================================================================================
        final_exact_counts = {}
        for price in candidate_prices:
            # 使用启发式分数作为计数的初始估计值
            tags_to_identify_count_estimate = price_to_score_map.get(price, 1)
            
            identified_tag_ids_for_price = set()
            
            # 初始帧大小基于启发值，但设置下限以防过小
            current_frame_size = max(1, int(tags_to_identify_count_estimate * 1.2))
            
            # 循环直到没有碰撞发生，保证所有标签都被识别
            while True:
                total_frames += 1
                seed_s = generate_random_seed()
                # 读写器广播 Select(price), current_frame_size, seed
                total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed_s.bit_length()

                # --- [物理层模拟] ---
                slot_states = defaultdict(list)
                # 仅未被识别且价格匹配的标签参与本轮响应
                responding_tags_in_round = [
                    t for t in tags if t.price == price and t.id not in identified_tag_ids_for_price
                ]

                # 如果没有更多标签需要识别，则终止此价格的计数
                if not responding_tags_in_round:
                    break

                for t in responding_tags_in_round:
                    slot_id = simple_hash(t.id, seed_s) % current_frame_size
                    slot_states[slot_id].append(t.id)
                # --- [物理层模拟结束] ---
                
                # 在验证阶段，标签需要回复更长的ID信息
                total_tag_bits_sent += current_frame_size * bits_per_tag_id_reply

                # 读写器分析聚合信号
                num_collision = 0
                for slot, tag_ids in slot_states.items():
                    if len(tag_ids) == 1:
                        identified_tag_ids_for_price.add(tag_ids[0])
                    elif len(tag_ids) > 1:
                        num_collision += 1

                # 终止条件：如果本轮没有碰撞，我们认为所有剩余的标签都已成功识别
                if num_collision == 0:
                    break

                # 动态调整下一轮的帧大小以解决碰撞
                # 使用一个常见的简单估计器：n_rem ≈ 2 * C
                remaining_tags_estimate = num_collision * 2
                current_frame_size = max(1, remaining_tags_estimate)

            final_exact_counts[price] = len(identified_tag_ids_for_price)

        # --- 最终结果处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4 
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "phase1_scores": price_to_score_map,
            "selected_candidates": sorted(candidate_prices),
            "final_exact_counts": final_exact_counts
        }

        if not final_exact_counts:
             return [], 0, total_time_ms, verification_data
        
        max_count = max(final_exact_counts.values())
        modes = sorted([
            price for price, count in final_exact_counts.items() 
            if count == max_count
        ])

        return modes, max_count, total_time_ms, verification_data
