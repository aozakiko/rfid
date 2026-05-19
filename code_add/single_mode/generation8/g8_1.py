import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g8_1(IProtocol):
    """
    混合剪枝与显性候选众数查询协议 (Hybrid Pruning & Dominant Candidate Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，通过一个多阶段的过滤与评分框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“显性候选提前终止”机制，以在保证高准确率的同时，显著优化在数据分布不均时的通信开销。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        # --- 协议超参数 ---
        # 交叉部分参数 (Inspired by Parents)
        PRECHECK_BINS = 16  # 范围剪枝阶段的分箱数 (g2_1)
        PRECHECK_FRAME_SIZE = 128  # 范围剪枝阶段的帧大小 (g2_1)
        PROGRESSIVE_SCORING_START_FRAME = 32 # 渐进式评分初始帧 (g7_3)
        PROGRESSIVE_SCORING_MAX_ITER = 4 # 渐进式评分最大迭代次数 (g7_3)
        SATURATION_THRESHOLD = 0.95 # 帧饱和度阈值 (g7_3)
        TIEBREAKER_FRAME_SIZE = 4096 # 决胜局帧大小 (g7_3)
        
        # 突变部分参数 (Newly introduced)
        EARLY_EXIT_CONFIDENCE_FACTOR = 2.0 # 显性候选判定因子 (全新)

        # --- 交叉部分：融合父代A(g2_1)的“范围分箱剪枝”思想与父代B(g7_3)的“渐进式锦标赛”思想 ---
        # 本协议首先采纳了父代A(g2_1)中确定性高、效率出色的“范围分箱剪枝”策略。
        # 与父代B(g7_3)概率性的全局哈希过滤相比，这种方法能更稳定、更彻底地排除大量不存在任何标签的价格区间，
        # 为后续的精确查询提供一个干净、高质量的候选集。
        # 随后，对于筛选出的候选价格，我们放弃了父代A的逐一线性评分方式，
        # 转而采用父代B(g7_3)中更为精细和高效的“渐进式评分”和“决胜局”机制。
        # 这种结合使得协议既拥有了前期大规模剪枝的效率，又具备了后期对潜在众数进行集中资源、精确打击的能力。

        # 阶段一：范围分箱剪枝 (源自父代A: g2_1)
        candidate_prices = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        
        for i in range(PRECHECK_BINS):
            p_min = price_min + i * bin_size
            p_max = min(p_min + bin_size - 1, price_max)
            if p_min > price_max:
                break
            
            # --- 读写器行为: 广播指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() + 
                                          p_min.bit_length() + p_max.bit_length())
            
            # --- [物理层模拟] ---
            slot_states = [0] * PRECHECK_FRAME_SIZE
            has_response = False
            for t in tags:
                if p_min <= t.price <= p_max:
                    has_response = True
                    break # 仅需确认区间内有无标签，无需完整模拟
            # --- [物理层模拟结束] ---
            total_slots += PRECHECK_FRAME_SIZE

            # --- 读写器行为: 分析帧 ---
            # 在实际物理层中，读写器会接收到一个非空信号。这里我们直接用`has_response`模拟。
            if has_response:
                for p in range(p_min, p_max + 1):
                    candidate_prices.add(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = max(0, (total_frames - 1) * 30)
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidate prices found after pruning"}

        # 阶段二：渐进式评分 (源自父代B: g7_3)
        progressive_scores = {}
        for price in sorted(list(candidate_prices)):
            current_frame_size = PROGRESSIVE_SCORING_START_FRAME
            score = 0
            
            for _ in range(PROGRESSIVE_SCORING_MAX_ITER):
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + current_frame_size.bit_length() + seed.bit_length())

                # --- [物理层模拟] ---
                slot_states = [0] * current_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % current_frame_size
                        slot_states[slot_id] = 1 # 只关心时隙是否非空
                # --- [物理层模拟结束] ---
                total_slots += current_frame_size
                
                score = sum(slot_states)
                
                # 如果帧未饱和，说明分数可信，跳出迭代
                if score < current_frame_size * SATURATION_THRESHOLD:
                    break
                
                # 否则，帧可能因碰撞而饱和，加倍帧长重试
                current_frame_size *= 2
            
            progressive_scores[price] = score
        
        if not progressive_scores:
             T_reader = total_bits_sent_by_reader * 0.025
             T_tag = total_slots * 1 * 0.4
             T_overhead = max(0, (total_frames - 1) * 30)
             return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores in progressive scoring"}

        # --- 突变部分：引入“显性候选提前终止”全新机制 ---
        # 两个父代协议在获得初步分数后，都需要进入下一轮更为昂贵的“决赛”或“精确评分”来确认最终胜者。
        # 本协议引入一个全新的判断逻辑：如果在渐进式评分后，发现得分最高的候选者（“冠军”）
        # 与得分次高的候选者（“亚军”）之间存在巨大的、压倒性的差距，
        # 我们就拥有了极高的置信度来判断“冠军”即为最终的众数。
        # 在这种情况下，协议将直接终止并返回结果，从而完全跳过后续所有为解决微小分数差异而设计的、
        # 代价高昂的“决胜局”阶段。这个机制使得协议在面对分布集中、众数明显的场景时，
        # 能够节约大量不必要的通信开销，极大地提升了效率。
        
        sorted_scores = sorted(progressive_scores.items(), key=lambda item: item[1], reverse=True)
        
        if len(sorted_scores) >= 2:
            top_score = sorted_scores[0][1]
            second_score = sorted_scores[1][1]
            # 如果最高分显著高于次高分 (例如，是其两倍以上)，则提前终止
            if top_score > 0 and top_score >= second_score * EARLY_EXIT_CONFIDENCE_FACTOR:
                max_score = top_score
                # 找出所有与最高分持平的候选者（尽管概率很小）
                modes = sorted([p for p, s in progressive_scores.items() if s == max_score])
                
                # 计算总时间并返回
                T_reader = total_bits_sent_by_reader * 0.025
                T_tag = total_slots * 1 * 0.4
                T_overhead = max(0, (total_frames - 1) * 30)
                total_time_ms = T_reader + T_tag + T_overhead
                
                verification_data = {
                    "mutation_info": f"Early exit triggered. Top score {top_score} vs second score {second_score}.",
                    "initial_candidate_count": len(candidate_prices),
                    "progressive_scores": progressive_scores,
                    "final_stage": "Skipped (Early Exit)"
                }
                return modes, max_score, total_time_ms, verification_data
        
        # 阶段三：决胜局 (Tiebreaker Pass, 源自父代B: g7_3)
        # 如果没有触发提前终止，说明候选者之间分数接近，需要进行最终的精确裁决。
        max_progressive_score = sorted_scores[0][1]
        finalists = [p for p, s in progressive_scores.items() if s == max_progressive_score]
        
        final_scores = {}
        # 如果只有一个最高分，或决胜局无意义，直接返回渐进式评分结果
        if len(finalists) == 1:
            final_scores = progressive_scores
        else:
            # 对所有并列第一的候选者进行一次大规模、高精度的决胜局探测
            for price in finalists:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + TIEBREAKER_FRAME_SIZE.bit_length() + seed.bit_length())
                
                # --- [物理层模拟] ---
                slot_states = [0] * TIEBREAKER_FRAME_SIZE
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % TIEBREAKER_FRAME_SIZE
                        slot_states[slot_id] = 1
                # --- [物理层模拟结束] ---
                total_slots += TIEBREAKER_FRAME_SIZE
                final_scores[price] = sum(slot_states)
        
        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 1 * 0.4
        T_overhead = max(0, (total_frames - 1) * 30)
        total_time_ms = T_reader + T_tag + T_overhead

        if not final_scores: # Should not happen if progressive_scores is not empty
             return [], 0, total_time_ms, {"reason": "Empty final scores"}

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])
        
        verification_data = {
            "mutation_info": "Early exit not triggered. Proceeded to final tiebreaker.",
            "initial_candidate_count": len(candidate_prices),
            "progressive_scores": progressive_scores,
            "finalists": finalists,
            "tiebreaker_scores": final_scores if len(finalists) > 1 else "Not needed"
        }
        
        return modes, max_score, total_time_ms, verification_data
