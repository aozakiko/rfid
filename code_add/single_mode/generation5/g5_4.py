import math
import collections
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g5_4(IProtocol):
    """
    子代协议: 混合自适应分箱众数查询 (Hybrid Adaptive Binning Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，有机地融合了两个父代协议的核心设计思想，并引入了一项创新的“自适应分箱”优化机制。
    协议通过一个多阶段、由粗到精的筛选过程，逐步锁定并最终验证价格众数。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        INITIAL_BINS = 16              # 初始分箱数量 (受父代B启发)
        BINNING_FRAME_SIZE = 128       # 分箱探测阶段的帧长
        REFINEMENT_THRESHOLD = 0.8     # 触发分箱细化的冲突率阈值 (突变部分)
        MIN_PRICES_TO_REFINE = 10      # 触发分箱细化的最小价格范围 (突变部分)
        SCORING_FRAME_SIZE = 64        # 启发式评分阶段的帧长 (受父代A和B启发)
        VERIFICATION_SCALE_FACTOR = 2.0# 验证阶段帧长的动态缩放因子 (受父代B启发)
        MIN_VERIFICATION_FRAME_SIZE = 128 # 验证阶段的最小帧长

        # =================================================================================
        # --- 交叉部分：融合父代B的“范围分箱”思想与父代A的“启发式评分”框架 ---
        # 协议的核心框架借鉴了父代的多阶段过滤思想。
        # 阶段一，我们采用父代B的“范围分箱”策略，将整个价格空间划分为多个区间，
        # 快速排除无标签的广阔区域，而不是像父代A那样需要检查所有可能的价格。
        # 阶段三，我们采用父代A和B共有的“启发式评分”思想，使用非空时隙数作为指标，
        # 对候选价格进行快速排序。
        # =================================================================================

        # --- 突变部分：引入“基于冲突感知的自适应分箱细化”全新机制 ---
        # 在父代协议的静态分箱或全范围扫描基础上，本协议引入了一个动态的“下钻”机制。
        # 在初始分箱探测后，如果发现某个分箱（价格区间）不仅存在标签，而且冲突率极高
        # （表明内部标签数量密集），同时该分箱覆盖的价格范围也较大，协议不会立即将
        # 其中所有价格都视为候选，而是会将这个“高密度”的宽泛分箱进一步分裂成两个
        # 更小的子分箱，并对它们进行递归探测。这种自适应的细化过程能够将探测资源
        # 智能地集中到最有可能包含众数的价格“热点区域”，从而以更低的成本生成一个
        # 高度精炼的候选价格列表，这是两个父代协议都不具备的能力。
        # =================================================================================

        # --- 阶段 1 & 2: 自适应分箱与候选集生成 (Adaptive Binning & Candidate Generation) ---
        candidate_prices = set()
        
        # 初始化处理队列 (Mutation)
        bins_to_process = collections.deque()
        price_range = price_max - price_min + 1
        
        # 初始分箱 (Crossover from Parent B's concept)
        bin_size = math.ceil(price_range / INITIAL_BINS)
        for i in range(INITIAL_BINS):
            start = price_min + i * bin_size
            end = min(start + bin_size - 1, price_max)
            if start <= end:
                bins_to_process.append((start, end))

        # 循环处理，直到所有分箱都被细化或接纳 (Mutation core loop)
        while bins_to_process:
            p_start, p_end = bins_to_process.popleft()
            
            # --- 读写器行为: 广播对价格区间 [p_start, p_end] 的探测指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + BINNING_FRAME_SIZE.bit_length() + p_start.bit_length() + p_end.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * BINNING_FRAME_SIZE
            is_active_in_bin = False
            for t in tags:
                if p_start <= t.price <= p_end:
                    is_active_in_bin = True
                    slot_id = simple_hash(t.id, seed) % BINNING_FRAME_SIZE
                    slot_states[slot_id] = 1 # 仅关心是否非空
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += BINNING_FRAME_SIZE * 1

            if is_active_in_bin:
                num_non_empty = sum(slot_states)
                collision_ratio = num_non_empty / BINNING_FRAME_SIZE
                num_prices_in_bin = p_end - p_start + 1

                # 突变核心逻辑: 判断是否需要细化
                if collision_ratio >= REFINEMENT_THRESHOLD and num_prices_in_bin > MIN_PRICES_TO_REFINE:
                    mid_point = p_start + num_prices_in_bin // 2
                    bins_to_process.append((p_start, mid_point - 1))
                    bins_to_process.append((mid_point, p_end))
                else:
                    # 如果不需要细化，则将该区间所有价格加入候选集
                    candidate_prices.update(range(p_start, p_end + 1))

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found after binning"}

        # --- 阶段 3: 启发式评分 (Heuristic Scoring) ---
        price_to_score_map = {}
        sorted_candidates = sorted(list(candidate_prices))

        for price in sorted_candidates:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + SCORING_FRAME_SIZE.bit_length() + seed.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SCORING_FRAME_SIZE
                    slot_states[slot_id] = 1
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += SCORING_FRAME_SIZE * 1
            
            num_non_empty = sum(slot_states)
            if num_non_empty > 0:
                price_to_score_map[price] = num_non_empty
        
        if not price_to_score_map:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores generated for candidates"}

        # --- 阶段 4: 最终验证 (Final Verification) ---
        max_score = max(price_to_score_map.values())
        top_candidates = [p for p, s in price_to_score_map.items() if s == max_score]

        final_counts = {}
        max_verified_count = -1.0
        modes = []

        for price in top_candidates:
            # 借鉴父代B的思想，根据启发式分数自适应调整帧长
            frame_size = max(MIN_VERIFICATION_FRAME_SIZE, int(price_to_score_map[price] * VERIFICATION_SCALE_FACTOR))
            
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + frame_size.bit_length() + seed.bit_length())
            
            # --- [物理层模拟] ---
            slot_states = [0] * frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += frame_size * 1
            
            # 使用标准的对数估计算法进行精确计数
            num_zeros = slot_states.count(0)
            verified_count = 0
            if num_zeros < frame_size and num_zeros > 0:
                verified_count = -frame_size * math.log(num_zeros / frame_size)
            elif num_zeros == 0: # 帧全满，给一个较大的估计值
                verified_count = frame_size * 2.5 
            
            final_counts[price] = verified_count
            
            if verified_count > max_verified_count:
                max_verified_count = verified_count
                modes = [price]
            elif math.isclose(verified_count, max_verified_count):
                modes.append(price)

        # --- 时间计算与结果返回 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        verification_data = {
            "candidates_after_binning": len(candidate_prices),
            "heuristic_scores": price_to_score_map,
            "top_candidates_for_verification": top_candidates,
            "final_verified_counts": final_counts,
        }
        
        modes.sort()
        
        return modes, math.ceil(max_verified_count) if max_verified_count > 0 else 0, total_time_ms, verification_data
