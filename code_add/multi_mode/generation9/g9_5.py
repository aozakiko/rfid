import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_5(IProtocol):
    """
    子代协议: 自适应碰撞强度协议 (Adaptive Collision Intensity Protocol, ACIP)

    本协议是一个全新的、为解决多众数查询问题而设计的子代协议。
    它严格遵循交叉与突变的设计原则，旨在实现高准确率与高效率的平衡。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        # 阶段一：过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 45      # 当候选者数量小于此值时，停止过滤
        max_filter_rounds = 6         # 最大过滤轮次，防止无限循环
        
        # 继承自父代的自适应帧长调整参数
        high_density_threshold = 0.85 # 帧密度过高阈值 (用于扩大帧)
        low_density_threshold = 0.25  # 帧密度过低阈值 (用于缩小帧)
        min_frame_size = 64           # 过滤帧的最小尺寸

        # 继承自父代的停滞感知参数
        stagnation_threshold = 0.05
        min_rounds_before_stagnation_check = 2
        
        # 阶段二：验证相关
        verification_frame_size = 128

        # ==================================================================
        #  阶段一: 基于碰撞强度的自适应迭代过滤
        # ==================================================================
        
        # --- 交叉部分：融合父代A的自适应与停滞检测思想与父代B的多哈希迭代过滤思想 ---
        # 本协议采纳了两个父代共有的“过滤-验证”两阶段架构。
        # 在核心的过滤阶段，我们融合了父代B的多哈希函数迭代过滤框架，通过多轮通信逐步筛选候选价格。
        # 同时，我们深度集成了父代A的精髓：1) 基于实时帧密度动态调整下一轮通信的帧长，以适应标签分布的变化；
        # 2) 引入“停滞感知”机制，当候选集缩减效率过低时提前终止过滤，避免不必要的通信开销。
        # 这种融合使得过滤阶段既有鲁棒的筛选能力，又具备智能的动态调节和提前终止能力。

        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            prev_candidate_count = len(candidate_prices)

            # 广播当前轮次的帧长和哈希种子
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            bits_sent_this_round = current_filter_frame_size.bit_length()
            for seed in filter_seeds:
                bits_sent_this_round += seed.bit_length() # 假设种子长度可变
            total_bits_sent_by_reader += bits_sent_this_round

            # --- [物理层模拟] ---
            # 读写器准备接收帧，每个slot记录响应的标签数量
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            
            # 此阶段标签仅需回复1bit表示存在性，通信成本与帧长相关
            total_tag_reply_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # --- 读写器分析聚合信号，生成新的候选集 ---
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
                
                # --- 突变部分：引入“碰撞强度”作为核心过滤准则的全新机制 ---
                # 这是父代A和B都未曾具备的全新优化。在传统的布隆过滤器逻辑（必须在所有哈希函数
                # 对应的时隙中都存在）之上，我们增加了一个更强的约束：一个价格要想在本轮中幸存，
                # 它不仅要处处存在，还必须“至少引发一次碰撞”。
                # 其核心逻辑是：众数（高频价格）极大概率会在至少一个哈希映射中与其他标签发生碰撞，
                # 而那些频率较低的“冷门”价格则更有可能在所有哈希映射中都幸运地只占据一个“单例”时隙。
                # 通过主动过滤掉这些未引发任何碰撞的“幸运儿”，协议可以更快速、更激进地剔除噪声，
                # 从而聚焦于真正具有“众数”潜力的价格。
                if is_present_in_all_frames and collision_count > 0:
                    new_candidates.add(p)

            # 如果没有候选者幸存，为防止候选集为空，提前终止
            if not new_candidates:
                break
            
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # (交叉) 继承自父代的帧碰撞率自适应调整逻辑
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2 # 碰撞太严重，扩大帧
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size) # 太稀疏，缩小帧
            
            # (交叉) 继承自父代的停滞感知提前终止逻辑
            if filter_rounds_done >= min_rounds_before_stagnation_check:
                reduction_ratio = (prev_candidate_count - len(candidate_prices)) / prev_candidate_count if prev_candidate_count > 0 else 0
                if reduction_ratio < stagnation_threshold:
                    break # 过滤效果停滞，提前跳出循环

        # ==================================================================
        #  阶段二: 高效并行精确验证
        # ==================================================================

        # --- 交叉部分：融合父代A与B共有的高效并行验证思想 ---
        # 经过第一阶段高效的自适应过滤后，候选者数量已被大幅削减。此时，本协议
        # 切换到两个父代共同采用的核心优势策略：并行位图验证。读写器将所有剩余的
        # 候选价格编码为位掩码的不同位，并通过一次通信让所有相关标签在同一个帧内
        # 并行地报告自己的存在。读写器仅需分析这一个聚合帧，即可同时解码出所有
        # 候选价格的准确计数值，以极高的效率完成最终的精确计数。

        final_scores = {}
        num_final_candidates = len(candidate_prices)

        if candidate_prices:
            total_frames += 1
            candidate_list = sorted(list(candidate_prices))
            price_to_bit_pos_map = {price: i for i, price in enumerate(candidate_list)}
            
            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                          seed_verify.bit_length() +
                                          sum(p.bit_length() for p in candidate_list))

            # --- [物理层模拟] ---
            # 标签回复的比特数等于最终候选者的数量
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)
            
            total_tag_reply_bits += verification_frame_size * num_final_candidates
            # --- [物理层模拟结束] ---
            
            # 读写器解码聚合帧，并行计算所有候选者的得分
            for price, bit_pos in price_to_bit_pos_map.items():
                score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                if score > 0:
                    final_scores[price] = score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        # 注意: 协议A和B中对 total_tag_reply_bits 的计算方式不同，这里统一采用 bits_per_slot * total_slots
        T_tag = total_tag_reply_bits * 0.4 
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "filter_rounds_done": filter_rounds_done,
            "final_filter_frame_size": current_filter_frame_size,
            "num_final_candidates": num_final_candidates,
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([
            price for price, score in final_scores.items()
            if score == max_score
        ])

        return modes, max_score, total_time_ms, verification_data
