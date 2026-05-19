import math
import random
from typing import List, Tuple, Dict
from collections import deque
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g1_2(IProtocol):
    """
    修正版协议：确定性分治众数查询 (Deterministic Divide-and-Conquer Mode Query)

    该协议通过两个确定性阶段来保证100%准确地查找众数价格:
    1.  分治候选识别 (Divide-and-Conquer Candidate Identification):
        采用严谨的分治策略，递归地对价格区间进行二分探测。一个区间只有在
        被证实包含至少一个标签响应时，才会被进一步细分。此过程持续进行，
        直到所有存在标签的单一价格点被精确识别出来。此阶段保证了候选集
        的完备性。
    2.  精确计数终选 (Final Exact Counting):
        对第一阶段识别出的所有候选价格，逐一进行精确的数量统计，最终找出
        数量最多的一个或多个价格作为众数。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_time_ms = 0.0
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_bits_sent_by_tags = 0

        # --- 协议超参数 ---
        # 阶段1参数
        HBS_FRAME_SIZE = 64
        # [修正] 移除了所有导致不确定性的参数，如 BRANCH_FACTOR 和 MIN_RANGE_SIZE_FOR_SEARCH

        # 阶段2参数 (模拟精确识别)
        SINGULATION_BITS_PER_TAG_QUERY = 16
        SINGULATION_BITS_PER_TAG_REPLY = 32

        # =================================================================================
        # Phase 1: Deterministic Candidate Identification
        # =================================================================================
        candidate_prices = []
        
        # 使用双端队列进行广度优先搜索来识别所有非空价格点
        queue = deque([(price_min, price_max)])

        while queue:
            current_min, current_max = queue.popleft()
            
            # --- 读写器行为: 广播对价格区间的存在性探测指令 ---
            total_frames += 1
            seed = generate_random_seed()
            
            bits_sent = seed.bit_length() + HBS_FRAME_SIZE.bit_length() + current_min.bit_length() + current_max.bit_length()
            total_bits_sent_by_reader += bits_sent

            # --- [物理层模拟] ---
            # 读写器无法访问标签私有数据。此代码块模拟所有标签根据自身数据和读写器指令
            # 在物理信道上产生的匿名、聚合响应。读写器只能观察到 slot_states 数组。
            slot_states = [0] * HBS_FRAME_SIZE  # 0: 空闲, 1: 成功, >1: 冲突
            for t in tags:
                if current_min <= t.price <= current_max:
                    slot_id = simple_hash(t.id, seed) % HBS_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_bits_sent_by_tags += HBS_FRAME_SIZE * 1

            # --- 读写器分析聚合信号 ---
            num_non_empty_slots = sum(1 for count in slot_states if count > 0)
            
            # [修正] 核心逻辑: 只有当区间内有标签响应时，才进行处理。
            # 这保证了我们只探索有意义的路径，但绝不错过任何一条。
            if num_non_empty_slots > 0:
                # 如果区间已经缩小到单个价格，那么这个价格就是一个有效的候选者
                if current_min == current_max:
                    candidate_prices.append(current_min)
                else:
                    # 如果区间仍然可以细分，则将其两个子区间加入队列继续探索
                    mid = (current_min + current_max) // 2
                    queue.append((current_min, mid))
                    if mid + 1 <= current_max:
                        queue.append((mid + 1, current_max))

        # 去重候选价格
        final_candidate_prices = sorted(list(set(candidate_prices)))

        # =================================================================================
        # Phase 2: Final Exact Counting
        # =================================================================================
        final_counts = {}
        for price in final_candidate_prices:
            
            # --- 读写器行为: 广播对特定价格的精确计数指令 ---
            total_frames += 1
            bits_sent = price.bit_length()
            total_bits_sent_by_reader += bits_sent

            # --- [物理层模拟] ---
            # 读写器广播计数请求后，物理层返回一个聚合的计数值。
            # 读写器无法得知是哪些标签响应，只能得到最终的数量。
            count_for_price = sum(1 for t in tags if t.price == price)
            # --- [物理层模拟结束] ---

            # --- 读写器接收并记录结果 ---
            num_tags_for_price = count_for_price
            final_counts[price] = num_tags_for_price
            
            # 模拟精确计数过程产生的通信开销
            if num_tags_for_price > 0:
                singulation_rounds = num_tags_for_price
                total_frames += singulation_rounds
                total_bits_sent_by_reader += singulation_rounds * SINGULATION_BITS_PER_TAG_QUERY
                total_bits_sent_by_tags += singulation_rounds * SINGULATION_BITS_PER_TAG_REPLY
        
        # --- 最终结果处理与时间计算 ---
        if not final_counts:
            return [], 0, 0.0, {}
        
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_bits_sent_by_tags * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        max_count = max(final_counts.values())
        modes = sorted([p for p, count in final_counts.items() if count == max_count])
        
        verification_data = {
            "HBS_frame_size": HBS_FRAME_SIZE,
            "final_candidates": final_candidate_prices,
            "final_exact_counts": final_counts
        }
        
        return modes, max_count, total_time_ms, verification_data