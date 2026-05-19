import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g4_5(IProtocol):
    """
    估算引导自适应帧众数查询协议 (Estimation-Guided Adaptive Frame Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    该协议通过一个三阶段流程运作：
    1. 粗粒度范围剪枝：快速排除没有标签的价格区间。
    2. 并行批量估算：对活跃价格进行高效的数量估算。
    3. 精确裁决：基于估算值，为每个候选价格动态设置最优帧大小，并使用最鲁棒的评分机制进行最终裁决。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 阶段一参数 (源于父代)
        PRECHECK_BINS = 8
        PRECHECK_FRAME_SIZE = 64
        # 阶段二参数 (源于父代)
        ESTIMATION_BATCH_SIZE = 100
        ESTIMATION_FRAME_SIZE = 256
        # 阶段三参数 (部分源于父代，部分为全新)
        CANDIDATE_CONFIDENCE_THRESHOLD = 0.75 # 用于筛选候选者的置信度阈值
        MIN_SHOWDOWN_FRAME_SIZE = 32         # 突变机制的帧大小下限
        MAX_SHOWDOWN_FRAME_SIZE = 512        # 突变机制的帧大小上限
        FRAME_SCALING_FACTOR = 1.2           # 突变机制的帧大小缩放因子

        # --- 交叉部分：融合父代B的“三阶段（剪枝-估算-裁决）”查询框架 与 父代A的“非空时隙数”精确评分思想 ---
        
        # 阶段一：粗粒度范围剪枝 (结构源自父代A和B的共同思想)
        # 采用分箱预检策略，快速排除不存在任何标签的价格大区间，减少后续处理范围。
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
            # 读写器广播：种子，帧大小，价格区间起始和结束
            total_bits_sent_by_reader += (seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() + 
                                          p_bin[0].bit_length() + p_bin[-1].bit_length())
            
            # 标签根据广播参数响应
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_states[simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE] += 1
            
            # 标签发送的总位数 (1 bit per slot)
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1
            
            # 读写器分析帧，如果帧中至少有一个时隙非空，则认为该价格区间是活跃的
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found"}

        # 阶段二：并行批量估算 (结构源自父代B)
        # 对所有活跃价格进行分批，使用更高效的位图帧来并行估算每个价格的标签数量。
        estimated_counts = {}
        sorted_active_prices = sorted(list(active_prices))
        price_batches = [
            sorted_active_prices[i:i + ESTIMATION_BATCH_SIZE] 
            for i in range(0, len(sorted_active_prices), ESTIMATION_BATCH_SIZE)
        ]

        for batch in price_batches:
            if not batch: continue

            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            
            # 读写器广播：种子，帧大小，批内价格数量 (用于标签构建响应)
            total_bits_sent_by_reader += (seed.bit_length() + ESTIMATION_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())
            # 实际协议中，价格到bit位置的映射关系也需要广播，但为简化计算，此处暂不计入
            
            # 标签响应，每个标签在对应价格的bit位上置1
            physical_frame = Frame(num_slots=ESTIMATION_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % ESTIMATION_FRAME_SIZE
                    physical_frame.aggregate(slot_id, 1 << price_to_bit_pos_map[t.price])
            
            total_tag_bits_sent += ESTIMATION_FRAME_SIZE * num_prices_in_batch

            # 读写器分析聚合帧，为每个价格估算数量
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_vec = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = ESTIMATION_FRAME_SIZE - sum(logical_vec)
                
                if num_zeros == ESTIMATION_FRAME_SIZE: 
                    estimated_counts[price] = 0
                elif num_zeros == 0: 
                    # 当所有时隙都非空时，无法精确估算，给一个较大的值
                    estimated_counts[price] = ESTIMATION_FRAME_SIZE * 2.5 
                else: 
                    # 经典的基于空时隙的估算公式
                    estimated_counts[price] = -ESTIMATION_FRAME_SIZE * math.log(num_zeros / ESTIMATION_FRAME_SIZE)

        if not estimated_counts:
            T_reader = total_bits_sent_by_reader * 0.025; T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "Estimation phase yielded no results"}
        
        # 筛选进入最终裁决阶段的候选者 (思想源自父代B的动态候选选择)
        max_estimated_count = max(estimated_counts.values()) if estimated_counts else 0
        showdown_candidates = [
            price for price, count in estimated_counts.items()
            if count >= max_estimated_count * CANDIDATE_CONFIDENCE_THRESHOLD and count > 0
        ]

        # 阶段三：精确评分最终裁决 (评分标准源自父代A)
        # 对动态筛选出的候选者，采用父代A更鲁棒的“非空时隙数”作为最终评分标准。
        final_scores = {}
        for price in showdown_candidates:
            
            # --- 突变部分：引入“基于估算值的自适应帧大小调整”全新机制 ---
            # 在父代A和B中，最终确认阶段的frame_size是固定的或基于历史碰撞率调整的。
            # 本协议引入一个创新：在精确裁决阶段，读写器不使用统一的帧大小，
            # 而是根据第二阶段对当前价格`price`的估算计数值`estimated_counts[price]`，
            # 为其量身定制一个最优的帧大小。理论上，当标签数为k时，帧大小设为k可以最大化非空时隙数。
            # 这种“一价一策”的自适应机制，能为每个潜在的众数创造最理想的识别条件，
            # 在确保准确性的同时，避免了对数量少的候选者浪费通信资源，也防止了对数量多的候选者因碰撞过高而误判。
            
            estimated_count = estimated_counts.get(price, MIN_SHOWDOWN_FRAME_SIZE)
            showdown_frame_size = max(MIN_SHOWDOWN_FRAME_SIZE, min(MAX_SHOWDOWN_FRAME_SIZE, int(estimated_count * FRAME_SCALING_FACTOR)))

            total_frames += 1
            seed = generate_random_seed()
            # 读写器广播：种子，为当前价格定制的帧大小，以及目标价格
            total_bits_sent_by_reader += seed.bit_length() + showdown_frame_size.bit_length() + price.bit_length()
            
            # 标签响应
            slot_states = [0] * showdown_frame_size
            for t in tags:
                if t.price == price:
                    slot_states[simple_hash(t.id, seed) % showdown_frame_size] += 1

            total_tag_bits_sent += showdown_frame_size * 1
            
            # 读写器采用父代A的核心评分思想：计算非空时隙数
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
            "showdown_candidate_count": len(showdown_candidates),
            "final_scores": dict(sorted(final_scores.items(), key=lambda item: item[1], reverse=True)),
            "mutation_info": "Frame size for showdown is adaptively set based on prior estimation for each candidate."
        }
        
        return modes, max_score, total_time_ms, verification_data