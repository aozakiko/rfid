import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g5_4(IProtocol):
    """
    子代协议: 全面验证与歧义解决协议 (Comprehensive Verification & Ambiguity Resolution Protocol)

    本协议是为解决高挑战性的多众数查询问题而设计的全新子代协议。它严格遵循
    交叉与突变的设计准则，将协议设计的核心从“效率优先”转向“准确率优先”，
    旨在从海量标签中以极高的置信度找出一个高频价格众数。

    协议流程分为三个核心阶段:

    1.  **自适应迭代过滤阶段 (交叉)**:
        # --- 交叉部分：融合父代B的自适应迭代过滤思想与父代A/B的多哈希过滤机制 ---
        此阶段完全继承了父代B的核心优势：通过多轮通信迭代，并根据每轮的帧碰撞密度
        动态调整帧长，逐步“精炼”出一个规模很小的候选价格集合。这个阶段的目标是
        高效地排除绝大多数无关价格，为后续的高成本精确验证减负。

    2.  **全面动态精确计数阶段 (交叉)**:
        # --- 交叉部分：融合父代A的动态追溯精确计数思想 ---
        与上一版协议不同，本协议放弃了有风险的启发式排名，而是回归到更稳健的策略。
        此阶段为所有在第一阶段幸存下来的候选者，逐一执行继承自父代A的动态精确计数。
        它通过“不成功则加倍帧长重试”的策略，为每一个候选者计算出准确的计数值。
        这种全面验证的方式，虽然牺牲了时间，但从根本上杜绝了因排序错误而丢失
        真正众数的可能性。

    3.  **高精度歧义解决阶段 (突变)**:
        # --- 突变部分：引入基于计数值相似度的高精度重验证机制 ---
        这是为最大化准确率而引入的全新机制。在完成对所有候选者的初步精确计数后，
        协议会检查计数值最高的几个候选者。如果它们的计数值非常接近（例如，差距在10%以内），
        协议会判定当前结果存在“歧义”，因为微小的估算误差可能影响最终排序。此时，
        协议会启动一次性的“决胜局”：仅针对这几个有争议的候选者，使用一个远大于
        常规的初始帧长，进行一次高精度重验证。这种机制以小部分额外的时间开销，
        换取了在关键结果上极高的分辨能力与最终的准确性。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1

        # --- 协议超参数 ---
        # 阶段一: 过滤
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 32
        max_refinement_rounds = 5
        high_density_threshold = 0.9
        low_density_threshold = 0.1
        min_frame_size = 128
        # 阶段二: 验证
        INITIAL_VERIFICATION_FRAME_SIZE = 64
        MAX_VERIFICATION_FRAME_SIZE = 8192
        # 阶段三: 突变
        CONFIDENCE_THRESHOLD = 0.1 # 计数值差距小于10%则认为有歧义
        HIGH_PRECISION_FRAME_SIZE = 1024 # 歧义解决阶段使用的大帧长

        # ==================================================================
        #  阶段一: 自适应迭代过滤 (继承自父代B)
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            bits_sent_this_round = current_filter_frame_size.bit_length()
            for seed in filter_seeds:
                bits_sent_this_round += seed.bit_length()
            total_bits_sent_by_reader += bits_sent_this_round

            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            total_slots += current_filter_frame_size * num_hash_functions

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
            refinement_rounds_done += 1

            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

        # ==================================================================
        #  阶段二: 全面动态精确计数 (继承自父代A)
        # ==================================================================
        final_counts = {}
        for price in candidate_prices:
            current_frame_size = INITIAL_VERIFICATION_FRAME_SIZE
            estimated_count = float('inf')

            while estimated_count == float('inf') and current_frame_size <= MAX_VERIFICATION_FRAME_SIZE:
                seed = generate_random_seed()
                total_frames += 1
                total_bits_sent_by_reader += (seed.bit_length() + current_frame_size.bit_length() + price.bit_length())

                verification_slot_states = [0] * int(current_frame_size)
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % int(current_frame_size)
                        verification_slot_states[slot_id] += 1
                total_slots += int(current_frame_size)

                num_zeros = verification_slot_states.count(0)
                if num_zeros == current_frame_size:
                    estimated_count = 0
                elif num_zeros == 0:
                    current_frame_size *= 2
                    estimated_count = float('inf')
                else:
                    estimated_count = -current_frame_size * math.log(num_zeros / current_frame_size)
            
            final_counts[price] = math.ceil(estimated_count) if estimated_count != float('inf') else float('inf')

        # ==================================================================
        #  阶段三: 高精度歧义解决 (全新突变机制)
        # ==================================================================
        ambiguity_resolved = False
        if len(final_counts) > 1:
            # Sort by value, then by key for deterministic behavior
            sorted_candidates = sorted(final_counts.items(), key=lambda item: (-item[1], item[0]))
            max_count = sorted_candidates[0][1]
            second_max_count = sorted_candidates[1][1]

            if max_count > 0 and second_max_count > 0 and (max_count - second_max_count) / max_count < CONFIDENCE_THRESHOLD:
                ambiguity_resolved = True
                ambiguous_prices = {p for p, c in final_counts.items() if c >= second_max_count}
                
                for price in ambiguous_prices:
                    # 使用一个固定的、较大的帧长进行高精度重验证
                    current_frame_size = HIGH_PRECISION_FRAME_SIZE
                    seed = generate_random_seed()
                    total_frames += 1
                    total_bits_sent_by_reader += (seed.bit_length() + current_frame_size.bit_length() + price.bit_length())

                    verification_slot_states = [0] * int(current_frame_size)
                    for t in tags:
                        if t.price == price:
                            slot_id = simple_hash(t.id, seed) % int(current_frame_size)
                            verification_slot_states[slot_id] += 1
                    total_slots += int(current_frame_size)

                    num_zeros = verification_slot_states.count(0)
                    if num_zeros == 0:
                        # Frame is saturated, implies very high count
                        final_counts[price] = float('inf')
                    elif num_zeros < current_frame_size:
                        # Valid range for logarithm calculation
                        estimated_count = -current_frame_size * math.log(num_zeros / current_frame_size)
                        final_counts[price] = math.ceil(estimated_count) # 更新为更高精度的计数值
                    else: # num_zeros == current_frame_size
                        # No tags responded
                        final_counts[price] = 0

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "refinement_rounds_done": refinement_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "final_counts": final_counts,
            "ambiguity_resolved": ambiguity_resolved,
            "total_frames": total_frames
        }

        if not final_counts:
            return [], 0, total_time_ms, verification_data
        
        max_score = -1
        has_inf = any(score == float('inf') for score in final_counts.values())

        if has_inf:
            max_score = float('inf')
        else:
            max_score = max(final_counts.values()) if final_counts else 0

        modes = sorted([
            price for price, score in final_counts.items()
            if score == max_score
        ])
        
        display_score = int(max_score) if not has_inf else float('inf')
        
        return modes, display_score, total_time_ms, verification_data

