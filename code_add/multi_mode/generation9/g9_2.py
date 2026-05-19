import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_2(IProtocol):
    """
    子代协议: 收益感知型分批验证协议 (Return-Aware Batched Verification Protocol)

    本协议是一个为解决多众数查询问题而设计的全新子代协议。它严格遵循信息隔离原则，
    其核心设计体现了对两个父代协议思想的深度融合（交叉）与创新优化（突变）。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 40      # 当候选者数量小于此值时，停止过滤
        max_filter_rounds = 6         # 最大过滤轮次
        high_density_threshold = 0.85 # 帧密度过高阈值 (用于扩大帧)
        low_density_threshold = 0.25  # 帧密度过低阈值 (用于缩小帧)
        min_frame_size = 64           # 过滤帧的最小尺寸

        # 阶段二: 验证相关
        verification_frame_size = 128
        batch_size_threshold = 32     # 验证阶段中，单个批次处理的最大候选者数量

        # --- 突变部分：引入“过滤收益递减提前终止”全新机制 ---
        min_reduction_threshold = 5   # 如果一轮过滤移除的候选者数量少于此值，则提前终止过滤

        # ==================================================================
        #  阶段一: 自适应过滤
        # --- 交叉部分：融合父代A和父代B共有的“自适应帧长”与“多哈希迭代过滤”核心思想 ---
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        terminated_by_diminishing_returns = False

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            num_candidates_before = len(candidate_prices)
            
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 读写器广播帧长和哈希种子
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32) # 假设种子是32位

            # --- [物理层模拟] ---
            # 读写器准备接收帧，每个slot记录响应次数
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            # 在过滤阶段，标签只回复1比特表示存在性
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # 读写器分析聚合信号
            new_candidates = set()
            for p in candidate_prices:
                is_still_candidate = True
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    slot_count = filter_frames_states[i][slot_id]

                    if slot_count == 0:
                        is_still_candidate = False
                        break
                
                if is_still_candidate:
                    new_candidates.add(p)

            if not new_candidates:
                break
            
            candidate_prices = new_candidates
            filter_rounds_done += 1
            num_candidates_after = len(candidate_prices)

            # --- 突变部分：引入“过滤收益递减提前终止”全新机制 ---
            # 检查本轮过滤的效率。如果移除的候选者太少，说明收益递减，应提前终止。
            if (num_candidates_before - num_candidates_after) < min_reduction_threshold:
                terminated_by_diminishing_returns = True
                break

            # 交叉部分：根据帧密度自适应调整帧长 (继承自两个父代的核心思想)
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)
        
        # ==================================================================
        #  阶段二: 分批并行位图验证
        # --- 交叉部分：融合父代A的“分批处理”思想与父代B（及A）的“并行位图验证”核心逻辑 ---
        # ==================================================================
        final_scores: Dict[int, int] = {}
        
        if candidate_prices:
            candidate_list = sorted(list(candidate_prices))
            num_candidates = len(candidate_list)
            num_batches = math.ceil(num_candidates / batch_size_threshold)
            
            for i in range(num_batches):
                batch_candidates = candidate_list[i * batch_size_threshold : (i + 1) * batch_size_threshold]
                if not batch_candidates: continue

                total_frames += 1
                price_to_bit_pos_map = {price: j for j, price in enumerate(batch_candidates)}
                bits_per_slot = len(batch_candidates)

                seed_verify = generate_random_seed()
                # 读写器广播验证参数
                total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                              seed_verify.bit_length() +
                                              sum(p.bit_length() for p in batch_candidates))

                # --- [物理层模拟] ---
                verification_frame_slots = [0] * verification_frame_size
                for t in tags:
                    if t.price in price_to_bit_pos_map:
                        slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                        bit_pos = price_to_bit_pos_map[t.price]
                        verification_frame_slots[slot_id] |= (1 << bit_pos)
                
                total_tag_response_bits += verification_frame_size * bits_per_slot
                # --- [物理层模拟结束] ---
                
                # 读写器分析聚合信号，计算当前批次中每个价格的得分
                for price, bit_pos in price_to_bit_pos_map.items():
                    score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                    if score > 0:
                        final_scores[price] = final_scores.get(price, 0) + score
        
        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "filter_rounds_done": filter_rounds_done,
            "terminated_by_diminishing_returns": terminated_by_diminishing_returns,
            "num_candidates_after_filter": len(candidate_prices),
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data
