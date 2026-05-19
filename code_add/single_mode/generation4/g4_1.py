import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g4_1(IProtocol):
    """
    锦标赛式筛查众数查询协议 (Tournament Screening Protocol for Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并通过分层过滤与锦标赛式评分，有机地融合了两个父代协议的核心思想，
    并引入了全新的多轮次精确打击策略以提升效率和准确性。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 交叉阶段1 (源于父代B)
        CANDIDATE_FILTER_FRAME_SIZE = 1024
        # 交叉阶段2 (源于父代A)
        PRUNING_BINS = 10
        PRUNING_FRAME_SIZE = 64
        # 突变阶段
        SEEDING_PASS_FRAME_SIZE = 64
        CHAMPIONSHIP_PASS_FRAME_SIZE = 512

        # --- 交叉部分：融合父代B的“全局槽位过滤”思想与父代A的“范围剪枝”思想 ---
        # 本协议首先采用父代B（g2_5）高效的“全局槽位过滤”机制。通过一次性对整个价格空间进行哈希映射，
        # 快速生成一个包含潜在众数的候选价格列表。这一步极大地减少了需要单独探测的价格数量。
        # 随后，我们融合了父代A（g2_1）的“分箱剪枝”思想，但将其创新性地用作一个“验证”阶段。
        # 我们将候选价格分组到几个大区间（bins）中，并对每个包含候选者的区间进行一次存在性检查。
        # 如果某个区间检查结果为空，则该区间内所有的候选价格都会被视为第一阶段哈希冲突产生的伪影而被剔除。
        # 这种“过滤+验证”的策略结合了父代B的速度和父代A的鲁棒性。

        # 阶段一：全局候选价格生成 (源自父代B)
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + CANDIDATE_FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        filtering_slots = [0] * CANDIDATE_FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % CANDIDATE_FILTER_FRAME_SIZE
            filtering_slots[slot_id] += 1
        # --- [物理层模拟结束] ---

        total_tag_bits_sent += CANDIDATE_FILTER_FRAME_SIZE * 1

        active_slot_indices = {i for i, count in enumerate(filtering_slots) if count > 0}
        
        preliminary_candidates = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % CANDIDATE_FILTER_FRAME_SIZE in active_slot_indices:
                    preliminary_candidates.append(p)
        
        # 阶段二：分箱验证与剪枝 (源自父代A)
        verified_candidates = []
        if preliminary_candidates:
            price_range = price_max - price_min + 1
            bin_size = math.ceil(price_range / PRUNING_BINS)
            
            candidate_bins = [[] for _ in range(PRUNING_BINS)]
            for p in preliminary_candidates:
                bin_index = (p - price_min) // bin_size
                candidate_bins[bin_index].append(p)

            for i in range(PRUNING_BINS):
                if not candidate_bins[i]:
                    continue
                
                min_p_in_bin = price_min + i * bin_size
                max_p_in_bin = min(min_p_in_bin + bin_size - 1, price_max)

                total_frames += 1
                pruning_seed = generate_random_seed()
                total_bits_sent_by_reader += (pruning_seed.bit_length() + PRUNING_FRAME_SIZE.bit_length() + 
                                              min_p_in_bin.bit_length() + max_p_in_bin.bit_length())
                
                # --- [物理层模拟] ---
                pruning_slots = [0] * PRUNING_FRAME_SIZE
                for t in tags:
                    if min_p_in_bin <= t.price <= max_p_in_bin:
                        slot_id = simple_hash(t.id, pruning_seed) % PRUNING_FRAME_SIZE
                        pruning_slots[slot_id] += 1
                # --- [物理层模拟结束] ---

                total_tag_bits_sent += PRUNING_FRAME_SIZE * 1

                if any(count > 0 for count in pruning_slots):
                    verified_candidates.extend(candidate_bins[i])

        if not verified_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No verified candidates after pruning"}

        # --- 突变部分：引入“锦标赛制”分层评分全新机制 ---
        # 两个父代协议在评分阶段要么是迭代调整帧长（父代A），要么是基于冲突率被动地进行二次探测（父代B）。
        # 本协议引入一个全新的、主动的分层评分机制，类似体育比赛的“锦标赛”。
        # 1. 预选赛 (Seeding Pass): 所有通过验证的候选价格，首先使用一个较小的、通信成本低的帧进行快速评分。
        #    此举旨在用最小的开销得出一个大致的排名。
        # 2. 决赛 (Championship Pass): 协议识别出在预选赛中得分最高的“决赛选手”（例如，所有获得最高预选分的价格）。
        #    然后，协议只对这些最有希望的“决赛选手”使用一个非常大的、高精度的帧进行最终评分，从而决出胜负。
        # 这种“锦标赛”策略将宝贵的高精度通信资源集中用于最关键的候选者，避免了资源浪费，
        # 在保证极高准确率的同时，显著提升了协议的整体效率。

        # 预选赛: 使用小帧长对所有候选者进行评分
        seeding_scores = {}
        for price in verified_candidates:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + SEEDING_PASS_FRAME_SIZE.bit_length() + seed.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * SEEDING_PASS_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SEEDING_PASS_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += SEEDING_PASS_FRAME_SIZE * 1
            seeding_scores[price] = sum(1 for count in slot_states if count > 0)

        if not seeding_scores:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores in seeding pass"}

        # 识别进入决赛的选手
        max_seeding_score = max(seeding_scores.values())
        finalists = [price for price, score in seeding_scores.items() if score == max_seeding_score]

        # 决赛: 使用大帧长对决赛选手进行精确评分
        final_scores = {}
        for price in finalists:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + CHAMPIONSHIP_PASS_FRAME_SIZE.bit_length() + seed.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * CHAMPIONSHIP_PASS_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % CHAMPIONSHIP_PASS_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += CHAMPIONSHIP_PASS_FRAME_SIZE * 1
            final_scores[price] = sum(1 for count in slot_states if count > 0)
        
        # 综合决赛结果，如果决赛无结果则使用预选赛结果
        if not final_scores:
            max_score = max_seeding_score
            modes = sorted(finalists)
        else:
            max_score = max(final_scores.values())
            modes = sorted([p for p, s in final_scores.items() if s == max_score])

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "preliminary_candidate_count": len(preliminary_candidates),
            "verified_candidate_count": len(verified_candidates),
            "finalist_count": len(finalists),
            "finalists": finalists,
            "final_scores": final_scores
        }
        
        return modes, max_score, total_time_ms, verification_data