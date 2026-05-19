import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g2_4(IProtocol):
    """
    迭代过滤众数查询协议 (Iterative Filtering Mode Query Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        COARSE_FILTER_FRAME_SIZE = 256 # 第一次粗过滤的帧大小
        REFINEMENT_THRESHOLD = 150 # 触发二次精细过滤的候选价格数量阈值
        REFINED_FILTER_FRAME_SIZE = 2048 # 第二次精细过滤的帧大小
        BITS_PER_SINGULATION_QUERY = 96  # 精确盘点成本 (源于父代A)
        BITS_PER_PRICE_REPLY = 32      # 精确盘点成本 (源于父代A)

        # --- 交叉部分：融合父代A的“精确盘点”思想与父代B的“槽位过滤”思想 ---

        # 阶段一：初步槽位过滤 (Inherited from Parent B: g1_4)
        # 采用父代B的槽位过滤机制，使用一个较小的帧快速生成第一批候选价格列表。
        total_frames += 1
        coarse_seed = generate_random_seed()
        total_bits_sent_by_reader += (coarse_seed.bit_length() + COARSE_FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        coarse_frame = Frame(num_slots=COARSE_FILTER_FRAME_SIZE, bits_per_slot=1)
        for t in tags:
            slot_id = simple_hash(t.price, coarse_seed) % COARSE_FILTER_FRAME_SIZE
            coarse_frame.aggregate(slot_id, 1)
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += COARSE_FILTER_FRAME_SIZE * 1
        
        active_coarse_slots = {i for i, data in enumerate(coarse_frame.slots) if data > 0}
        
        coarse_candidates = []
        if active_coarse_slots:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, coarse_seed) % COARSE_FILTER_FRAME_SIZE in active_coarse_slots:
                    coarse_candidates.append(p)
        
        final_candidates = coarse_candidates
        
        # --- 突变部分：引入“自适应二次精炼过滤”全新机制 ---
        # 父代B只有一次过滤，如果价格分布导致大量哈希冲突，候选列表会很长，导致最终盘点成本高。
        # 本协议引入一个创新：如果第一次过滤产生的候选者数量过多，就启动一次额外的、更精细的过滤。
        # 这次过滤使用一个更大的帧，并且只针对第一轮筛选出的候选标签，从而以较小的代价进一步精确筛选，
        # 大幅降低最终高成本精确盘点的负担。
        
        refined_filter_ran = False
        if len(coarse_candidates) > REFINEMENT_THRESHOLD:
            refined_filter_ran = True
            total_frames += 1
            refined_seed = generate_random_seed()
            total_bits_sent_by_reader += (refined_seed.bit_length() + REFINED_FILTER_FRAME_SIZE.bit_length())

            # --- [物理层模拟] ---
            # 关键优化：标签只有在自己的价格属于上一轮的候选者时才响应
            refined_frame = Frame(num_slots=REFINED_FILTER_FRAME_SIZE, bits_per_slot=1)
            coarse_candidates_set = set(coarse_candidates)
            for t in tags:
                if t.price in coarse_candidates_set:
                    slot_id = simple_hash(t.price, refined_seed) % REFINED_FILTER_FRAME_SIZE
                    refined_frame.aggregate(slot_id, 1)
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += REFINED_FILTER_FRAME_SIZE * 1

            active_refined_slots = {i for i, data in enumerate(refined_frame.slots) if data > 0}
            
            # 从粗候选者中再次筛选
            refined_candidates = []
            if active_refined_slots:
                for p in coarse_candidates:
                    if simple_hash(p, refined_seed) % REFINED_FILTER_FRAME_SIZE in active_refined_slots:
                        refined_candidates.append(p)
            final_candidates = refined_candidates

        # 阶段三：对最终候选者进行精确盘点 (Inspired by Parent A: ModeQueryID)
        # 对经过一或两轮过滤筛选出的最终候选者，采用父代A的逐一识别模型进行精确计数，确保结果100%准确。
        exact_counts = {}
        for price in final_candidates:
            # --- [物理层模拟] ---
            count_for_price = sum(1 for t in tags if t.price == price)
            
            # 计算这次精确盘点的成本，借鉴父代A的模型
            if count_for_price > 0:
                exact_counts[price] = count_for_price
                # 每次成功的识别和读取都可视为一次独立的交互或“帧”
                total_frames += count_for_price
                total_bits_sent_by_reader += count_for_price * BITS_PER_SINGULATION_QUERY
                total_tag_bits_sent += count_for_price * BITS_PER_PRICE_REPLY
            # --- [物理层模拟结束] ---

        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        if not exact_counts:
            return [], 0, total_time_ms, {"candidates": final_candidates, "exact_counts": {}}
        
        max_count = max(exact_counts.values())
        modes = sorted([p for p, count in exact_counts.items() if count == max_count])

        verification_data = {
            "coarse_candidates_found": len(coarse_candidates),
            "refined_filter_ran": refined_filter_ran,
            "final_candidates_to_verify": len(final_candidates),
            "final_exact_counts": exact_counts
        }

        return modes, max_count, total_time_ms, verification_data
