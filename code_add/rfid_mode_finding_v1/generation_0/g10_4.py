import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_4(IProtocol):
    """
    子代协议: 动态碰撞阈值协议 (Dynamic Collision Threshold Protocol, DCTP)

    本协议是一个为解决复杂多众数查询问题而设计的全新子代协议。
    它严格遵循交叉与突变的设计原则，旨在实现极致的准确率与卓越的通信效率。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        # 阶段一：预扫描相关 (继承自父代A)
        initial_scan_bins = 64

        # 阶段二：过滤相关 (融合父代A和B)
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_stop_threshold = 40
        max_filter_rounds = 6
        min_frame_size = 64
        high_density_threshold = 0.8
        low_density_threshold = 0.2
        stagnation_threshold = 0.05

        # 阶段二：突变机制相关
        high_collision_rate_threshold = 0.5  # 高碰撞率场景阈值
        low_collision_rate_threshold = 0.1   # 低碰撞率场景阈值
        
        # 阶段三：验证相关 (继承自父代A和B)
        verification_frame_size = 128

        # ==================================================================
        #  阶段一: 热点区域预扫描 (Crossover from Parent A)
        # ==================================================================
        # --- 交叉部分：融合父代A的“热点区域预扫描”思想与父代B的“碰撞强度过滤”思想 ---
        # 本协议首先采纳父代A的核心优势策略：在协议开始时进行一次粗粒度的“热点区域预扫描”。
        # 这次扫描通过简单的分箱和碰撞检测，能以极低的成本将初始的巨大价格范围（1-1000）迅速缩减到一个小的、更有可能包含众数的候选价格集。
        
        total_frames += 1
        total_bits_sent_by_reader += initial_scan_bins.bit_length()
        
        # --- [物理层模拟] ---
        scan_frame_counts = [0] * initial_scan_bins
        price_range_width = price_max - price_min + 1
        for t in tags:
            bin_index = math.floor(((t.price - price_min) / price_range_width) * initial_scan_bins)
            bin_index = min(bin_index, initial_scan_bins - 1)
            scan_frame_counts[bin_index] += 1
        
        # 标签仅需回复1bit表示存在性，因此每个bin的通信成本为1 bit
        total_tag_reply_bits += initial_scan_bins * 1 
        # --- [物理层模拟结束] ---

        # 读写器根据碰撞状态（计数 > 1）识别热点区域
        hot_zone_indices = {i for i, count in enumerate(scan_frame_counts) if count > 1}
        
        # 如果没有碰撞区，则退化为寻找所有非空区域，保证协议的鲁棒性
        if not hot_zone_indices:
            hot_zone_indices = {i for i, count in enumerate(scan_frame_counts) if count > 0}

        candidate_prices: Set[int] = set()
        if hot_zone_indices:
            for i in hot_zone_indices:
                start_price = price_min + math.floor(i * (price_range_width / initial_scan_bins))
                end_price = price_min + math.floor((i + 1) * (price_range_width / initial_scan_bins))
                for p in range(start_price, min(end_price, price_max + 1)):
                    candidate_prices.add(p)
        
        # 如果预扫描后无候选，则使用全集，确保协议能继续进行
        if not candidate_prices:
            candidate_prices = set(range(price_min, price_max + 1))


        # ==================================================================
        #  阶段二: 动态阈值迭代过滤 (Crossover from B + Mutation)
        # ==================================================================
        # 紧接着，在第二阶段的迭代过滤中，我们引入了父代B的“碰撞强度”作为核心过滤准则。
        # 与传统的存在性过滤不同，此方法要求一个候选价格不仅要存在，还必须在至少一个哈希映射中引发碰撞，从而更积极地淘汰低频价格，聚焦于潜在的众数。
        # 同时，协议继承了两个父代共有的自适应帧长调整和停滞检测机制，以确保过滤过程的效率和鲁棒性。

        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_stop_threshold and filter_rounds_done < max_filter_rounds:
            prev_candidate_count = len(candidate_prices)
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32)

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            
            total_tag_reply_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # --- 突变部分：引入“动态碰撞阈值（Dynamic Collision Threshold, DCT）”全新机制 ---
            # 父代B引入了静态的“必须碰撞”规则（collision_count > 0），这是一个强有力的启发式方法，但可能在特定密度下过于激进，导致误删真实的低频众数。
            # 本协议对此进行了“突变”优化，引入了一个全新的“动态碰撞阈值”机制。
            # 在每一轮过滤中，读写器不再使用固定的碰撞要求，而是首先计算当前所有帧的“全局碰撞率”（发生碰撞的时隙 / 所有非空时隙）。
            # 然后，基于这个实时反馈的碰撞率来动态设定本轮候选者存活所需的最小碰撞次数。
            # 当网络拥塞、碰撞普遍时，阈值会自动调高，以更强的标准筛选出“超级热门”的价格。
            # 当网络稀疏、碰撞罕见时，阈值会自动降低（甚至降为0，即退化为仅需存在），以避免因哈希幸运而错误地淘汰真正的众数。
            # 这种自适应的过滤强度是父代协议所不具备的，它使协议在不同标签密度和分布下都更具鲁棒性和智能性。
            
            total_non_empty_slots = sum(1 for frame in filter_frames_states for count in frame if count > 0)
            total_collision_slots = sum(1 for frame in filter_frames_states for count in frame if count > 1)
            collision_rate = total_collision_slots / total_non_empty_slots if total_non_empty_slots > 0 else 0

            required_collisions = 1 # 默认要求至少1次碰撞
            if collision_rate > high_collision_rate_threshold:
                required_collisions = 2 # 碰撞非常普遍，提高门槛以筛选“超级碰撞者”
            elif collision_rate < low_collision_rate_threshold:
                required_collisions = 0 # 碰撞稀少，为避免错杀，退化为仅需存在

            new_candidates = set()
            for p in candidate_prices:
                is_present_in_all_frames = True
                collision_count = 0
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    slot_state = filter_frames_states[i][slot_id]
                    if slot_state == 0:
                        is_present_in_all_frames = False
                        break
                    if slot_state > 1:
                        collision_count += 1
                
                if is_present_in_all_frames and collision_count >= required_collisions:
                    new_candidates.add(p)

            if not new_candidates and len(candidate_prices) > 0:
                break 
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # (交叉) 继承自父代的帧密度自适应调整逻辑
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size) if current_filter_frame_size > 0 else 0
            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)
            
            # (交叉) 继承自父代的停滞感知提前终止逻辑
            reduction_ratio = (prev_candidate_count - len(candidate_prices)) / prev_candidate_count if prev_candidate_count > 0 else 0
            if filter_rounds_done > 1 and reduction_ratio < stagnation_threshold:
                break

        # ==================================================================
        #  阶段三: 并行位图精确验证 (Crossover from Parent A & B)
        # ==================================================================
        final_counts: Dict[int, int] = {}
        final_candidate_list = sorted(list(candidate_prices))
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            bits_per_slot_verify = len(final_candidate_list)
            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (verification_frame_size.bit_length() + 32 +
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

            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                count = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if count > 0:
                    final_counts[price] = count
        
        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_reply_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "initial_candidates_after_scan": len(candidate_prices) if filter_rounds_done == 0 else "N/A",
            "filter_rounds_done": filter_rounds_done,
            "final_candidates_after_filter": len(final_candidate_list),
            "final_counts": final_counts,
        }

        if not final_counts:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_counts.values())
        modes = sorted([p for p, s in final_counts.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data
