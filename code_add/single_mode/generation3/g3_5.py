import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g3_5(IProtocol):
    """
    自适应批次精炼协议 (Adaptive Batch Refinement Protocol)

    本协议是一个全新的、功能完整的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议（g2_1 和 g2_2）的核心设计思想，
    同时引入了全新的、基于通信质量反馈的自适应精炼机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        PRECHECK_BINS = 8               # 阶段一：范围剪枝的分箱数
        PRECHECK_FRAME_SIZE = 64        # 阶段一：范围剪枝的帧大小
        SCORING_BATCH_SIZE = 100        # 阶段二：并行评分的批次大小
        SCORING_FRAME_SIZE = 256        # 阶段二：并行评分的帧大小
        REFINEMENT_FRAME_SIZE = 1024    # 阶段三：对模糊批次进行精炼的帧大小
        AMBIGUITY_COLLISION_THRESHOLD = 0.3 # 触发精炼的批次碰撞率阈值
        AMBIGUITY_MARGIN_THRESHOLD = 0.1    # 触发精炼的胜出分数边际阈值

        # --- 交叉部分：融合父代A(g2_2)的“并行估算”思想与父代B(g2_1)的“分阶段剪枝”思想 ---
        
        # 阶段一：范围剪枝 (Inherited from Parent B: g2_1)
        # 采用父代B的分箱预检策略，通过少量通信快速排除大部分不存在任何标签的价格区间，
        # 从而显著缩小后续需要精确处理的价格候选集。
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
            # 读写器广播：种子，帧大小，价格区间
            total_bits_sent_by_reader += (seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() + p_bin[0].bit_length() + p_bin[-1].bit_length())
            
            # 物理层模拟：标签根据自身价格是否在区间内，决定是否响应
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_id = simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE
                    slot_states[slot_id] += 1
            
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1 # 标签回复1 bit
            
            # 读写器分析：只要帧中有一个非空时隙，就认为该价格区间是活跃的
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found"}

        # 阶段二：并行评分 (Inherited & Fused from Parent A: g2_2)
        # 对剪枝后的活跃价格，采用父代A的并行批处理思想，一次查询一批价格。
        # 但评分标准并非父代A的数学估计，而是受父代B启发，采用更直接的“逻辑非空时隙数”作为分数，
        # 这种融合兼顾了父代A的效率和父代B评分的鲁棒性。
        final_scores = {}
        sorted_active_prices = sorted(list(active_prices))
        price_batches = [
            sorted_active_prices[i:i + SCORING_BATCH_SIZE] 
            for i in range(0, len(sorted_active_prices), SCORING_BATCH_SIZE)
        ]
        
        ambiguous_batches = []

        for batch in price_batches:
            if not batch: continue
            
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            # 读写器广播：种子，帧大小，批内价格数量 (用于确定回复向量长度)
            total_bits_sent_by_reader += (seed.bit_length() + SCORING_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())

            # 物理层模拟：标签根据自身价格，在对应bit位置1，形成向量并回复
            physical_frame = Frame(num_slots=SCORING_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % SCORING_FRAME_SIZE
                    reply_vector = 1 << price_to_bit_pos_map[t.price]
                    physical_frame.aggregate(slot_id, reply_vector)
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * num_prices_in_batch

            # --- 突变部分：引入“基于通信歧义的自适应批次精炼”全新机制 ---
            # 父代协议的优化是全局的(Top-K)或顺序的(逐个调整)。本协议引入一个创新：
            # 读写器在完成一次并行查询后，会立刻对该批次的数据质量进行评估。
            # 如果批次内碰撞严重，或得分最高的几个候选者分数过于接近（“通信歧义”），
            # 则将该批次标记为“模糊批次”，并启动一次针对该批次的、使用更大帧的“精炼查询”。
            # 这种机制能智能地识别并解决局部困难问题，将宝贵的通信资源精确投放到最需要的地方。
            
            batch_scores = {}
            num_collision_slots = 0
            for slot_data in physical_frame.slots:
                # 使用 bit_count 检查时隙中是否有多于1个比特被置1，即为碰撞
                if bin(slot_data).count('1') > 1:
                    num_collision_slots += 1

            for price, bit_pos in price_to_bit_pos_map.items():
                # 从物理帧中为每个价格提取其逻辑比特向量
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                # 分数为该价格的逻辑向量中'1'的数量
                score = sum(logical_bit_vector)
                batch_scores[price] = score

            # 评估当前批次是否存在“通信歧义”
            collision_rate = num_collision_slots / SCORING_FRAME_SIZE
            
            # 获取批次内分数最高的两个价格
            sorted_scores = sorted(batch_scores.values(), reverse=True)
            margin = float('inf')
            if len(sorted_scores) > 1 and sorted_scores[0] > 0:
                margin = (sorted_scores[0] - sorted_scores[1]) / sorted_scores[0]

            # 如果碰撞率过高 或 胜出边际过小，则认为此批次结果有歧义，需要精炼
            if collision_rate > AMBIGUITY_COLLISION_THRESHOLD or margin < AMBIGUITY_MARGIN_THRESHOLD:
                ambiguous_batches.append(batch)
            else:
                final_scores.update(batch_scores)

        # 阶段三: 精炼查询 (Refinement Query)
        # 对所有被标记为“模糊”的批次，使用更大的帧长重新进行并行评分
        for batch in ambiguous_batches:
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            total_bits_sent_by_reader += (seed.bit_length() + REFINEMENT_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())
            
            physical_frame = Frame(num_slots=REFINEMENT_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % REFINEMENT_FRAME_SIZE
                    reply_vector = 1 << price_to_bit_pos_map[t.price]
                    physical_frame.aggregate(slot_id, reply_vector)

            total_tag_bits_sent += REFINEMENT_FRAME_SIZE * num_prices_in_batch

            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                final_scores[price] = sum(logical_bit_vector)

        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "active_price_count": len(active_prices),
            "ambiguous_batch_count": len(ambiguous_batches),
            "final_scores": final_scores,
        }
        
        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        # 避免因没有标签导致max_score为0时返回所有价格
        if max_score == 0:
             return [], 0, total_time_ms, verification_data

        modes = sorted([p for p, e in final_scores.items() if e == max_score])
        
        return modes, max_score, total_time_ms, verification_data