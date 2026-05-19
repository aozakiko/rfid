import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g5_2(IProtocol):
    """
    子代协议：分组对决精炼协议 (Grouped Showdown Refinement Protocol)

    本协议是一个为解决多众数查询问题设计的全新子代协议。它严格遵循交叉与突变的设计准则，
    通过一个多阶段的精炼过程，旨在准确且高效地从大规模标签中找出至少一个高频价格（众数）。

    协议流程分为三个核心阶段：

    1.  **阶段一：布隆过滤筛选 (Crossover from Protocol A)**:
        此阶段融合了父代协议A的“布隆过滤”思想。它使用多个哈希函数将所有可能的价格映射到
        不同的位图中。只有那些在所有位图中都映射到非空位置的价格，才会被认为是潜在的
        候选者。这个阶段的目的是以极低的通信成本，快速排除大量不存在或频率极低的价格，
        生成一个较小的候选价格池。

    2.  **阶段二：并行批量估算 (Crossover from Protocol B)**:
        此阶段借鉴了父代协议B的“并行批量估算”策略。对于第一阶段筛选出的候选池，协议将
        它们分批处理。在每一批中，通过一次帧通信，同时对多个价格进行基数估算。这利用了
        父代B将ID哈希到时隙、用位向量表示价格的核心机制，并采用零时隙估算公式来并行获取
        每个候选价格的大致频率，从而筛选出最有可能是众数的顶尖竞争者。

    3.  **阶段三：分组对决与动态追溯 (Mutation - New Mechanism)**:
        这是本协议引入的全新“分组对决 (Grouped Showdown)”机制。为了避免对所有顶尖竞争者
        都进行昂贵的精确计数，协议首先将它们两两分组进行快速“对决”。在对决中，通过一次
        通信帧比较两个价格的单时隙响应数量。如果一个价格的响应数远超另一个，则可以提前
        淘汰败者。只有在对决中胜出或实力相当的候选者，才会进入最终的“动态追溯计数”环节。
        该最终环节继承了父代B的自适应帧长调整策略，通过在帧饱和时加倍帧长来确保即使面对
        频率极高的众数也能获得准确的计数值。这个“先对决，再精算”的突变设计，显著降低了
        在最终阶段不必要的计算开销。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 阶段 1: 布隆过滤
        FILTER_FRAME_SIZE = 1024
        NUM_HASH_FUNCTIONS = 2

        # 阶段 2: 并行估算
        ESTIMATION_FRAME_SIZE = 128
        ESTIMATION_BATCH_SIZE = 32
        TOP_K_CANDIDATES = 8 # 选出前8名进入决赛圈

        # 阶段 3: 分组对决与动态追溯 (突变)
        SHOWDOWN_FRAME_SIZE = 256
        SHOWDOWN_THRESHOLD = 5.0 # 对决阈值，一方响应数是另一方的5倍以上则淘汰后者
        INITIAL_ACCURATE_FRAME_SIZE = 256
        MAX_ACCURATE_FRAME_SIZE = 8192

        # ==================================================================
        # --- 交叉部分：融合父代A的“布隆过滤”思想 ---
        # 阶段一: 快速布隆过滤筛选
        # ==================================================================
        potential_prices = set(range(price_min, price_max + 1))
        
        filter_seeds = [generate_random_seed() for _ in range(NUM_HASH_FUNCTIONS)]
        total_frames += NUM_HASH_FUNCTIONS
        total_bits_sent_by_reader += sum(s.bit_length() for s in filter_seeds) + NUM_HASH_FUNCTIONS * FILTER_FRAME_SIZE.bit_length()

        # [物理层模拟]
        filter_frames_responses = [[0] * FILTER_FRAME_SIZE for _ in range(NUM_HASH_FUNCTIONS)]
        for t in tags:
            if t.price in potential_prices:
                for i in range(NUM_HASH_FUNCTIONS):
                    slot_id = simple_hash(t.price, filter_seeds[i]) % FILTER_FRAME_SIZE
                    filter_frames_responses[i][slot_id] = 1 # 任何响应都将时隙设为1
        
        total_tag_bits_sent += (FILTER_FRAME_SIZE * 1) * NUM_HASH_FUNCTIONS
        # [物理层模拟结束]

        stage1_candidates = set()
        for p in potential_prices:
            is_candidate = all(filter_frames_responses[i][simple_hash(p, filter_seeds[i]) % FILTER_FRAME_SIZE] == 1 for i in range(NUM_HASH_FUNCTIONS))
            if is_candidate:
                stage1_candidates.add(p)

        if not stage1_candidates:
            return [], 0, 0.0, {"stage": 1, "message": "No candidates found after Bloom filter."}

        # ==================================================================
        # --- 交叉部分：融合父代B的“并行批量估算”思想 ---
        # 阶段二: 并行批量估算
        # ==================================================================
        estimated_counts = {}
        candidate_list = sorted(list(stage1_candidates))
        price_batches = [candidate_list[i:i + ESTIMATION_BATCH_SIZE] for i in range(0, len(candidate_list), ESTIMATION_BATCH_SIZE)]

        for batch in price_batches:
            if not batch: continue

            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            
            total_frames += 1
            total_bits_sent_by_reader += (seed.bit_length() + ESTIMATION_FRAME_SIZE.bit_length() + sum(p.bit_length() for p in batch))
            
            physical_frame = Frame(num_slots=ESTIMATION_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % ESTIMATION_FRAME_SIZE
                    bit_pos = price_to_bit_pos_map[t.price]
                    physical_frame.aggregate(slot_id, 1 << bit_pos)
            
            total_tag_bits_sent += ESTIMATION_FRAME_SIZE * num_prices_in_batch

            for price, bit_pos in price_to_bit_pos_map.items():
                num_zeros = ESTIMATION_FRAME_SIZE - sum(((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots)
                if num_zeros == 0: estimated_counts[price] = float('inf')
                elif num_zeros == ESTIMATION_FRAME_SIZE: estimated_counts[price] = 0
                else: estimated_counts[price] = -ESTIMATION_FRAME_SIZE * math.log(num_zeros / ESTIMATION_FRAME_SIZE)

        sorted_estimates = sorted(estimated_counts.items(), key=lambda item: (item[1] != float('inf'), -item[1]))
        stage2_candidates = [price for price, count in sorted_estimates[:TOP_K_CANDIDATES]]

        if not stage2_candidates:
            return [], 0, 0.0, {"stage": 2, "message": "No candidates after estimation."}

        # ==================================================================
        # --- 突变部分：引入“分组对决与动态追溯”全新机制 ---
        # 阶段三: 分组对决与动态追溯
        # ==================================================================
        survivors = []
        showdown_candidates = list(stage2_candidates)
        
        i = 0
        while i < len(showdown_candidates) - 1:
            p1, p2 = showdown_candidates[i], showdown_candidates[i+1]
            seed = generate_random_seed()
            
            total_frames += 1
            total_bits_sent_by_reader += seed.bit_length() + SHOWDOWN_FRAME_SIZE.bit_length() + p1.bit_length() + p2.bit_length()
            
            frame = [0] * SHOWDOWN_FRAME_SIZE
            for t in tags:
                slot_id = simple_hash(t.id, seed) % SHOWDOWN_FRAME_SIZE
                if t.price == p1: frame[slot_id] |= 1
                elif t.price == p2: frame[slot_id] |= 2

            total_tag_bits_sent += SHOWDOWN_FRAME_SIZE * 2

            p1_single_slots = frame.count(1)
            p2_single_slots = frame.count(2)

            if p1_single_slots > p2_single_slots * SHOWDOWN_THRESHOLD:
                if p1 not in survivors: survivors.append(p1)
            elif p2_single_slots > p1_single_slots * SHOWDOWN_THRESHOLD:
                if p2 not in survivors: survivors.append(p2)
            else:
                if p1 not in survivors: survivors.append(p1)
                if p2 not in survivors: survivors.append(p2)
            i += 2
        
        if len(showdown_candidates) % 2 == 1:
            last_candidate = showdown_candidates[-1]
            if last_candidate not in survivors: survivors.append(last_candidate)

        if not survivors: survivors = stage2_candidates

        final_counts = {}
        for price in survivors:
            current_frame_size = INITIAL_ACCURATE_FRAME_SIZE
            estimated_count = float('inf')

            while estimated_count == float('inf') and current_frame_size <= MAX_ACCURATE_FRAME_SIZE:
                seed = generate_random_seed()
                total_frames += 1
                total_bits_sent_by_reader += (seed.bit_length() + current_frame_size.bit_length() + price.bit_length())

                physical_frame = Frame(num_slots=current_frame_size, bits_per_slot=1)
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % current_frame_size
                        physical_frame.aggregate(slot_id, 1)
                
                total_tag_bits_sent += current_frame_size * 1
                
                num_zeros = physical_frame.slots.count(0)
                if num_zeros == current_frame_size: estimated_count = 0
                elif num_zeros == 0: current_frame_size *= 2; estimated_count = float('inf')
                else: estimated_count = -current_frame_size * math.log(num_zeros / current_frame_size)
            final_counts[price] = estimated_count

        # ==================================================================
        # 最终处理与时间计算
        # ==================================================================
        if not final_counts: return [], 0, 0.0, {"stage": 3, "message": "Final count failed."}

        max_count_val = max(final_counts.values()) if final_counts else -1
        modes = sorted([p for p, c in final_counts.items() if math.isclose(c, max_count_val)]) if max_count_val != float('inf') else sorted([p for p, c in final_counts.items() if c == float('inf')])
        display_count = math.ceil(max_count_val) if max_count_val != float('inf') else "inf"

        T_reader = total_bits_sent_by_reader * 0.025
        # The formula uses total_slots * bits_per_slot. Here, total_tag_bits_sent is already sum(slots_i * bits_per_slot_i)
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "stage1_candidates_count": len(stage1_candidates),
            "stage2_top_candidates": sorted_estimates[:TOP_K_CANDIDATES],
            "showdown_survivors": survivors,
            "final_counts": final_counts,
            "total_frames": total_frames,
        }

        return modes, display_count, total_time_ms, verification_data
