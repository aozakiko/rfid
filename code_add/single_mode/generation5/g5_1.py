import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g5_1(IProtocol):
    """
    混合锦标赛众数查询协议 (Hybrid Tournament Mode Query Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并通过一个创新的多阶段过滤与评分框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“多样性预检与自适应过滤”机制以应对不同的数据分布。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 突变阶段参数
        DIVERSITY_PRECHECK_FRAME_SIZE = 64
        DIVERSITY_THRESHOLD = 0.5 # 超过50%的槽位被占用，则认为多样性高
        # 交叉阶段参数
        FILTER_FRAME_SIZE_LOW_DIVERSITY = 512
        FILTER_FRAME_SIZE_HIGH_DIVERSITY = 1024
        SEEDING_PASS_FRAME_SIZE = 64
        CHAMPIONSHIP_PASS_FRAME_SIZE = 512
        
        # --- 突变部分：引入“多样性预检与自适应过滤”全新机制 ---
        # 两个父代协议的第一阶段过滤/剪枝策略均采用固定的帧大小。
        # 本协议引入一个全新的预检步骤：在进行主候选价格过滤之前，协议首先使用一个极小的帧对全体标签的价格多样性进行一次快速探测。
        # 读写器根据探测到的非空时隙数（代表价格多样性），动态地、智能地调整后续主过滤阶段的帧大小。
        # 如果多样性低，则使用一个较小的、高效的过滤帧；如果多样性高，则自动切换到一个更大的、抗冲突能力更强的过滤帧。
        # 这种对问题“难度”的预判和资源调整能力是父代协议所不具备的。

        total_frames += 1
        precheck_seed = generate_random_seed()
        total_bits_sent_by_reader += (precheck_seed.bit_length() + DIVERSITY_PRECHECK_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        precheck_slots = [0] * DIVERSITY_PRECHECK_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, precheck_seed) % DIVERSITY_PRECHECK_FRAME_SIZE
            precheck_slots[slot_id] = 1 # 仅关心是否存在，不关心碰撞
        # --- [物理层模拟结束] ---

        total_tag_bits_sent += DIVERSITY_PRECHECK_FRAME_SIZE * 1
        
        diversity_metric = sum(precheck_slots) / DIVERSITY_PRECHECK_FRAME_SIZE
        main_filter_frame_size = (FILTER_FRAME_SIZE_HIGH_DIVERSITY if diversity_metric > DIVERSITY_THRESHOLD 
                                  else FILTER_FRAME_SIZE_LOW_DIVERSITY)
        
        # --- 交叉部分：融合父代B(g4_1)的“全局价格哈希过滤”思想与父代A(g2_1)的“非空时隙评分”思想 ---
        # 本协议首先采用父代B的核心策略，通过对价格值进行哈希，在一次通信中快速生成一个高度精炼的候选价格列表。
        # 这一步使用了由突变部分动态决定的帧大小，比父代协议更具适应性。
        # 随后，在评分阶段，我们采用父代A的核心评分标准，即使用“非空时隙数”这一鲁棒的指标来估计每个候选价格的标签数量，
        # 并借鉴了父代B的锦标赛分层思想进行高效评分。

        # 阶段一：自适应全局候选价格生成 (源自父代B思想)
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + main_filter_frame_size.bit_length())

        # --- [物理层模拟] ---
        filtering_slots = [0] * main_filter_frame_size
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % main_filter_frame_size
            filtering_slots[slot_id] = 1 # 同样只关心存在性
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += main_filter_frame_size * 1
        
        active_slot_indices = {i for i, state in enumerate(filtering_slots) if state > 0}
        
        candidates = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % main_filter_frame_size in active_slot_indices:
                    candidates.append(p)
        
        if not candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after filtering"}

        # 阶段二：锦标赛式评分 (源自父代A的评分标准 + 父代B的锦标赛框架)
        # 预选赛 (Seeding Pass)
        seeding_scores = {}
        for price in candidates:
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
            # 采用父代A的“非空时隙数”作为评分标准
            seeding_scores[price] = sum(1 for count in slot_states if count > 0)
        
        if not seeding_scores or max(seeding_scores.values()) == 0:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores in seeding pass"}

        # 识别进入决赛的选手
        max_seeding_score = max(seeding_scores.values())
        finalists = [price for price, score in seeding_scores.items() if score == max_seeding_score]

        # 决赛 (Championship Pass)
        final_scores = {}
        # 如果预选赛只有一位胜出者，且其分数已经很高（例如接近预选赛帧长），可能无需决赛。但为保证准确性，此处仍进行决赛。
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

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        # 确定最终结果
        if not final_scores:
            # 决赛没有产生任何分数，说明预选赛可能存在误差，此时返回预选赛结果
            max_score = max_seeding_score
            modes = sorted(finalists)
        else:
            max_score = max(final_scores.values())
            modes = sorted([p for p, s in final_scores.items() if s == max_score])

        verification_data = {
            "mutation_info": f"Diversity pre-check resulted in metric {diversity_metric:.2f}, chose filter frame size {main_filter_frame_size}",
            "candidate_count": len(candidates),
            "finalist_count": len(finalists),
            "finalists": finalists,
            "seeding_scores": seeding_scores,
            "final_scores": final_scores
        }
        
        return modes, max_score, total_time_ms, verification_data
