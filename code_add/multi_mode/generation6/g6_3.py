import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g6_3(IProtocol):
    """
    子代协议：分层剪枝自adaptive协议 (Hierarchical Pruning Adaptive Protocol, HPAP)

    本协议是一个为解决复杂多众数问题而设计的全新子代协议。它严格遵循“交叉”与“突变”的核心设计准则，
    旨在实现高准确率与高效率的平衡。

    协议核心流程:
    1.  **阶段一 (交叉): 自适应布隆过滤** - 融合父代A的“帧长自适应”与“迭代过滤”思想，以及父代B的
        “严格多哈希过滤”逻辑，构建一个高效的初级筛选器，快速缩小候选价格范围。
    2.  **阶段二 (突变): 预验证候选者剪枝** - 引入一个在父代协议中均未出现过的全新机制。对第一阶段
        产生的每个候选者进行一次极低成本的独立“存在性”探测。如果某候选者在探测中未能激起任何标签
        响应（即产生一个全空帧），则它将被提前、确定性地剔除，避免其进入昂贵的最终验证阶段。
    3.  **阶段三 (交叉): 并行位图验证** - 对经过两轮筛选后剩余的、高质量的候选者们，采用源自父代A的
        高效并行位图验证方法，在单次通信中精确计算出每个候选者的最终得分。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots_1bit = 0  # 记录所有回复为1bit的时隙总数
        total_slots_bitmap = 0 # 记录并行验证阶段的时隙总数
        bits_per_bitmap_slot = 0 # 并行验证阶段每个时隙的比特宽度

        # --- 协议超参数 ---
        # 阶段一相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 40      # 当候选者数量小于此值时，停止过滤
        max_refinement_rounds = 5     # 最大过滤轮次
        high_density_threshold = 0.85 # 帧密度过高阈值
        low_density_threshold = 0.25  # 帧密度过低阈值
        min_frame_size = 64
        # 阶段二 (突变) 相关
        pruning_frame_size = 16       # 预验证剪枝阶段的帧长，极小以降低开销
        # 阶段三相关
        parallel_verification_frame_size = 128

        # ==================================================================
        #  阶段一: 自适应布隆过滤
        # ==================================================================
        # --- 交叉部分：融合父代A的自适应帧长与迭代过滤思想，与父代B的严格多哈希(布隆)过滤思想 ---
        # 本阶段采纳了父代A的迭代式过滤框架和基于通信密度的帧长自适应调整策略，这使得协议能
        # 灵活应对不同阶段的标签响应密度。同时，在过滤逻辑上，采用了父代B更为严格的多哈希
        # “全命中”准则（即布隆过滤器原理），确保了每一轮过滤都具有极高的排除效率。
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

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
            total_slots_1bit += current_filter_frame_size * num_hash_functions
            # --- [物理层模拟结束] ---

            # 应用严格的布隆过滤逻辑
            new_candidates = {p for p in candidate_prices if all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % current_filter_frame_size] > 0
                for i in range(num_hash_functions)
            )}
            candidate_prices = new_candidates
            refinement_rounds_done += 1

            # 根据帧密度自适应调整帧长
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

        # ==================================================================
        #  阶段二: 预验证候选者剪枝
        # ==================================================================
        # --- 突变部分：引入“预验证候选者剪枝”全新机制 ---
        # 这是本协议的核心创新。在两个父代协议中，一旦一个价格通过了过滤阶段，它就必须
        # 接受昂贵的验证。本协议在此引入一个全新的、极低成本的中间环节。读写器对每个
        # 候选者发起一次独立的、帧长极短（如16）的“存在性探测”。如果某个候选价格无法在
        # 这次探测中收到任何标签的响应（即通信帧全空），则证明该价格在系统中不存在，
        # 可以被100%确定地提前剔除。此举能有效过滤掉由哈希碰撞产生的伪候选者，
        # 显著降低最终验证阶段的通信负担。
        pruned_candidates: Set[int] = set()
        if candidate_prices:
            for p in sorted(list(candidate_prices)):
                total_frames += 1
                pruning_seed = generate_random_seed()
                total_bits_sent_by_reader += p.bit_length() + pruning_frame_size.bit_length() + pruning_seed.bit_length()

                # --- [物理层模拟] ---
                pruning_frame_states = [0] * pruning_frame_size
                for t in tags:
                    if t.price == p:
                        slot_id = simple_hash(t.id, pruning_seed) % pruning_frame_size
                        pruning_frame_states[slot_id] += 1
                total_slots_1bit += pruning_frame_size
                # --- [物理层模拟结束] ---

                # 只有能够产生非空帧的候选者才能通过
                if any(count > 0 for count in pruning_frame_states):
                    pruned_candidates.add(p)

        # ==================================================================
        #  阶段三: 并行位图验证
        # ==================================================================
        final_scores: Dict[int, int] = {}
        if pruned_candidates:
            total_frames += 1
            candidate_list = sorted(list(pruned_candidates))
            price_to_bit_pos_map = {price: i for i, price in enumerate(candidate_list)}
            num_final_candidates = len(candidate_list)
            bits_per_bitmap_slot = num_final_candidates

            seed_verify_par = generate_random_seed()
            total_bits_sent_by_reader += (parallel_verification_frame_size.bit_length() +
                                          seed_verify_par.bit_length() +
                                          sum(p.bit_length() for p in candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * parallel_verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify_par) % parallel_verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)
            total_slots_bitmap += parallel_verification_frame_size
            # --- [物理层模拟结束] ---

            # 解码位图以计算最终得分
            for price, bit_pos in price_to_bit_pos_map.items():
                score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                if score > 0:
                    final_scores[price] = score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        # T_tag需要分别计算1bit响应和bitmap响应的成本
        T_tag_1bit = total_slots_1bit * 1 * 0.4
        T_tag_bitmap = total_slots_bitmap * bits_per_bitmap_slot * 0.4
        T_tag = T_tag_1bit + T_tag_bitmap

        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "refinement_rounds_done": refinement_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "num_candidates_after_pruning": len(pruned_candidates),
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, verification_data
