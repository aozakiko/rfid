import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g6_5(IProtocol):
    """
    三阶段漏斗众数查询协议 (Three-Stage Funnel Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，通过深度融合两个父代协议的核心思想，并引入一个创新的
    “捆绑候选者验证”阶段，实现了在保证高准确率的前提下的性能优化。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTER_FRAME_SIZE = 1024       # 阶段一：全局候选过滤的帧长
        VERIFICATION_BINS = 100        # 阶段二：捆绑验证的分箱数
        VERIFICATION_FRAME_SIZE = 32   # 阶段二：捆绑验证的帧长
        SCORING_FRAME_SIZE = 128       # 阶段三：精确评分的帧长

        # --- 交叉部分：融合父代A的“全局哈希过滤”思想与父代B的“阶段化剪枝”结构 ---
        # 本协议的整体架构采用了“过滤-验证-评分”的三阶段漏斗模型，是对父代思想的有机结合。
        # 阶段一：全局候选过滤 (源自父代A: g5_2)
        # 我们采纳了父代A的核心优势，即使用一次大规模的哈希广播（基于价格），快速、高效地生成一个
        # 包含所有潜在众数的初步候选集。这个方法相比父代B的区间预检，能以更低的通信开销（仅一个frame）
        # 获得一个更精细的候选列表，为后续阶段打下良好基础。
        
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        # 读写器无法看到标签的price，只能看到聚合后的slot计数
        filtering_slots = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTER_FRAME_SIZE
            filtering_slots[slot_id] += 1
        # --- [物理层模拟结束] ---

        total_tag_bits_sent += FILTER_FRAME_SIZE * 1

        active_slot_indices = {i for i, count in enumerate(filtering_slots) if count > 0}
        
        preliminary_candidates = []
        if not active_slot_indices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No tags responded in the initial filter"}

        for p in range(price_min, price_max + 1):
            if simple_hash(p, filter_seed) % FILTER_FRAME_SIZE in active_slot_indices:
                preliminary_candidates.append(p)
        
        if not preliminary_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No preliminary candidates found"}


        # --- 突变部分：引入“捆绑候选者验证 (Bundled Candidate Verification)”全新机制 ---
        # 两个父代协议要么是在初始阶段对整个价格范围进行粗粒度分箱（父代B），要么是在初步筛选后对
        # 候选者进行分箱存在性检查（父代A）。本协议引入一个全新的中间阶段，其核心思想是“二次聚焦过滤”。
        # 在第一阶段的高效全局过滤之后，我们获得了可能因哈希碰撞而包含大量“伪候选者”的初步列表。
        # 为了不逐个对它们进行昂贵的精确评分，我们引入此“捆绑验证”阶段。我们将初步候选者按价格范围
        # “捆绑”到不同的验证箱中，然后对每个“非空”的箱子进行一次性的、基于ID哈希的存在性查询。
        # 这种方法可以用极小的代价（一个很小的frame）一次性地排除掉一整个价格区间内的所有伪候选者。
        # 这是一个在父代中均未出现过的、作为中间步骤的、专门用于清理初步候选集的创新设计，
        # 它在父代A的“全局过滤”和父代B的“精确评分”之间建立了一个高效的桥梁。

        verified_candidates = []
        bin_size = math.ceil((price_max - price_min + 1) / VERIFICATION_BINS)
        
        candidate_bins = [[] for _ in range(VERIFICATION_BINS)]
        for p in preliminary_candidates:
            bin_index = (p - price_min) // bin_size
            # 确保索引在范围内
            if 0 <= bin_index < VERIFICATION_BINS:
                candidate_bins[bin_index].append(p)

        for i in range(VERIFICATION_BINS):
            if not candidate_bins[i]:
                continue
            
            min_p_in_bin = price_min + i * bin_size
            max_p_in_bin = min(min_p_in_bin + bin_size - 1, price_max)

            total_frames += 1
            verify_seed = generate_random_seed()
            total_bits_sent_by_reader += (verify_seed.bit_length() + VERIFICATION_FRAME_SIZE.bit_length() + 
                                          min_p_in_bin.bit_length() + max_p_in_bin.bit_length())
            
            # --- [物理层模拟] ---
            verification_slots = [0] * VERIFICATION_FRAME_SIZE
            for t in tags:
                if min_p_in_bin <= t.price <= max_p_in_bin:
                    slot_id = simple_hash(t.id, verify_seed) % VERIFICATION_FRAME_SIZE
                    verification_slots[slot_id] += 1
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += VERIFICATION_FRAME_SIZE * 1

            if any(count > 0 for count in verification_slots):
                verified_candidates.extend(candidate_bins[i])

        if not verified_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No verified candidates after bundled verification"}
        
        # --- 交叉部分：最终评分阶段采用父代协议共同认可的“单价格精确评分”及“非空时隙数”指标 ---
        # 阶段三：精确评分 (源自父代A和B的共同思想)
        # 对于通过了“全局过滤”和“捆绑验证”两层漏斗的幸存候选者，我们借鉴了两个父代协议最终都采用的
        # 稳健策略：为每个候选价格独立进行一次通信。并且，我们使用“非空时隙数”作为最终评分标准，
        # 这一指标被证明是估计标签数量的可靠方法，在父代A和B中都得到了体现。
        # 这种“逐一精确画像”的收尾方式确保了最终结果的准确性。
        final_scores = {}
        for price in verified_candidates:
            total_frames += 1
            scoring_seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + SCORING_FRAME_SIZE.bit_length() + scoring_seed.bit_length())

            # --- [物理层模拟] ---
            scoring_slots = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, scoring_seed) % SCORING_FRAME_SIZE
                    scoring_slots[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1
            
            num_non_empty_slots = sum(1 for count in scoring_slots if count > 0)
            if num_non_empty_slots > 0:
                final_scores[price] = num_non_empty_slots

        if not final_scores:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores calculated in final stage"}

        # --- 最终处理与时间计算 ---
        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "preliminary_candidate_count": len(preliminary_candidates),
            "verified_candidate_count": len(verified_candidates),
            "final_scores": final_scores
        }
        
        return modes, max_score, total_time_ms, verification_data