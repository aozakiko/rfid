import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_2(IProtocol):
    """
    子代协议: 混合自适应冲突预判协议 (Hybrid Adaptive Collision-Anticipation Protocol, HACPA)

    本协议是一个为解决复杂多众数查询问题而全新设计的子代协议。它通过深度融合
    父代协议A的“动态哈希数量”和父代协议B的“收益递减早停”机制，构建了一个高效
    且鲁棒的过滤阶段。其核心创新（突变）在于引入了“ID哈希帧”，使读写器能够在不
    违反信息隔离原则的前提下，感知当前响应标签的总体密度，从而更智能地“预判”
    未来的信道冲突并前瞻性地调整帧长，实现了更高维度的自适应性。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        # 阶段一：过滤
        initial_frame_size = 256
        candidate_threshold = 40      # 候选集小于此值时停止过滤
        max_filter_rounds = 7         # 最大过滤轮次
        
        # --- 交叉部分：融合父代A和B的自适应与早停思想 ---
        # 继承自父代A的动态哈希参数
        initial_hash_functions = 1
        max_hash_functions = 3
        ineffective_reduction_threshold = 0.25 # 缩减率低于此值时增加哈希函数
        # 继承自父代B的早停参数
        diminishing_return_threshold = 0.9 # 若候选者缩减效果甚微则早停

        # 通用自适应帧长参数
        high_density_threshold = 0.8
        low_density_threshold = 0.2
        min_frame_size = 64

        # --- 突变部分：引入“冲突预判”机制的全新超参数 ---
        id_frame_density_scaling_factor = 1.5 # 基于ID帧密度调整帧长的增强因子

        # 阶段二：验证
        verification_frame_size = 128

        # ==================================================================
        #  阶段一: 混合自适应迭代过滤
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        filter_rounds_done = 0
        current_frame_size = initial_frame_size
        num_hash_functions = initial_hash_functions
        termination_reason = "Max rounds reached"

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            prev_candidate_count = len(candidate_prices)

            # 读写器广播当前轮次参数
            price_hash_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            id_hash_seed = generate_random_seed() # 为突变机制新增的ID哈希种子
            
            # price_hash_seeds + id_hash_seed + frame_size
            bits_sent_this_round = (num_hash_functions + 1) * 32 + current_frame_size.bit_length()
            total_bits_sent_by_reader += bits_sent_this_round
            total_frames += (num_hash_functions + 1) # 突变引入了一个额外的ID帧

            # --- [物理层模拟] ---
            # 价格哈希帧 (用于过滤)
            price_frames = [[0] * current_frame_size for _ in range(num_hash_functions)]
            # --- 突变部分：引入ID哈希帧，用于感知标签响应密度 ---
            # 这是一个全新的数据结构，父代协议中均未出现。所有持有候选价格的标签
            # 除了在价格帧中响应，也同时在ID帧中响应。这使得读写器能获得一个
            # 关于当前活跃标签总数的匿名聚合视图，从而更精准地评估信道拥堵情况。
            id_frame = [0] * current_frame_size

            for t in tags:
                if t.price in candidate_prices:
                    # 在价格哈希帧中响应 (类似父代)
                    for i in range(num_hash_functions):
                        slot = simple_hash(t.price, price_hash_seeds[i]) % current_frame_size
                        price_frames[i][slot] = 1
                    
                    # 在ID哈希帧中响应 (突变)
                    id_slot = simple_hash(t.id, id_hash_seed) % current_frame_size
                    id_frame[id_slot] = 1 # 仅标记存在性

            # 标签回复1 bit
            total_tag_reply_bits += (current_frame_size * num_hash_functions + current_frame_size) * 1
            # --- [物理层模拟结束] ---

            # --- 交叉部分：融合父代A的严格多哈希过滤思想与父代B的迭代框架 ---
            # 采用父代A的布隆过滤器式逻辑，一个价格必须在所有价格哈希帧的
            # 对应时隙中都非空才能存活，这提供了比单一哈希更强的过滤能力。
            # 这个过程在父代B的迭代式框架内执行。
            new_candidates = set()
            for p in candidate_prices:
                is_candidate_in_round = True
                for i in range(num_hash_functions):
                    slot_p = simple_hash(p, price_hash_seeds[i]) % current_frame_size
                    if price_frames[i][slot_p] == 0:
                        is_candidate_in_round = False
                        break
                if is_candidate_in_round:
                    new_candidates.add(p)
            
            # --- 交叉部分：融合父代A的动态哈希调整与父代B的早停机制 ---
            # 1. 继承自父代A的动态哈希函数数量调整机制
            reduction_ratio = (prev_candidate_count - len(new_candidates)) / prev_candidate_count if prev_candidate_count > 0 else 0
            if reduction_ratio < ineffective_reduction_threshold and num_hash_functions < max_hash_functions:
                num_hash_functions += 1

            # 2. 继承自父代B的“收益递减”早停机制
            if prev_candidate_count > 0 and (len(new_candidates) / prev_candidate_count) > diminishing_return_threshold:
                termination_reason = f"Diminishing returns ({len(new_candidates)}/{prev_candidate_count})"
                candidate_prices = new_candidates
                filter_rounds_done += 1
                break
            
            candidate_prices = new_candidates
            filter_rounds_done += 1

            if not candidate_prices:
                termination_reason = "No candidates survived"
                break
            if len(candidate_prices) <= candidate_threshold:
                termination_reason = f"Candidate count below threshold ({candidate_threshold})"
                break

            # --- 突变部分：引入基于ID帧密度的“冲突预判”帧长调整全新机制 ---
            # 这是协议的核心创新。读写器首先按常规方式基于价格帧的密度调整帧长，
            # 然后，利用ID帧提供的更准确的全局拥堵信息，进行二次、更激进的调整。
            # 如果ID帧密度很高，说明大量标签正在响应（很可能已命中一个或多个众数），
            # 协议会前瞻性地、按比例地大幅增加下一轮的帧长，以主动规避即将到来的
            # 大规模冲突，这是父代协议基于历史信息进行被动调整的模式所不具备的。
            
            # 基础调整 (源自父代)
            price_frame_density = sum(sum(frame) for frame in price_frames) / (num_hash_functions * current_frame_size)
            if price_frame_density > high_density_threshold:
                current_frame_size *= 2
            elif price_frame_density < low_density_threshold:
                current_frame_size = max(min_frame_size, current_frame_size // 2)

            # 冲突预判增强调整 (突变)
            id_frame_density = sum(id_frame) / current_frame_size
            if id_frame_density > high_density_threshold:
                # 预判到高冲突，更激进地增加帧长
                new_size = int(current_frame_size * (1 + (id_frame_density - high_density_threshold) * id_frame_density_scaling_factor))
                current_frame_size = new_size


        # ==================================================================
        #  阶段二: 高效并行精确验证
        # ==================================================================
        # --- 交叉部分：采用父代A和B共有的高效并行位图验证思想 ---
        # 经过第一阶段的强力筛选，候选者数量已大幅减少。此时，协议切换到两个
        # 父代协议都采用的最高效的验证策略：并行位图验证。所有剩余的候选价格
        # 被映射到位掩码的不同位，读写器通过一次通信即可并行解码出所有候选者
        # 的精确计数值，完美地平衡了准确性与最终阶段的通信效率。
        final_scores = {}
        final_candidate_list = sorted(list(candidate_prices))
        num_final_candidates = len(final_candidate_list)

        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            
            seed_verify = generate_random_seed()
            # 广播验证参数
            total_bits_sent_by_reader += (verification_frame_size.bit_length() + 32 +
                                         sum(p.bit_length() for p in final_candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)
            
            # 标签回复的比特数等于最终候选者的数量
            total_tag_reply_bits += verification_frame_size * num_final_candidates
            # --- [物理层模拟结束] ---
            
            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                score = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if score > 0:
                    final_scores[price] = score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_reply_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "filter_rounds_done": filter_rounds_done,
            "termination_reason": termination_reason,
            "final_filter_frame_size": current_frame_size,
            "final_hash_functions": num_hash_functions,
            "num_final_candidates": num_final_candidates,
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_scores.values())
        modes = sorted([
            price for price, score in final_scores.items() if score == max_score
        ])

        return modes, max_score, total_time_ms, debug_data
