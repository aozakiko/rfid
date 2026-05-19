import math
import statistics
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g6_3(IProtocol):
    """
    子代协议: 自适应密度过滤协议 (Adaptive Density Filter Protocol)

    本协议是一个为高效、准确地解决大规模RFID众数查询问题而设计的全新子代协议。
    它严格遵守信息隔离原则，深度融合了两个父代协议的核心设计哲学，并引入了一项
    关于动态资源分配的全新突变机制。

    协议流程:
    1. 全局过滤 (源自父代B): 使用一次基于价格哈希的大规模通信，快速筛选出初步候选价格集。
    2. 自适应评分 (突变+交叉): 协议“感知”候选集的密度。如果候选者稀疏，使用更精确的
       大帧进行评分；如果候选者密集，则使用节省时间的的小帧快速评分。评分方法本身
       (非空时隙数)是两个父代共有的思想。
    3. 统计剪枝 (源自父代A): 利用统计学原理（均值和标准差）动态剔除评分较低的候选者，
       智能地缩小验证范围。
    4. 精确验证 (源自父代A): 对最终入围的候选者，使用高精度的对数估计法进行最终计数，
       并确定众数。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTER_FRAME_SIZE = 1024       # 阶段1：全局过滤帧大小
        BASE_SCORING_FRAME_SIZE = 32   # 阶段2：评分阶段的基础帧大小
        DENSITY_THRESHOLD = 50         # 阶段2：判断候选集“拥挤”程度的阈值
        FRAME_ADJUSTMENT_FACTOR = 2    # 阶段2：稀疏候选集下，帧大小的放大倍数
        STATISTICAL_PRUNING_FACTOR = 1.2 # 阶段3：统计剪枝的标准差倍数
        VERIFICATION_FRAME_SCALE_FACTOR = 1.8 # 阶段4：验证阶段帧大小的缩放因子
        MIN_VERIFICATION_FRAME_SIZE = 128   # 阶段4：验证阶段的最小帧大小

        # =================================================================================
        # 阶段 1: 全局候选价格过滤 (Global Candidate Filtering)
        # --- 交叉部分：融合父代B的“全局价格哈希过滤”思想 ---
        # 我们首先采用父代B（g4_1）中最高效的初步筛选机制。读写器不直接扫描价格，而是
        # 让所有标签根据自身的价格（price）在一个共享的大帧（frame）中进行哈希。
        # 读写器只需分析这个帧，就能反推出哪些价格“可能”存在，从而将后续需要处理的
        # 价格数量从整个价格范围（1-1000）急剧减少到一个很小的候选集。
        # =================================================================================
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        filtering_slots = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTER_FRAME_SIZE
            filtering_slots[slot_id] += 1
        # --- [物理层模拟结束] ---

        total_tag_bits_sent += FILTER_FRAME_SIZE * 1

        active_slot_indices = {i for i, count in enumerate(filtering_slots) if count > 0}
        
        preliminary_candidates = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTER_FRAME_SIZE in active_slot_indices:
                    preliminary_candidates.append(p)
        
        if not preliminary_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No preliminary candidates found"}

        # =================================================================================
        # 阶段 2: 自适应启发式评分 (Adaptive Heuristic Scoring)
        # --- 突变部分：引入“基于候选集密度的动态资源分配”全新机制 ---
        # 这是本协议的核心创新，在两个父代中均未出现。在对候选者进行评分前，协议会
        # 首先评估候选集的“拥挤”程度（即候选者数量）。
        # - 如果候选者数量较少（稀疏），说明问题相对简单，协议会分配更多的通信资源
        # （更大的帧），以获得更准确的初始分数，为后续的统计剪枝提供更高质量的数据。
        # - 如果候选者数量很多（密集），说明问题复杂，协议则会使用较小的帧进行快速评分，
        # 优先保证速度，避免在评分阶段花费过多时间。
        # 这种自适应机制使得协议能够智能地在不同数据分布下平衡开销与精度。
        # --- 交叉部分：采用父代A和B共有的“非空时隙数”作为评分指标 ---
        # 评分的启发式方法本身（计算非空时隙数）是两个父代协议都采用的经典、鲁棒的策略。
        # =================================================================================
        
        # --- [突变逻辑开始] ---
        if len(preliminary_candidates) < DENSITY_THRESHOLD:
            current_scoring_frame_size = int(BASE_SCORING_FRAME_SIZE * FRAME_ADJUSTMENT_FACTOR)
        else:
            current_scoring_frame_size = BASE_SCORING_FRAME_SIZE
        # --- [突变逻辑结束] ---

        price_to_score_map = {}
        for price in preliminary_candidates:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + current_scoring_frame_size.bit_length() + seed.bit_length()

            # --- [物理层模拟] ---
            slot_states = [0] * current_scoring_frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % current_scoring_frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += current_scoring_frame_size * 1

            num_non_empty_slots = sum(1 for count in slot_states if count > 0)
            if num_non_empty_slots > 0:
                price_to_score_map[price] = num_non_empty_slots

        # =================================================================================
        # 阶段 3: 基于统计的动态剪枝 (Statistics-Based Dynamic Pruning)
        # --- 交叉部分：继承父代A的“动态候选集筛选”思想 ---
        # 此阶段完全继承了父代A（g5_5）的智能剪枝逻辑。我们不使用固定的阈值，而是根据
        # 当前所有候选者分数的数据分布（均值和标准差），动态计算出一个淘汰线。只有
        # 那些分数显著优于“平均水平”的候选者才能进入最终的精确验证阶段。这使得协议
        # 的筛选能力可以根据数据的实际情况自适应调整，非常高效。
        # =================================================================================
        if not price_to_score_map:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores generated"}

        all_scores = list(price_to_score_map.values())
        candidates_for_verification = []
        
        if len(all_scores) > 1:
            mean_score = statistics.mean(all_scores)
            stdev_score = statistics.stdev(all_scores)
            score_threshold = mean_score + STATISTICAL_PRUNING_FACTOR * stdev_score
            candidates_for_verification = [p for p, s in price_to_score_map.items() if s >= score_threshold]
        
        if not candidates_for_verification:
            max_score = max(all_scores) if all_scores else 0
            candidates_for_verification = [p for p, s in price_to_score_map.items() if s == max_score]

        candidates_for_verification.sort(key=lambda p: price_to_score_map[p], reverse=True)

        # =================================================================================
        # 阶段 4: 精确验证 (Precise Verification)
        # --- 交叉部分：采用父代A的“对数精确估计”方法 ---
        # 对于通过了层层筛选的最终候选者，我们采用父代A（g5_5）的精确计数方法。
        # 该方法使用一个根据初始分数自适应调整大小的帧，并通过对数公式 
        # (-F * log(空槽位数 / 总槽位数)) 来高精度地估计每个价格的标签数量。
        # 这种方法比单纯比较非空时隙数更为准确，是确定最终胜利者的可靠保证。
        # =================================================================================
        final_counts = {}
        max_verified_count = -1.0
        modes = []
        
        for price in candidates_for_verification:
            frame_size = max(MIN_VERIFICATION_FRAME_SIZE, int(price_to_score_map[price] * VERIFICATION_FRAME_SCALE_FACTOR))
            
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + frame_size.bit_length() + seed.bit_length()
            
            # --- [物理层模拟] ---
            slot_states = [0] * frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += frame_size * 1

            num_zeros = slot_states.count(0)
            verified_count = 0
            if num_zeros == frame_size:
                verified_count = 0
            elif num_zeros == 0:
                # 帧全满，使用一个合理的较大估计，避免log(0)
                verified_count = frame_size * 2.39 
            else:
                verified_count = -frame_size * math.log(num_zeros / frame_size)
            
            final_counts[price] = verified_count

            if verified_count > max_verified_count:
                max_verified_count = verified_count
                modes = [price]
            elif math.isclose(verified_count, max_verified_count, rel_tol=1e-5):
                modes.append(price)

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "preliminary_candidate_count": len(preliminary_candidates),
            "adaptive_scoring_frame_size": current_scoring_frame_size,
            "candidates_after_statistical_pruning": candidates_for_verification,
            "final_verified_counts": final_counts,
        }
        
        if not modes:
             return [], 0, total_time_ms, verification_data
        
        modes.sort()
        
        return modes, math.ceil(max_verified_count), total_time_ms, verification_data
