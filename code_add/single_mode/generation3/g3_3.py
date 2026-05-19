import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g3_3(IProtocol):
    """
    分层自适应裁决众数查询协议 (Hierarchical Adaptive Showdown Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        PRECHECK_BINS = 8               # 预检分箱数 (源于父代B)
        PRECHECK_FRAME_SIZE = 64        # 预检帧长 (源于父代B)
        ESTIMATION_BATCH_SIZE = 100     # 估算批大小 (源于父代B)
        ESTIMATION_FRAME_SIZE = 256     # 估算帧长 (源于父代B)
        SHOWDOWN_FRAME_SIZE = 128       # 最终裁决帧长 (源于父代A)
        CANDIDATE_CONFIDENCE_THRESHOLD = 0.8 # 候选者置信度阈值 (突变部分)

        # --- 交叉部分：融合父代B(g1_1)的“分层剪枝估算”框架与父代A(g2_5)的“精确评分”思想 ---

        # 阶段一：粗粒度范围剪枝 (Inherited from Parent B: g1_1)
        # 采用父代B的分箱预检策略，快速排除不存在任何标签的价格区间。
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
            
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_states[simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE] += 1
            
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            T_reader = total_bits_sent_by_reader * 0.025; T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found"}

        # 阶段二：并行批量估算 (Inherited from Parent B: g1_1)
        # 对活跃价格进行分批并行估算，快速得到每个价格的近似标签数量。
        estimated_counts = {}
        sorted_active_prices = sorted(list(active_prices))
        price_batches = [
            sorted_active_prices[i:i + ESTIMATION_BATCH_SIZE] 
            for i in range(0, len(sorted_active_prices), ESTIMATION_BATCH_SIZE)
        ]

        for batch in price_batches:
            if not batch: continue
            total_frames += 1; num_prices_in_batch = len(batch); seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            total_bits_sent_by_reader += (seed.bit_length() + ESTIMATION_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())
            
            physical_frame = Frame(num_slots=ESTIMATION_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % ESTIMATION_FRAME_SIZE
                    physical_frame.aggregate(slot_id, 1 << price_to_bit_pos_map[t.price])
            
            total_tag_bits_sent += ESTIMATION_FRAME_SIZE * num_prices_in_batch

            for price, bit_pos in price_to_bit_pos_map.items():
                logical_vec = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = ESTIMATION_FRAME_SIZE - sum(logical_vec)
                if num_zeros == ESTIMATION_FRAME_SIZE: estimated_counts[price] = 0
                elif num_zeros == 0: estimated_counts[price] = ESTIMATION_FRAME_SIZE * 2.5 
                else: estimated_counts[price] = -ESTIMATION_FRAME_SIZE * math.log(num_zeros / ESTIMATION_FRAME_SIZE)

        if not estimated_counts:
             T_reader = total_bits_sent_by_reader * 0.025; T_tag = total_tag_bits_sent * 0.4
             T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
             return [], 0, T_reader + T_tag + T_overhead, {"reason": "Estimation phase yielded no results"}

        # --- 突变部分：引入“基于估算值置信区间的自适应候选选择”全新机制 ---
        # 父代协议B使用固定的Top-K来选择候选者，这在估算值非常接近时可能错失真正的众数。
        # 本协议引入一个创新：动态地决定最终裁决的候选集。它首先找到最高的估算值，
        # 然后将所有估算值大于 (最高估算值 * 阈值) 的价格都纳入最终裁决名单。
        # 这使得协议在面对多个“势均力敌”的竞争者时表现更鲁棒，能自适应地扩大精确验证的范围，
        # 而在众数“一骑绝尘”时又能保持小范围验证的高效率。
        max_estimated_count = max(estimated_counts.values())
        showdown_candidates = [
            price for price, count in estimated_counts.items()
            if count >= max_estimated_count * CANDIDATE_CONFIDENCE_THRESHOLD
        ]

        # 阶段三：精确评分最终裁决 (Inspired by Parent A: g2_5)
        # 对动态筛选出的候选者，采用父代A更鲁棒的“非空时隙数”作为最终评分标准，确保结果的准确性。
        final_scores = {}
        for price in showdown_candidates:
            total_frames += 1; seed = generate_random_seed()
            total_bits_sent_by_reader += seed.bit_length() + SHOWDOWN_FRAME_SIZE.bit_length() + price.bit_length()
            
            slot_states = [0] * SHOWDOWN_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_states[simple_hash(t.id, seed) % SHOWDOWN_FRAME_SIZE] += 1

            total_tag_bits_sent += SHOWDOWN_FRAME_SIZE * 1
            score = sum(1 for count in slot_states if count > 0)
            if score > 0:
                final_scores[price] = score

        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        if not final_scores:
            return [], 0, total_time_ms, {"reason": "Showdown phase yielded no scores", "candidates": showdown_candidates}

        max_score = max(final_scores.values())
        modes = sorted([p for p, score in final_scores.items() if score == max_score])
        
        verification_data = {
            "active_prices_after_pruning": len(active_prices),
            "estimated_counts": dict(sorted(estimated_counts.items(), key=lambda item: item[1], reverse=True)[:10]),
            "showdown_candidate_count": len(showdown_candidates),
            "final_scores": final_scores
        }
        
        return modes, max_score, total_time_ms, verification_data
