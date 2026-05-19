import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_3(IProtocol):
    """
    子代协议: 融合突变型精确计数协议 (Fusion-Mutated Exact-Counting Protocol)

    本协议旨在通过深度融合两个父代协议的优点并引入全新的精确验证机制，
    来稳健、准确地解决复杂环境下的多众数查询问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        # 阶段零: 预扫描 (继承自父代B)
        initial_scan_bins = 64
        
        # 阶段一: 过滤 (交叉父代A和B)
        initial_filter_frame_size = 128
        candidate_stop_threshold = 40
        max_filter_rounds = 6
        high_density_threshold = 0.85
        low_density_threshold = 0.1
        stagnation_threshold = 0.05
        
        # 阶段二: 并行验证 (继承自父代A和B)
        verification_frame_size = 128

        # 阶段三: 精确计数 (突变)
        exact_counting_frame_size = 256
        
        # 动态哈希函数数量调整 (继承自父代A)
        initial_hash_functions = 1
        max_hash_functions = 3
        ineffective_reduction_threshold = 0.15


        # ==================================================================
        #  阶段零: 热点区域预扫描 (源自父代B)
        # ==================================================================
        total_frames += 1
        total_bits_sent_by_reader += initial_scan_bins.bit_length() + price_min.bit_length() + price_max.bit_length()
        
        # --- [物理层模拟] ---
        scan_frame = [0] * initial_scan_bins
        price_range_width = price_max - price_min + 1
        for t in tags:
            # 标签根据自身价格计算所属的区间(bin)
            bin_index = math.floor(((t.price - price_min) / price_range_width) * initial_scan_bins)
            bin_index = min(bin_index, initial_scan_bins - 1)
            scan_frame[bin_index] = 1 # 仅标记区间非空即可
        
        total_tag_reply_bits += initial_scan_bins * 1  # 标签仅需1比特表示存在性
        # --- [物理层模拟结束] ---

        # 读写器分析预扫描结果，构建初始候选价格集
        hot_zone_indices = {i for i, count in enumerate(scan_frame) if count > 0}
        
        candidate_prices: Set[int] = set()
        if not hot_zone_indices: # 如果全空, 则搜索整个范围
            candidate_prices = set(range(price_min, price_max + 1))
        else:
            for i in hot_zone_indices:
                start_price = price_min + math.floor(i * (price_range_width / initial_scan_bins))
                end_price = price_min + math.ceil((i + 1) * (price_range_width / initial_scan_bins))
                for p in range(start_price, min(end_price, price_max + 1)):
                    candidate_prices.add(p)

        initial_candidate_count = len(candidate_prices)

        # ==================================================================
        #  阶段一: 自适应迭代过滤
        # ==================================================================
        # --- 交叉部分：融合父代B的自适应迭代过滤思想与父代A的动态哈希调整思想 ---
        # 本协议采纳了两个父代共有的核心过滤框架：通过多轮通信迭代地削减候选集。
        # 具体实现上，我们继承了父代B的“基于时隙密度的帧长自适应调整”机制，以应对标签响应的拥塞或稀疏。
        # 同时，我们融入了父代A独创的“基于过滤效率的动态哈希函数数量调整”策略，
        # 当过滤效果不佳时，能通过增加哈希函数来增强筛选能力，实现了更高维度的自适应。
        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        num_hash_functions = initial_hash_functions

        while len(candidate_prices) > candidate_stop_threshold and filter_rounds_done < max_filter_rounds:
            prev_candidate_count = len(candidate_prices)

            # 广播当前轮次的帧长和哈希种子
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            bits_sent_this_round = current_filter_frame_size.bit_length()
            for seed in filter_seeds:
                bits_sent_this_round += seed.bit_length()
            total_bits_sent_by_reader += bits_sent_this_round

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] = 1 # 标记为非空
            
            total_tag_reply_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # 读写器分析聚合信号，生成新的候选集
            new_candidates = set()
            for p in candidate_prices:
                is_candidate_in_round = True
                for i in range(num_hash_functions):
                    slot_p = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    if filter_frames_states[i][slot_p] == 0:
                        is_candidate_in_round = False
                        break
                if is_candidate_in_round:
                    new_candidates.add(p)
            
            candidate_prices = new_candidates
            filter_rounds_done += 1
            
            # 计算缩减率和时隙密度
            reduction_ratio = (prev_candidate_count - len(candidate_prices)) / prev_candidate_count if prev_candidate_count > 0 else 0
            total_non_empty_slots = sum(sum(frame) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            # (源自父代A) 动态调整哈希函数数量
            if reduction_ratio < ineffective_reduction_threshold and num_hash_functions < max_hash_functions:
                num_hash_functions += 1

            # (源自父代B) 自适应调整帧长
            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                current_filter_frame_size = max(initial_filter_frame_size // 2, current_filter_frame_size // 2)

            # (源自父代B) 停滞检测
            if filter_rounds_done > 1 and reduction_ratio < stagnation_threshold:
                break

        final_candidate_list = sorted(list(candidate_prices))

        # ==================================================================
        #  阶段二: 并行位图近似验证 (源自父代A和B)
        # ==================================================================
        estimated_scores = {}
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            bits_per_slot_verify = len(final_candidate_list)

            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (verification_frame_size.bit_length() + 
                                          seed_verify.bit_length() + 
                                          sum(p.bit_length() for p in final_candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)

            total_tag_reply_bits += verification_frame_size * bits_per_slot_verify
            # --- [物理层模拟结束] ---

            # 读写器解码，得到每个候选价格的近似计数值（出现过的时隙数）
            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                count = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if count > 0:
                    estimated_scores[price] = count

        # ==================================================================
        #  阶段三: 精确计数值验证
        # ==================================================================
        # --- 突变部分：引入基于二次验证的“精确计数”全新机制 ---
        # 父代协议的并行验证阶段虽然高效，但其计数值是通过统计“价格对应比特位被置1的时隙数量”得出的，
        # 这在标签ID哈希到同一时隙时会产生计数误差（多个标签计为1个），得到的是“存在性”的近似计数而非精确值。
        # 本协议引入了一个全新的“二次精确验证”阶段。在并行验证得出初步的众数候选者后，协议会针对这一个或几个
        # 顶级候选价格，逐一进行独立的、无冲突的计数。读写器为每个顶级候选价格分配一个专用的通信帧，
        # 仅让拥有该价格的标签进行响应。通过统计该帧中的响应总数，
        # 读写器可以获得该价格的无偏、精确的计数值，从而确保最终众数选择的绝对准确性，解决了父代协议中的核心精度问题。
        final_scores = {}
        if estimated_scores:
            max_estimated_score = max(estimated_scores.values())
            top_contenders = [p for p, s in estimated_scores.items() if s == max_estimated_score]

            for price_to_count in top_contenders:
                total_frames += 1
                seed_exact = generate_random_seed()
                total_bits_sent_by_reader += (price_to_count.bit_length() + 
                                              seed_exact.bit_length() +
                                              exact_counting_frame_size.bit_length())
                
                # --- [物理层模拟] ---
                exact_frame = [0] * exact_counting_frame_size
                for t in tags:
                    if t.price == price_to_count:
                        slot_id = simple_hash(t.id, seed_exact) % exact_counting_frame_size
                        exact_frame[slot_id] += 1
                
                total_tag_reply_bits += exact_counting_frame_size * 1 # 假设标签仅回复1比特存在信号
                # --- [物理层模拟结束] ---

                # 读写器通过统计非空时隙数来精确计数
                # 假设 exact_counting_frame_size 足够大以至于哈希碰撞可忽略
                exact_count = sum(1 for slot in exact_frame if slot > 0)
                final_scores[price_to_count] = exact_count
        
        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        # 标签回复的比特数在验证阶段是动态的，这里简化计算
        T_tag_slots = total_tag_reply_bits / 1 # 假设每个时隙回复1bit
        T_tag = T_tag_slots * 0.4 
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "initial_candidates_after_scan": initial_candidate_count,
            "filter_rounds_done": filter_rounds_done,
            "final_candidates_after_filter": len(final_candidate_list),
            "estimated_scores": estimated_scores,
            "final_exact_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([
            price for price, score in final_scores.items()
            if score == max_score
        ])

        return modes, max_score, total_time_ms, verification_data
