import math
import random
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g1_3(IProtocol):
    """
    修正版协议：确定性候选筛选众数查询 (Deterministic Candidate Filtering Mode Query)

    该协议通过一个两阶段的“全员海选-精确终选”策略，高效且100%精确地查找众数:
    1. 并行估算筛选 (Deterministic Candidate Filtering):
       利用并行估算技术（如SEM），对整个价格范围进行一次快速扫描。此阶段的唯一目的
       是确定性地筛选出所有响应标签数量大于0的价格，形成一个完整的候选集。
       任何估算值大于0（包括饱和）的价格都会被纳入候选。

    2. 精确计数终选 (Exhaustive Exact Counting):
       对第一阶段筛选出的所有候选者，无一遗漏地进行精确的逐一识别和计数。
       最终根据这些绝对准确的计数值来确定众数，确保结果100%正确。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_time_ms = 0.0
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0
        
        # --- 协议超参数 ---
        SEM_BATCH_SIZE = 100
        SEM_FRAME_SIZE = 512
        # [修正] 移除了不必要的 TOP_K_CANDIDATES 和 ADAPTIVE_FRAME_SIZE

        # =================================================================================
        # Phase 1: 并行估算以筛选出所有非零候选者
        # =================================================================================
        estimated_counts = {}
        all_prices = list(range(price_min, price_max + 1))
        price_batches = [all_prices[i:i + SEM_BATCH_SIZE] for i in range(0, len(all_prices), SEM_BATCH_SIZE)]

        for batch in price_batches:
            # --- 读写器行为: 广播对一批价格的并行估算指令 ---
            num_prices_in_batch = len(batch)
            total_frames += 1
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}

            bits_sent = (seed.bit_length() + SEM_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())
            total_bits_sent_by_reader += bits_sent
            
            # --- [物理层模拟] ---
            # 读写器无法访问标签私有数据。此代码块模拟所有标签根据收到的指令
            # (seed, price_map) 产生匿名的、聚合的响应帧。
            physical_frame = Frame(num_slots=SEM_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed) % SEM_FRAME_SIZE
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---

            # --- 读写器分析聚合的 Frame 对象 ---
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = SEM_FRAME_SIZE - sum(logical_bit_vector)
                
                if num_zeros == SEM_FRAME_SIZE:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    estimated_counts[price] = float('inf') # 饱和状态
                else:
                    count = -SEM_FRAME_SIZE * math.log(num_zeros / SEM_FRAME_SIZE)
                    estimated_counts[price] = count
        
        # 估算阶段标签发送的总比特数
        total_tag_bits_sent += total_frames * SEM_FRAME_SIZE * SEM_BATCH_SIZE

        # --- [修正] 关键逻辑：不过滤TOP_K，而是选取所有估算值大于0的作为候选 ---
        candidate_prices = [p for p, c in estimated_counts.items() if c > 0]

        # =================================================================================
        # Phase 2: 对所有候选者进行精确计数
        # =================================================================================
        exact_counts = {}
        time_phase2_ms = 0
        
        for price in candidate_prices:
            # --- 读写器行为: 广播对单个价格的精确计数指令 ---
            # (此处的通信开销被包含在下方的物理层模拟模型中)

            # --- [物理层模拟] ---
            # 读写器发起精确计数请求，物理层返回一个聚合的计数值和该过程的开销。
            # 这是对一个完整的逐一识别协议（如二进制树）的抽象。
            count_for_price = sum(1 for t in tags if t.price == price)
            
            cost = 0.0
            if count_for_price > 0:
                # 假设的精确计数时间模型：开销与待识别的标签数量成正比
                time_per_tag = (4 * 58.8) + 6.4
                cost = count_for_price * time_per_tag
            # --- [物理层模拟结束] ---

            # --- 读写器接收结果 ---
            exact_counts[price] = count_for_price
            time_phase2_ms += cost
        
        # --- 最终结果处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag_phase1 = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        
        # 总时间是各阶段时间之和
        total_time_ms = T_reader + T_tag_phase1 + time_phase2_ms + T_overhead

        if not exact_counts:
            return [], 0, total_time_ms, {"candidates": candidate_prices, "exact_counts": {}}

        max_count = max(exact_counts.values())
        modes = sorted([p for p, count in exact_counts.items() if count == max_count])
        
        verification_data = {
            "SEM_frame_size": SEM_FRAME_SIZE,
            "SEM_batch_size": SEM_BATCH_SIZE,
            "all_candidates_after_filtering": candidate_prices,
            "final_exact_counts": exact_counts
        }
        
        return modes, max_count, total_time_ms, verification_data