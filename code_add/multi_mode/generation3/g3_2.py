import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g3_2(IProtocol):
    """
    子代协议：自适应混合众数查询协议 (Hybrid Adaptive Mode Protocol)

    本协议通过深度融合父代协议A和B的核心思想，并引入全新的自适应优化机制，
    旨在高效、准确地解决大规模标签环境下的多众数查询问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        initial_filter_frame_size = 512
        num_hash_functions = 2
        verification_frame_size = 128
        # --- 突变部分引入的超参数 ---
        candidate_threshold = 64  # 如果候选者数量超过此阈值，则启动自适应精炼
        max_refinement_rounds = 3 # 防止无限循环

        # ==================================================================
        #  阶段一: 初始布隆过滤与自适应精炼
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        
        refinement_rounds_done = 0
        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # Assuming 32-bit seeds for calculation
            bits_sent_per_hash = initial_filter_frame_size.bit_length() + 32 * num_hash_functions
            total_bits_sent_by_reader += bits_sent_per_hash

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * initial_filter_frame_size for _ in range(num_hash_functions)]
            
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % initial_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            
            total_tag_reply_bits += initial_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---
            
            # --- 读写器分析聚合信号，生成新的候选集 ---
            new_candidates = set()
            for p in candidate_prices:
                is_candidate_in_round = True
                for i in range(num_hash_functions):
                    slot_p = simple_hash(p, filter_seeds[i]) % initial_filter_frame_size
                    if filter_frames_states[i][slot_p] == 0:
                        is_candidate_in_round = False
                        break
                if is_candidate_in_round:
                    new_candidates.add(p)
            
            if not new_candidates:
                break
            candidate_prices = new_candidates
            refinement_rounds_done += 1

        # ==================================================================
        #  阶段二: 并行精确验证
        # ==================================================================
        final_scores = {}
        num_final_candidates = len(candidate_prices)

        # ***** START: MODIFICATION FOR VERIFICATION TIME CALCULATION *****
        # 初始化验证阶段的专用成本计数器
        verification_frames = 0
        verification_bits_sent_by_reader = 0
        verification_tag_reply_bits = 0
        # ***** END: MODIFICATION FOR VERIFICATION TIME CALCULATION *****

        if candidate_prices:
            # ***** START: MODIFICATION FOR VERIFICATION TIME CALCULATION *****
            verification_frames += 1
            # ***** END: MODIFICATION FOR VERIFICATION TIME CALCULATION *****
            
            candidate_list = sorted(list(candidate_prices)) # 确保顺序一致
            price_to_bit_pos_map = {price: i for i, price in enumerate(candidate_list)}
            
            seed_verify = generate_random_seed()
            
            # ***** START: MODIFICATION FOR VERIFICATION TIME CALCULATION *****
            verification_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                                 seed_verify.bit_length() +
                                                 sum(p.bit_length() for p in candidate_list))
            # ***** END: MODIFICATION FOR VERIFICATION TIME CALCULATION *****

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)
            
            # ***** START: MODIFICATION FOR VERIFICATION TIME CALCULATION *****
            verification_tag_reply_bits += verification_frame_size * num_final_candidates
            # ***** END: MODIFICATION FOR VERIFICATION TIME CALCULATION *****
            # --- [物理层模拟结束] ---
            
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots]
                score = sum(logical_bit_vector)
                if score > 0:
                    final_scores[price] = score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        
        # ***** START: MODIFICATION FOR VERIFICATION TIME CALCULATION *****
        # 计算并打印验证阶段的执行时间
        T_reader_verify = verification_bits_sent_by_reader * 0.025
        T_tag_verify = verification_tag_reply_bits * 0.4
        T_overhead_verify = (verification_frames - 1) * 30 if verification_frames > 0 else 0
        verification_time_ms = T_reader_verify + T_tag_verify + T_overhead_verify
        print(f"Verification Phase Execution Time: {verification_time_ms:.4f} ms")

        # 将验证阶段的成本加回到总成本中
        total_frames += verification_frames
        total_bits_sent_by_reader += verification_bits_sent_by_reader
        total_tag_reply_bits += verification_tag_reply_bits
        # ***** END: MODIFICATION FOR VERIFICATION TIME CALCULATION *****

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_reply_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "initial_filter_frame_size": initial_filter_frame_size,
            "num_hash_functions": num_hash_functions,
            "candidate_threshold": candidate_threshold,
            "refinement_rounds_done": refinement_rounds_done,
            "num_final_candidates": num_final_candidates,
            "verification_frame_size": verification_frame_size,
            "final_scores": final_scores,
            "verification_time_ms": verification_time_ms # Optionally add to debug data
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())

        modes = sorted([
            price for price, score in final_scores.items()
            if score == max_score
        ])

        return modes, max_score, total_time_ms, verification_data