import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_4(IProtocol):
    """
    自适应分组评分众数查询协议 (Adaptive Grouped Scoring Protocol for Mode Query)

    本协议是一个为解决大规模RFID众数查询问题而设计的全新子代协议。它严格遵循信息隔离原则，
    其核心设计体现了对两个父代协议思想的深度融合（交叉），并在此基础上引入了一项全新的、
    基于候选者密度的自适应分组优化机制（突变），旨在以高准确率为首要目标，同时显著优化通信效率。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        # --- 协议超参数 ---
        # 阶段一：范围剪枝 (源自父代A和B的共同思想)
        PRECHECK_BINS = 16
        PRECHECK_FRAME_SIZE = 64

        # 阶段二/三：交叉与突变参数
        # 突变参数: 自适应分组的密度阈值和大小 (全新)
        DENSITY_THRESHOLD = (price_max - price_min + 1) * 0.25 # 当候选者数量超过总范围的25%时，视为高密度
        LARGE_GROUP_SIZE = 8  # 高密度（分散）时，使用大分组以减少帧开销
        SMALL_GROUP_SIZE = 4  # 低密度（集中）时，使用小分组以提高单次探测精度

        # 交叉参数 (融合自父代A和B)
        PROGRESSIVE_SCORING_START_FRAME = 32 # 渐进式评分的初始子帧大小 (源自父代A)
        PROGRESSIVE_SCORING_MAX_ITER = 5     # 最大迭代次数 (源自父代A)
        SATURATION_THRESHOLD = 0.9           # 帧饱和度阈值 (源自父代A)

        # --- 阶段一：范围分箱剪枝 (继承自两个父代共有的高效策略) ---
        candidate_prices: Set[int] = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)

        for i in range(PRECHECK_BINS):
            p_min = price_min + i * bin_size
            p_max = min(p_min + bin_size - 1, price_max)
            if p_min > price_max:
                break

            total_frames += 1
            # 读写器广播范围和帧参数
            total_bits_sent_by_reader += (p_min.bit_length() + p_max.bit_length() + PRECHECK_FRAME_SIZE.bit_length())

            # --- [物理层模拟] ---
            # 仅检查此价格区间内是否存在任何标签的响应
            slot_states = [0] * PRECHECK_FRAME_SIZE
            for t in tags:
                if p_min <= t.price <= p_max:
                    slot_id = simple_hash(t.id, i) % PRECHECK_FRAME_SIZE # 使用确定性种子
                    slot_states[slot_id] = 1
            has_response_in_bin = sum(slot_states) > 0
            # --- [物理层模拟结束] ---
            total_slots += PRECHECK_FRAME_SIZE

            if has_response_in_bin:
                for p in range(p_min, p_max + 1):
                    candidate_prices.add(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = max(0, (total_frames - 1) * 30)
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after range pruning"}

        contenders = sorted(list(candidate_prices))
        final_scores = {}

        # --- 突变部分：引入“基于候选者密度的自适应分组”全新机制 ---
        group_size = LARGE_GROUP_SIZE if len(contenders) > DENSITY_THRESHOLD else SMALL_GROUP_SIZE

        # --- 交叉部分：融合父代B的“分组探测”框架与父代A的“渐进式评分”逻辑 ---
        contender_groups = [contenders[i:i + group_size] for i in range(0, len(contenders), group_size)]

        for group in contender_groups:
            current_sub_frame_size = PROGRESSIVE_SCORING_START_FRAME
            group_scores = {}
            is_group_score_stable = False
            temp_scores = {p: 0 for p in group} # 确保temp_scores总是有定义的

            for _ in range(PROGRESSIVE_SCORING_MAX_ITER):
                total_frames += 1
                seed = generate_random_seed()
                combined_frame_size = len(group) * current_sub_frame_size

                # 读写器广播组内所有价格、种子和子帧大小
                bits_for_prices = sum(p.bit_length() for p in group)
                total_bits_sent_by_reader += (seed.bit_length() + current_sub_frame_size.bit_length() + bits_for_prices)

                # --- [物理层模拟] ---
                slot_states = [0] * combined_frame_size
                for t in tags:
                    if t.price in group:
                        price_index_in_group = group.index(t.price)
                        slot_offset = price_index_in_group * current_sub_frame_size
                        slot_id = slot_offset + (simple_hash(t.id, seed) % current_sub_frame_size)
                        slot_states[slot_id] = 1 # 仅关心时隙是否非空
                # --- [物理层模拟结束] ---
                total_slots += combined_frame_size

                # 读写器分析组合帧，并检查饱和情况
                any_sub_frame_saturated = False
                temp_scores = {}
                for i, price in enumerate(group):
                    sub_frame_start = i * current_sub_frame_size
                    sub_frame_end = sub_frame_start + current_sub_frame_size
                    score = sum(slot_states[sub_frame_start:sub_frame_end])
                    temp_scores[price] = score
                    if score >= current_sub_frame_size * SATURATION_THRESHOLD:
                        any_sub_frame_saturated = True
                        break # 只要组内有一个饱和，就需要为整个组增加帧长后重试

                if not any_sub_frame_saturated:
                    group_scores = temp_scores
                    is_group_score_stable = True
                    break # 所有子帧都未饱和，分数可信，完成本组探测

                # 如果饱和，则加倍子帧大小进行下一轮迭代
                current_sub_frame_size *= 2

            # 将本组最终获得的分数（无论是稳定还是迭代结束）存入最终结果
            if not is_group_score_stable: # 如果迭代结束仍饱和，也使用最后一次的结果
                group_scores = temp_scores
            final_scores.update(group_scores)

        # --- 阶段四：最终裁决与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 1 * 0.4
        T_overhead = max(0, (total_frames - 1) * 30)
        total_time_ms = T_reader + T_tag + T_overhead

        if not final_scores:
            return [], 0, total_time_ms, {"reason": "Scoring phase yielded no results"}

        max_score = max(final_scores.values())
        if max_score == 0:
            return [], 0, total_time_ms, {"reason": "Max score is zero"}

        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        verification_data = {
            "crossover_info": "Used Parent B's 'Grouped Probing' framework combined with Parent A's 'Progressive Scoring' logic inside each group.",
            "mutation_info": f"Introduced 'Adaptive Grouping' based on candidate density. Chosen GROUP_SIZE: {group_size} for {len(contenders)} candidates.",
            "initial_candidate_count": len(candidate_prices),
            "final_scores_count": len(final_scores),
            "final_scores": final_scores
        }

        return modes, max_score, total_time_ms, verification_data