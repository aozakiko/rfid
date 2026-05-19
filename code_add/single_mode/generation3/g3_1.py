import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g3_1(IProtocol):
    """
    批量评分剪枝众数查询协议 (Batched Scoring Pruning Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        PRECHECK_BINS = 8               # 剪枝阶段的分箱数 (源于父代A)
        PRECHECK_FRAME_SIZE = 64        # 剪枝阶段的帧大小 (源于父代A)
        SCORING_BATCH_SIZE = 50         # 批量评分的批大小 (突变部分)
        SCORING_FRAME_SIZE = 512        # 批量评分的帧大小 (突变部分)

        # --- 交叉部分：融合父代A(g2_1)的“分阶段剪枝”思想与父代B(ModeQueryTCS)的“非空时隙评分”思想 ---

        # 阶段一：范围剪枝 (Inherited from Parent A: g2_1)
        # 采用父代A的分箱预检策略，通过少量通信快速排除掉不存在任何标签的大量价格区间，
        # 生成一个大大缩减的“活跃价格”列表，为下一阶段的精确评分奠定高效基础。
        active_prices = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        price_bins = [
            list(range(price_min + i * bin_size, min(price_min + (i + 1) * bin_size, price_max + 1)))
            for i in range(PRECHECK_BINS)
        ]

        for p_bin in price_bins:
            if not p_bin: continue
            
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() + 
                                          p_bin[0].bit_length() + p_bin[-1].bit_length())
            
            # --- [物理层模拟] ---
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_id = simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1
            
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found"}

        # 阶段二：对活跃价格进行评分 (Inspired by Parent B: ModeQueryTCS)
        # 核心评分标准借鉴父代B鲁棒的“非空时隙数”启发式指标，这为估算群体规模提供了可靠依据。
        price_to_score_map = {}
        sorted_active_prices = sorted(list(active_prices))

        # --- 突变部分：引入“并行批量评分”全新机制 ---
        # 两个父代协议在评分/详查阶段都是逐个价格进行探测，当活跃价格数量较多时，通信帧数会线性增长。
        # 本协议引入一个核心创新：将活跃价格分组（“批处理”），在一次通信帧内并行地对一批价格进行评分。
        # 标签的回复不再是简单的1-bit，而是一个编码了其价格在批次中位置的向量（bitmask）。
        # 读写器通过解析聚合后的向量帧，可以一次性得到该批次内所有价格的“非空时隙数”分数。
        # 这极大地减少了总通信帧数，是协议效率的关键跃升点。
        
        price_batches = [
            sorted_active_prices[i:i + SCORING_BATCH_SIZE] 
            for i in range(0, len(sorted_active_prices), SCORING_BATCH_SIZE)
        ]

        for batch in price_batches:
            if not batch: continue
            
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            total_bits_sent_by_reader += (seed.bit_length() + SCORING_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())
            
            # --- [物理层模拟] ---
            physical_frame = Frame(num_slots=SCORING_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % SCORING_FRAME_SIZE
                    reply_vector = 1 << price_to_bit_pos_map[t.price]
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * num_prices_in_batch

            # --- 读写器分析帧，一次性为批内所有价格打分 ---
            for price, bit_pos in price_to_bit_pos_map.items():
                # 为当前价格提取其逻辑上的0/1向量
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                # 计算“非空时隙数”作为分数
                score = sum(logical_bit_vector)
                if score > 0:
                    price_to_score_map[price] = score

        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        if not price_to_score_map:
            return [], 0, total_time_ms, {"active_prices": sorted_active_prices, "scores": {}}

        max_score = max(price_to_score_map.values())
        modes = sorted([p for p, score in price_to_score_map.items() if score == max_score])

        verification_data = {
            "active_price_count": len(active_prices),
            "scoring_batch_size": SCORING_BATCH_SIZE,
            "scoring_batches_run": len(price_batches),
            "final_scores": price_to_score_map,
        }
        
        return modes, max_score, total_time_ms, verification_data
