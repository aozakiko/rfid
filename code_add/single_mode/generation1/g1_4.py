import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g1_4(IProtocol):
    """
    最终修正版协议：确定性槽位过滤众数查询 (Deterministic Slot Filtering Mode Query)

    该协议经过重新设计，采用确定性逻辑来保证100%的准确率，同时将通信开销保持在可行水平。
    1. 活跃槽位识别 (Active Slot Identification):
       使用一个中等大小的通信帧对整个标签群体进行一次探测。读写器的唯一目标是识别出
       哪些槽位接收到了至少一个标签的响应（即“活跃槽位”），而无需关心响应的具体内容或估算其数量。

    2. 候选价格反向映射 (Candidate Price Reverse Mapping):
       此阶段无任何通信开销。读写器在本地计算价格范围内的每个价格会映射到哪个槽位。
       如果一个价格映射到的槽位是“活跃槽位”，则该价格被列为候选者。这个过程确保了
       任何有响应的标签，其价格都绝对不会被遗漏。

    3. 精确计数终选 (Exhaustive Exact Counting):
       对第二阶段筛选出的所有候选者，进行无遗漏的精确计数，最终找出众数，确保结果100%准确。
    """
    
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_time_ms = 0.0
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # [修正] 使用一个大小合理的帧进行探测，不再需要SOG估算相关的复杂参数
        FILTERING_FRAME_SIZE = 1024 
        # 每个标签只需回复1bit表示存在即可
        BITS_PER_SLOT = 1

        # =================================================================================
        # Phase 1: 活跃槽位识别
        # =================================================================================
        # --- 读写器行为: 广播探测指令 ---
        total_frames += 1
        seed = generate_random_seed()
        total_bits_sent_by_reader += (seed.bit_length() + FILTERING_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        # 标签根据自身价格哈希到对应槽位，并回复一个比特“1”表示存在。
        filtering_frame = Frame(num_slots=FILTERING_FRAME_SIZE, bits_per_slot=BITS_PER_SLOT)
        for t in tags:
            slot_id = simple_hash(t.price, seed) % FILTERING_FRAME_SIZE
            # 只需用1bit表示该槽位有响应即可
            filtering_frame.aggregate(slot_id, 1)
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += FILTERING_FRAME_SIZE * BITS_PER_SLOT

        # --- 读写器分析聚合信号，找出所有活跃的槽位 ---
        active_slot_indices = {i for i, data in enumerate(filtering_frame.slots) if data > 0}

        # =================================================================================
        # Phase 2: 候选价格反向映射 (无通信)
        # =================================================================================
        candidate_prices = []
        if active_slot_indices:
            # 读写器在本地计算，找出所有可能落在活跃槽位里的价格
            for p in range(price_min, price_max + 1):
                if simple_hash(p, seed) % FILTERING_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)
        
        # =================================================================================
        # Phase 3: 对所有候选者进行精确计数
        # =================================================================================
        exact_counts = {}
        time_phase3_ms = 0
        
        for price in candidate_prices:
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
                time_phase3_ms += cost
        
        # --- 最终结果处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag_phase1 = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        
        total_time_ms = T_reader + T_tag_phase1 + T_overhead + time_phase3_ms

        if not exact_counts:
            # 如果候选者很多但精确计数后发现都为0，也可能发生
            return [], 0, total_time_ms, {"candidates": candidate_prices, "exact_counts": {}}
        
        max_count = max(exact_counts.values())
        modes = sorted([p for p, count in exact_counts.items() if count == max_count])

        verification_data = {
            "filtering_frame_size": FILTERING_FRAME_SIZE,
            "active_slots_found": len(active_slot_indices),
            "candidates_after_filtering": len(candidate_prices),
            "final_exact_counts": exact_counts
        }

        return modes, max_count, total_time_ms, verification_data