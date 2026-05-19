import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g2_1(IProtocol):
    """
    自适应混合剪枝众数查询协议 (Adaptive Hybrid Pruning Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        PRECHECK_BINS = 8  # 预检阶段的分箱数
        PRECHECK_FRAME_SIZE = 64 # 预检阶段的帧大小
        INITIAL_SCORING_FRAME_SIZE = 128 # 评分阶段的初始帧大小
        MIN_FRAME_SIZE = 32 # 帧大小动态调整下限
        MAX_FRAME_SIZE = 512 # 帧大小动态调整上限
        COLLISION_HIGH_THRESHOLD = 0.4 # 碰撞率高阈值，用于增大帧
        COLLISION_LOW_THRESHOLD = 0.1 # 碰撞率低阈值，用于减小帧
        
        price_to_score_map = {}
        
        # --- 交叉部分：融合父代A的“非空时隙评分”思想与父代B的“分阶段剪枝”思想 ---
        # 阶段一：范围剪枝 (Inherited from Parent B: g1_1)
        # 采用父代B的分箱预检策略，快速排除不存在任何标签的价格区间，大大减少后续需要详查的价格数量。
        
        active_prices = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        price_bins = [
            list(range(price_min + i * bin_size, min(price_min + (i + 1) * bin_size, price_max + 1)))
            for i in range(PRECHECK_BINS)
        ]

        for p_bin in price_bins:
            if not p_bin: continue
            
            # --- 读写器行为: 广播指令 ---
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
            
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1 # 1 bit reply
            
            # --- 读写器行为: 分析帧 ---
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            # 如果没有活跃价格，提前终止并计算时间
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found"}

        # 阶段二：精确评分 (Inherited from Parent A: ModeQueryTCS)
        # 对剪枝后筛选出的活跃价格，逐一进行探测，并采用父代A鲁棒的“非空时隙数”作为核心评分标准。
        # 这种方式比父代B的估计和碰撞计数更直接、更准确。
        
        sorted_active_prices = sorted(list(active_prices))
        
        # --- 突变部分：引入“基于碰撞率的动态帧大小调整”全新机制 ---
        # 在父代A和B中，各阶段的frame_size都是固定的。本协议引入一个创新：
        # 在精确评分阶段，读写器会根据上一轮探测的碰撞情况，动态调整下一轮的frame_size。
        # 如果碰撞严重，说明当前价格的标签数量远超预期，应增大frame_size以获得更准确的非空时隙数。
        # 如果几乎没有碰撞，可以适当减小frame_size以节约通信开销。
        # 这种自适应机制使得协议能更智能地分配通信资源，在保证精度的同时提升效率。
        
        current_frame_size = INITIAL_SCORING_FRAME_SIZE
        
        for price in sorted_active_prices:
            # --- 读写器行为: 广播对价格 price 的探测指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed.bit_length()

            # --- [物理层模拟] ---
            slot_states = [0] * current_frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % current_frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += current_frame_size * 1

            # --- 读写器分析聚合信号并为突变机制收集数据 ---
            num_non_empty_slots = 0
            num_collisions = 0
            for count in slot_states:
                if count > 0:
                    num_non_empty_slots += 1
                if count > 1:
                    num_collisions += 1
            
            # 使用父代A的核心思想进行评分
            if num_non_empty_slots > 0:
                price_to_score_map[price] = num_non_empty_slots

            # 执行突变逻辑：动态调整下一轮的帧大小
            collision_rate = num_collisions / current_frame_size
            if collision_rate > COLLISION_HIGH_THRESHOLD:
                current_frame_size = min(MAX_FRAME_SIZE, int(current_frame_size * 1.5))
            elif collision_rate < COLLISION_LOW_THRESHOLD:
                current_frame_size = max(MIN_FRAME_SIZE, int(current_frame_size * 0.75))
            # 否则保持当前帧大小不变

        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "precheck_bins": PRECHECK_BINS,
            "active_price_count": len(active_prices),
            "final_scores": price_to_score_map,
            "mutation_info": "Frame size is dynamically adjusted based on collision rate"
        }
        
        if not price_to_score_map:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = max(price_to_score_map.values())
        modes = sorted([
            price for price, score in price_to_score_map.items() 
            if score == max_score
        ])
        
        return modes, max_score, total_time_ms, verification_data
