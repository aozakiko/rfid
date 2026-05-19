import math
from typing import List, Tuple, Dict
from collections import deque, defaultdict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g1_5(IProtocol):
    """
    最终修正版协议：确定性分治众数查询 (Deterministic Divide-and-Conquer Mode Query)

    本协议经过重新设计，以100%准确率为首要目标，采用确定性逻辑进行众数查找。
    1. 确定性分治筛选 (Deterministic Divide-and-Conquer Filtering):
       采用严谨的分治策略，通过二分搜索递归地探测价格区间。一个区间只有在被证实
       包含至少一个标签响应时（即存在非空时隙），才会被进一步细分。此过程持续进行，
       直到所有存在标签的单一价格点被精确地分离出来，形成一个完备的候选集。

    2. 精确计数终选 (Exhaustive Exact Counting):
       对第一阶段筛选出的所有候选者，进行无一遗漏的精确计数，最终根据这些绝对准确
       的计数值来确定众数，确保结果100%正确。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        HBS_FRAME_SIZE = 64
        # [修正] 移除了所有导致不确定性的参数: MIN_RANGE_SIZE_FOR_SEARCH, BRANCH_FACTOR, TOP_K_CANDIDATES

        # =================================================================================
        # Phase 1: 确定性分治筛选
        # =================================================================================
        candidate_prices = []
        # 使用双端队列进行广度优先搜索
        queue = deque([(price_min, price_max)])

        while queue:
            current_min, current_max = queue.popleft()
            
            # --- 读写器行为: 广播对价格区间的探测指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + HBS_FRAME_SIZE.bit_length() + 
                                          current_min.bit_length() + current_max.bit_length())

            # --- [物理层模拟] ---
            # 读写器无法访问标签私有数据。此代码块模拟所有标签的匿名、聚合响应。
            # 读写器能观察到的最终结果是 slot_states 数组。
            slot_states = [0] * HBS_FRAME_SIZE  # 0: 空闲, 1: 成功, >1: 冲突
            for t in tags:
                if current_min <= t.price <= current_max:
                    slot_id = simple_hash(t.id, seed) % HBS_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            # 探测阶段，每个时隙回复1bit即可
            total_tag_bits_sent += HBS_FRAME_SIZE * 1

            # --- 读写器分析聚合信号 ---
            # [修正] 使用更鲁棒的“非空时隙数”作为判断依据
            num_non_empty_slots = sum(1 for count in slot_states if count > 0)
            
            # [修正] 核心逻辑：只有当区间内确实有标签响应时，才进行处理
            if num_non_empty_slots > 0:
                # 如果区间已缩小到单个价格，那么它就是一个有效的候选者
                if current_min == current_max:
                    candidate_prices.append(current_min)
                else:
                    # 否则，将其两个子区间加入队列继续探索
                    mid = (current_min + current_max) // 2
                    queue.append((current_min, mid))
                    if mid + 1 <= current_max:
                        queue.append((mid + 1, current_max))

        # 去重，以防万一（虽然在此逻辑中不太可能出现重复）
        final_candidate_prices = sorted(list(set(candidate_prices)))

        # =================================================================================
        # Phase 2: 对所有候选者进行精确计数
        # =================================================================================
        exact_counts = {}
        time_phase2_ms = 0
        
        for price in final_candidate_prices:
            # --- [物理层模拟] ---
            # 读写器发起精确计数请求，物理层返回聚合的计数值和该过程的开销。
            count_for_price = sum(1 for t in tags if t.price == price)
            
            cost = 0.0
            if count_for_price > 0:
                # 模拟逐一识别(Singulation)开销
                time_per_tag = (16 * 0.025) + (32 * 0.4) + 30.0 # T_reader + T_tag_reply + T_overhead_per_tag
                cost = count_for_price * time_per_tag
            # --- [物理层模拟结束] ---
            
            # --- 读写器接收并记录结果 ---
            if count_for_price > 0:
                exact_counts[price] = count_for_price
                time_phase2_ms += cost
        
        # --- 最终结果处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag_phase1 = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag_phase1 + T_overhead + time_phase2_ms

        if not exact_counts:
            return [], 0, total_time_ms, {"candidates": final_candidate_prices, "exact_counts": {}}
        
        max_count = max(exact_counts.values())
        modes = sorted([p for p, count in exact_counts.items() if count == max_count])

        verification_data = {
            "HBS_frame_size": HBS_FRAME_SIZE,
            "candidates_after_filtering": final_candidate_prices,
            "final_exact_counts": exact_counts
        }

        return modes, max_count, total_time_ms, verification_data