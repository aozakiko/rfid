import math
import statistics
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g8_4(IProtocol):
    """
    混合式自适应精炼众数查询协议 (Hybrid Adaptive Refinement Mode Query Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，并通过一个创新的多阶段框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“自适应过滤增强”机制，以实现更优的性能和准确率。

    协议流程:
    1.  自适应双哈希过滤 (Adaptive Dual-Hash Filtering):
        - 交叉: 继承父代A (g7_5) 的“双哈希过滤”框架，通过两次独立的哈希探测来生成高质量的候选集。
        - 突变: 引入全新的“自适应过滤增强”机制。在两次哈希过滤之间，读写器会分析第一次过滤的
          结果。如果时隙占用率过高（意味着潜在的哈希冲突严重），则动态增大第二次过滤的帧大小，
          以提高过滤的精度，生成一个更纯净的初始候选集。

    2.  统计剪枝 (Statistical Pruning):
        - 交叉: 继承父代B (g6_3) 的“基于统计的动态剪枝”思想。在对候选集进行初步评分后，
          协议会利用均值和标准差动态计算出一个淘汰阈值，智能地剔除表现不佳的候选价格，
          显著缩小需要进一步验证的范围。

    3.  对数精确认证 (Logarithmic Verification):
        - 交叉: 继承父代B (g6_3) 的“对数精确估计”方法。对通过了层层筛选的最终候选者，
          使用高精度的对数公式来估计其真实数量，确保最终结果的准确性。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        """
        执行协议的核心逻辑。
        """
        # --- 时间计算与统计变量 ---
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTER_FRAME_SIZE_1 = 1024       # 第一次过滤的基础帧大小
        FILTER_FRAME_SIZE_2_BASE = 1024  # 第二次过滤的基础帧大小
        FILTER_FRAME_SIZE_2_BOOST = 2048 # 第二次过滤的增强帧大小
        UTILIZATION_THRESHOLD = 0.4      # 触发第二次过滤帧大小增强的占用率阈值
        SCORING_FRAME_SIZE = 64          # 初步评分阶段的帧大小
        PRUNING_FACTOR = 1.0             # 统计剪枝的标准差倍数
        VERIFICATION_FRAME_FACTOR = 2.0  # 最终验证阶段帧大小的缩放因子
        MIN_VERIFICATION_FRAME_SIZE = 128# 最终验证阶段的最小帧大小

        # =================================================================================
        # 阶段 1: 自适应双哈希过滤 (Adaptive Dual-Hash Filtering)
        # =================================================================================

        # --- 交叉部分：融合父代A的“双哈希过滤”思想，以实现高精度的候选集生成 ---
        # --- 突变部分：引入“自适应过滤增强”全新机制，根据第一次过滤的反馈动态调整第二次过滤的资源 ---

        # --- 第一次哈希过滤 ---
        total_frames += 1
        filter_seed_1 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_1.bit_length() + FILTER_FRAME_SIZE_1.bit_length()
        
        # [物理层模拟]
        filtering_slots_1 = [0] * FILTER_FRAME_SIZE_1
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_1) % FILTER_FRAME_SIZE_1
            filtering_slots_1[slot_id] = 1
        # [物理层模拟结束]
        
        total_tag_bits_sent += FILTER_FRAME_SIZE_1 * 1
        active_slot_indices_1 = {i for i, state in enumerate(filtering_slots_1) if state > 0}

        # --- [突变逻辑开始] ---
        # 读写器分析第一次过滤的时隙占用率。
        slot_utilization_rate = len(active_slot_indices_1) / FILTER_FRAME_SIZE_1
        
        # 如果占用率过高，则动态增大第二次过滤的帧大小以减少冲突。
        filter_frame_size_2_actual = FILTER_FRAME_SIZE_2_BOOST \
            if slot_utilization_rate > UTILIZATION_THRESHOLD else FILTER_FRAME_SIZE_2_BASE
        # --- [突变逻辑结束] ---

        # --- 第二次哈希过滤 ---
        total_frames += 1
        filter_seed_2 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_2.bit_length() + filter_frame_size_2_actual.bit_length()
        
        # [物理层模拟]
        filtering_slots_2 = [0] * filter_frame_size_2_actual
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_2) % filter_frame_size_2_actual
            filtering_slots_2[slot_id] = 1
        # [物理层模拟结束]

        total_tag_bits_sent += filter_frame_size_2_actual * 1
        active_slot_indices_2 = {i for i, state in enumerate(filtering_slots_2) if state > 0}

        # --- 读写器本地生成最终候选集 ---
        candidate_prices = []
        if active_slot_indices_1 and active_slot_indices_2:
            for p in range(price_min, price_max + 1):
                hash1_in_set = (simple_hash(p, filter_seed_1) % FILTER_FRAME_SIZE_1) in active_slot_indices_1
                hash2_in_set = (simple_hash(p, filter_seed_2) % filter_frame_size_2_actual) in active_slot_indices_2
                if hash1_in_set and hash2_in_set:
                    candidate_prices.append(p)
        
        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after filtering"}

        # =================================================================================
        # 阶段 2: 初步评分与统计剪枝 (Scoring and Statistical Pruning)
        # =================================================================================
        
        # --- 对候选价格进行初步评分 ---
        scores = {}
        for price in candidate_prices:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + SCORING_FRAME_SIZE.bit_length() + seed.bit_length()

            # [物理层模拟]
            slot_states = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SCORING_FRAME_SIZE
                    slot_states[slot_id] += 1
            # [物理层模拟结束]
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1
            num_non_empty = sum(1 for count in slot_states if count > 0)
            if num_non_empty > 0:
                scores[price] = num_non_empty

        if not scores:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores generated"}
            
        # --- 交叉部分：融合父代B的“基于统计的动态剪枝”思想，智能缩小验证范围 ---
        all_scores = list(scores.values())
        finalists = []
        if len(all_scores) > 1:
            mean_score = statistics.mean(all_scores)
            stdev_score = statistics.stdev(all_scores) if len(all_scores) > 1 else 0
            score_threshold = mean_score + PRUNING_FACTOR * stdev_score
            finalists = [p for p, s in scores.items() if s >= score_threshold]

        # 如果统计剪枝后没有候选者，则保留原始得分最高的
        if not finalists:
            max_score = max(all_scores)
            finalists = [p for p, s in scores.items() if s == max_score]

        # =================================================================================
        # 阶段 3: 对数精确认证 (Logarithmic Verification)
        # =================================================================================
        
        # --- 交叉部分：融合父代B的“对数精确估计”方法，确保最终结果的准确性 ---
        final_counts = {}
        for price in finalists:
            # 根据初步评分动态调整验证帧大小
            frame_size = max(MIN_VERIFICATION_FRAME_SIZE, int(scores[price] * VERIFICATION_FRAME_FACTOR))
            
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + frame_size.bit_length() + seed.bit_length()
            
            # [物理层模拟]
            slot_states = [0] * frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    slot_states[slot_id] += 1
            # [物理层模拟结束]
            
            total_tag_bits_sent += frame_size * 1
            num_zeros = slot_states.count(0)
            
            estimated_count = 0
            if num_zeros < frame_size:
                # 避免log(0)错误
                if num_zeros == 0:
                    estimated_count = frame_size * 2.39 # 使用一个较大的估计值
                else:
                    estimated_count = -frame_size * math.log(num_zeros / frame_size)
            final_counts[price] = estimated_count

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Fused A's dual-hash filter with B's statistical pruning and log verification.",
            "mutation_info": "Introduced adaptive frame sizing in the second filter based on the first filter's slot utilization.",
            "initial_candidates": len(candidate_prices),
            "adaptive_filter_2_size": filter_frame_size_2_actual,
            "finalists_after_pruning": len(finalists),
            "final_counts": final_counts
        }

        if not final_counts:
            return [], 0, total_time_ms, verification_data

        max_count = max(final_counts.values())
        if max_count == 0:
             return [], 0, total_time_ms, verification_data
        
        modes = sorted([p for p, c in final_counts.items() if math.isclose(c, max_count, rel_tol=1e-5)])
        
        return modes, math.ceil(max_count), total_time_ms, verification_data