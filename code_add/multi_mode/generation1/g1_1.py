import math
from typing import List, Tuple, Dict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g1_1(IProtocol):
    """
    子代协议: 融合剪枝与批量启发式评分的众数查询协议 (Hybrid Pruning & Batched Heuristic Mode Query)

    本协议通过一个两阶段过程来高效并准确地找出众数。

    第一阶段 (突变 - 存在性剪枝): 协议首先使用非常大的批次对整个价格范围进行快速扫描。
    这个阶段的目标不是精确计数，而是快速识别出哪些价格“可能”存在标签，从而将大量不存在
    任何标签的价格从后续的计算中剪枝掉，极大地缩小了搜索空间。

    第二阶段 (交叉 - 批量启发式评分): 协议对第一阶段筛选出的候选价格集进行处理。
    此阶段融合了父代A的“并行批处理”思想和父代B的“非空时隙数”作为启发式指标的鲁棒性。
    它将候选价格分批，在每个批次内并行地为每个价格收集其非空时隙数。这个分数是一个
    关于标签数量的强单调函数，能有效反映其相对规模。

    最终，协议找出在第二阶段中获得最高“非空时隙”分数的那个价格，作为最终的众数。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 突变阶段参数：使用大帧宽和批大小以快速过滤
        existence_check_frame_size = 1024
        existence_check_batch_size = 1024
        # 交叉阶段参数：使用更适合精确评分的帧宽和批大小
        scoring_frame_size = 256
        scoring_batch_size = 128

        # ===================================================================================
        # --- 突变部分：引入基于大批次扫描的存在性剪枝（Existence Pruning）全新机制 ---
        # 该机制在父代A和B中均未出现。其目的是在精确估算前，首先高效地剔除大量无标签的价格，
        # 从而显著减少后续阶段的计算负担。
        # ===================================================================================
        candidate_prices = set()
        all_prices = list(range(price_min, price_max + 1))
        existence_batches = [all_prices[i:i + existence_check_batch_size] for i in range(0, len(all_prices), existence_check_batch_size)]

        for batch in existence_batches:
            if not batch: continue

            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}

            total_bits_sent_by_reader += (seed.bit_length() + existence_check_frame_size.bit_length() + num_prices_in_batch.bit_length())

            # [物理层模拟]
            physical_frame = Frame(num_slots=existence_check_frame_size, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % existence_check_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            
            total_tag_bits_sent += existence_check_frame_size * num_prices_in_batch

            # 读写器分析聚合帧，找出有响应的价格
            for price, bit_pos in price_to_bit_pos_map.items():
                # 检查该价格对应的逻辑位向量是否全为0
                has_response = any(((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots)
                if has_response:
                    candidate_prices.add(price)
        
        # ===================================================================================
        # --- 交叉部分：融合父代A的“并行批处理”思想与父代B的“非空时隙数评分”思想 ---
        # 此阶段仅对上一阶段筛选出的 `candidate_prices` 进行处理。
        # 它采用父代A的架构，将价格分组并并行查询，但评分时则采用父代B更鲁棒的启发式
        # 指标（非空时隙数），而非父代A的对数估算。
        # ===================================================================================
        final_scores = {}
        
        # 仅对候选价格进行评分
        candidate_price_list = sorted(list(candidate_prices))
        scoring_batches = [candidate_price_list[i:i + scoring_batch_size] for i in range(0, len(candidate_price_list), scoring_batch_size)]

        for batch in scoring_batches:
            if not batch: continue

            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}

            total_bits_sent_by_reader += (seed.bit_length() + scoring_frame_size.bit_length() + num_prices_in_batch.bit_length())

            # [物理层模拟]
            physical_frame = Frame(num_slots=scoring_frame_size, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % scoring_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            
            total_tag_bits_sent += scoring_frame_size * num_prices_in_batch

            # 读写器分析聚合帧，并使用“非空时隙数”进行评分
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                # B协议的核心思想：使用非空时隙数作为分数
                score = sum(logical_bit_vector)
                final_scores[price] = score
        
        # --- 结果汇总与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        # 修正 T_tag 的计算，应基于总时隙数和每个时隙的比特数
        total_slots = (len(existence_batches) * existence_check_frame_size) + (len(scoring_batches) * scoring_frame_size)
        # 假设每个时隙的比特数在两个阶段是不同的，但这里为了简化，我们已经在循环中累加了总比特数
        # 因此直接使用 total_tag_bits_sent 是更准确的
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "existence_check_frame_size": existence_check_frame_size,
            "existence_check_batch_size": existence_check_batch_size,
            "scoring_frame_size": scoring_frame_size,
            "scoring_batch_size": scoring_batch_size,
            "num_candidate_prices": len(candidate_prices),
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = max(final_scores.values())
        
        modes = sorted([
            price for price, score in final_scores.items() 
            if score == max_score
        ])
        
        return modes, max_score, total_time_ms, verification_data
