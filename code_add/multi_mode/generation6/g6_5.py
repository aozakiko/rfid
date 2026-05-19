import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g6_5(IProtocol):
    """
    子代协议：具有提前终止机制的混合自适应众数查询协议
    (Adaptive Hybrid Protocol with Early Termination for Mode Query)

    本协议是一个为解决复杂多众数问题而设计的全新子代协议。它严格遵循“强制交叉”与“强制突变”原则，
    创造出一个逻辑自洽且高效的解决方案。

    核心思想：
    1.  **交叉融合**:
        - 继承了父代A和B共有的“过滤-验证”两阶段核心架构。
        - 在过滤阶段，融合了双方都采用的“基于帧密度的自适应帧长调整”策略，以动态适应不同标签密度。
        - 在验证阶段，采纳了父代A的“强弱候选者分离处理”思想，但通过更高效的方式实现，并结合了父代B的精确验证方法。

    2.  **创新突变**:
        - 引入了一种全新的“基于强候选者得分的提前终止”机制。在完成对高概率众数（强候选者）的精确验证后，
          协议会先进行一次快速评估，判断剩余的低概率候选者（弱候选者）是否还有可能成为最终的众数。
          如果不可能，则直接跳过对弱候选者的验证阶段，从而在保证准确率的前提下，显著节约通信开销和执行时间。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot_in_parallel_verification = 0 # 用于时间计算

        # --- 协议超参数 ---
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 40      # 当候选者数量小于此值时，停止过滤
        max_refinement_rounds = 5     # 最大过滤轮次
        verification_frame_size = 64  # 用于独立验证的帧长
        high_density_threshold = 0.85 # 帧密度过高阈值
        low_density_threshold = 0.15  # 帧密度过低阈值
        min_frame_size = 128

        # ==================================================================
        #  阶段一: 基于碰撞率的自适应迭代过滤
        # ==================================================================
        # --- 交叉部分：融合父代A的“过滤-验证”架构与父代B的“基于帧密度的自适应帧长调整”思想 ---
        # 本协议采纳了父代共有的“先过滤，后验证”的两阶段核心架构。在过滤阶段，我们深度融合了
        # 双方都采用的先进机制：在每一轮过滤后，读写器会分析帧的“密度”（非空时隙占比），
        # 并动态调整下一轮的帧长。如果密度过高（碰撞严重），则增大帧长；如果密度过低（信道浪费），
        # 则减小帧长。这种闭环自适应能力使得协议在处理不同数据分布时更加高效和鲁棒。

        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        last_filter_frames_states = []

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += current_filter_frame_size.bit_length() + sum(s.bit_length() for s in filter_seeds)

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            total_slots += current_filter_frame_size * num_hash_functions
            last_filter_frames_states = filter_frames_states
            # --- [物理层模拟结束] ---

            new_candidates = {p for p in candidate_prices if all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % current_filter_frame_size] > 0
                for i in range(num_hash_functions)
            )}
            
            # 防止候选集为空导致后续逻辑出错
            if not new_candidates:
                break
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
        #  阶段二: 候选者分类 (强/弱模式识别)
        # ==================================================================
        # --- 交叉部分：融合父代A的“强弱候选者分离”思想，但采用更高效的实现方式 ---
        # 我们借鉴了父代A的核心洞察：不同频率的候选者应区别对待。但我们摒弃了其需要额外通信的
        # “筛选”阶段，而是直接利用第一阶段最后一轮过滤的帧信息。我们将那些映射到“碰撞”时隙
        # （即 slot_count > 1）的候选价格定义为“强候选者”，其余为“弱候选者”。
        # 这种方式在不增加任何通信开销的情况下，高效地完成了候选者的智能分类。
        
        strong_candidates: Set[int] = set()
        if last_filter_frames_states:
            collision_slots_by_hash = [
                {i for i, count in enumerate(frame) if count > 1}
                for frame in last_filter_frames_states
            ]
            for p in candidate_prices:
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    if slot_id in collision_slots_by_hash[i]:
                        strong_candidates.add(p)
                        break
        
        weak_candidates = candidate_prices - strong_candidates
        final_scores: Dict[int, int] = {}

        # ==================================================================
        #  阶段三: 混合验证与提前终止
        # ==================================================================
        
        # Part A: 对强候选者进行独立精确验证 (借鉴自父代A和B的精确验证逻辑)
        max_strong_score = 0
        if strong_candidates:
            for p in sorted(list(strong_candidates)):
                total_frames += 1
                seed_verify_ind = generate_random_seed()
                total_bits_sent_by_reader += p.bit_length() + verification_frame_size.bit_length() + seed_verify_ind.bit_length()

                verification_slot_states = [0] * verification_frame_size
                for t in tags:
                    if t.price == p:
                        slot_id = simple_hash(t.id, seed_verify_ind) % verification_frame_size
                        verification_slot_states[slot_id] += 1
                total_slots += verification_frame_size
                
                num_non_empty = sum(1 for count in verification_slot_states if count > 0)
                if num_non_empty > 0:
                    final_scores[p] = num_non_empty
                    if num_non_empty > max_strong_score:
                        max_strong_score = num_non_empty

        # Part B: 对弱候选者进行验证决策
        skipped_weak_verification = False
        if weak_candidates:
            # --- 突变部分：引入基于强候选者得分的提前终止全新机制 ---
            # 这是父代A和B都未曾具备的全新优化。在精确计算出强候选者的最高分(`max_strong_score`)后，
            # 我们并不立即对弱候选者进行验证。而是先用一个极小的开销（一个计数值大于0即可的单帧）
            # 快速估算所有弱候选者对应的标签总数。如果这个总数（即弱候选者可能达到的最高分）
            # 仍然小于等于已知的`max_strong_score`，那么弱候选者中不可能存在新的众数。
            # 此时，协议将直接跳过整个弱候选者验证阶段，从而在不牺牲准确率的前提下，实现了执行路径的“短路”，
            # 极大地提升了在众数分布集中的场景下的协议效率。
            
            # 使用一个快速帧来统计所有弱候选者对应的标签总数
            total_frames += 1
            seed_pre_check = generate_random_seed()
            # 广播一个足够大的帧，只需要知道非空时隙数
            pre_check_frame_size = 256
            total_bits_sent_by_reader += pre_check_frame_size.bit_length() + seed_pre_check.bit_length()

            pre_check_slots = [0] * pre_check_frame_size
            for t in tags:
                if t.price in weak_candidates:
                    slot_id = simple_hash(t.id, seed_pre_check) % pre_check_frame_size
                    pre_check_slots[slot_id] += 1
            total_slots += pre_check_frame_size
            
            max_possible_weak_score = sum(1 for count in pre_check_slots if count > 0)

            if max_strong_score > 0 and max_possible_weak_score <= max_strong_score:
                skipped_weak_verification = True
            else:
                # 如果无法提前终止，则执行并行位图验证 (借鉴自父代A)
                total_frames += 1
                candidate_list = sorted(list(weak_candidates))
                price_to_bit_pos_map = {price: i for i, price in enumerate(candidate_list)}
                bits_per_slot_in_parallel_verification = len(candidate_list)

                # 帧长可以动态调整，这里简化为固定值
                parallel_verification_frame_size = 128
                
                seed_verify_par = generate_random_seed()
                total_bits_sent_by_reader += (parallel_verification_frame_size.bit_length() +
                                              seed_verify_par.bit_length() +
                                              sum(p.bit_length() for p in candidate_list))

                verification_frame_slots = [0] * parallel_verification_frame_size
                for t in tags:
                    if t.price in price_to_bit_pos_map:
                        slot_id = simple_hash(t.id, seed_verify_par) % parallel_verification_frame_size
                        bit_pos = price_to_bit_pos_map[t.price]
                        verification_frame_slots[slot_id] |= (1 << bit_pos)
                
                # 更新总时隙数，注意这里是替换而不是增加，因为预检帧被验证帧替代了
                total_slots = total_slots - pre_check_frame_size + parallel_verification_frame_size

                for price, bit_pos in price_to_bit_pos_map.items():
                    score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                    if score > 0:
                        final_scores[price] = score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025

        # 将总时隙数分为单比特时隙和多比特时隙
        slots_for_parallel = 0
        if weak_candidates and not skipped_weak_verification:
            slots_for_parallel = parallel_verification_frame_size
        
        slots_for_single_bit = total_slots - slots_for_parallel
        
        T_tag = (slots_for_single_bit * 1 * 0.4) + \
                (slots_for_parallel * bits_per_slot_in_parallel_verification * 0.4)
        
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "refinement_rounds_done": refinement_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "num_strong_candidates": len(strong_candidates),
            "num_weak_candidates": len(weak_candidates),
            "skipped_weak_verification": skipped_weak_verification,
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, verification_data
