import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g7_5(IProtocol):
    """
    双层过滤与自适应迭代众数查询协议 (Dual-Filter Adaptive Iterative Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并通过一个创新的多阶段框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“双哈希函数协同过滤”机制以实现前所未有的候选集精炼能力。
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
        FILTER_FRAME_SIZE_1 = 1024  # 第一层哈希过滤的帧大小
        FILTER_FRAME_SIZE_2 = 1024  # 第二层哈希过滤的帧大小
        INITIAL_SCORING_FRAME_SIZE = 64 # 评分阶段的初始帧大小
        MAX_SCORING_ROUNDS = 3      # 评分阶段最多进行的轮次
        CONFIDENCE_THRESHOLD = 3.5  # 提前终止的置信度阈值 (最高分/次高分)
        MIN_FRAME_SIZE = 32         # 动态帧大小下限
        MAX_FRAME_SIZE = 1024       # 动态帧大小上限
        COLLISION_HIGH_THRESHOLD = 0.35 # 碰撞率高阈值，用于增大帧
        COLLISION_LOW_THRESHOLD = 0.1  # 碰撞率低阈值，用于减小帧
        
        # --- 交叉部分：融合父代A的“全局价格哈希过滤”思想 ---
        # --- 突变部分：引入“双哈希函数协同过滤”全新机制 ---
        # 阶段一：双层哈希候选价格过滤
        # 本协议采纳了父代A (g6_1) 的高效“全局哈希过滤”思想，即不通过范围扫描，而是直接对价格值进行哈希来快速定位潜在的候选者。
        # 作为一项全新的突变，本协议将该思想进行了强化升级，引入了“双哈希函数协同过滤”机制。
        # 读写器使用两个独立的哈希函数和帧进行两次连续的全局探测。一个价格只有在两轮探测中都映射到非空时隙，才会被接纳为候选价格。
        # 这种“双重确认”的策略极大地降低了因哈希碰撞而引入无关价格的概率，能够生成一个比任何父代协议都更纯净、更精简的候选集，
        # 从而显著减少后续昂贵评分阶段的工作量。这是两个父代协议均未采用的全新过滤增强逻辑。

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

        # --- 第二次哈希过滤 ---
        total_frames += 1
        filter_seed_2 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_2.bit_length() + FILTER_FRAME_SIZE_2.bit_length()
        
        # [物理层模拟]
        filtering_slots_2 = [0] * FILTER_FRAME_SIZE_2
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_2) % FILTER_FRAME_SIZE_2
            filtering_slots_2[slot_id] = 1
        # [物理层模拟结束]

        total_tag_bits_sent += FILTER_FRAME_SIZE_2 * 1
        active_slot_indices_2 = {i for i, state in enumerate(filtering_slots_2) if state > 0}

        # --- 读写器本地生成最终候选集 ---
        candidate_prices = []
        if active_slot_indices_1 and active_slot_indices_2:
            for p in range(price_min, price_max + 1):
                hash1_in_set = (simple_hash(p, filter_seed_1) % FILTER_FRAME_SIZE_1) in active_slot_indices_1
                hash2_in_set = (simple_hash(p, filter_seed_2) % FILTER_FRAME_SIZE_2) in active_slot_indices_2
                if hash1_in_set and hash2_in_set:
                    candidate_prices.append(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after dual-hash filtering"}

        # --- 交叉部分：融合父代A的“多轮迭代评分与置信度早停”结构与父代B的“基于碰撞率的动态帧调整”策略 ---
        # 阶段二：自适应迭代评分
        # 本阶段深度融合了两个父代协议在评分阶段的核心优势，形成了一个全新的、更高效的评分框架。
        # 1. 结构上，采纳了父代A (g6_1) 的“多轮迭代与置信度早停”框架。协议会进行多轮评分，每轮结束后评估结果，
        #    如果众数已经非常明确（置信度高），则提前终止，避免冗余通信。
        # 2. 细节上，在每一轮评分的内部，当逐个探测候选价格时，引入了父代B (g2_1) 的“基于碰撞率的动态帧调整”机制。
        #    即探测完一个价格后，会根据其碰撞情况智能调整用于探测下一个价格的帧大小。
        # 这种“宏观迭代”与“微观自适应”的结合，使得协议既能快速锁定胜者，又能精细化地为每次探测分配最合适的通信资源。

        current_contenders = candidate_prices
        current_scores = {}
        scoring_frame_size = INITIAL_SCORING_FRAME_SIZE
        final_round_num = 0

        for round_num in range(MAX_SCORING_ROUNDS):
            final_round_num = round_num + 1
            
            # 对本轮的竞争者进行探测
            for price in current_contenders:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + scoring_frame_size.bit_length() + seed.bit_length()

                # [物理层模拟]
                slot_states = [0] * scoring_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % scoring_frame_size
                        slot_states[slot_id] += 1
                # [物理层模拟结束]
                
                total_tag_bits_sent += scoring_frame_size * 1
                
                num_non_empty_slots = 0
                num_collisions = 0
                for count in slot_states:
                    if count > 0:
                        num_non_empty_slots += 1
                    if count > 1:
                        num_collisions += 1
                
                # 采用父代通用的“非空时隙数”作为核心评分标准
                current_scores[price] = num_non_empty_slots
                
                # 执行源自父代B的动态帧调整逻辑
                if num_non_empty_slots > 0:
                    collision_rate = num_collisions / num_non_empty_slots
                    if collision_rate > COLLISION_HIGH_THRESHOLD:
                        scoring_frame_size = min(MAX_FRAME_SIZE, int(scoring_frame_size * 2.0))
                    elif collision_rate < COLLISION_LOW_THRESHOLD:
                        scoring_frame_size = max(MIN_FRAME_SIZE, int(scoring_frame_size * 0.75))

            # 执行源自父代A的置信度早停与迭代逻辑
            if not current_scores or max(current_scores.values()) == 0:
                break 

            sorted_scores = sorted(current_scores.values(), reverse=True)
            max_score = sorted_scores[0]
            
            if len(sorted_scores) < 2:
                break # 只有一个有效得分，可以终止

            second_max_score = sorted_scores[1]
            
            if second_max_score > 0 and (max_score / second_max_score) >= CONFIDENCE_THRESHOLD:
                break # 置信度足够高，提前终止

            # 筛选进入下一轮的竞争者
            if round_num < MAX_SCORING_ROUNDS - 1:
                next_contenders = [p for p, s in current_scores.items() if s >= second_max_score]
                current_contenders = list(set(next_contenders))
        
        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Fused A's iterative refinement with B's dynamic frame sizing.",
            "mutation_info": "Introduced a dual-hash filtering mechanism for superior candidate pruning.",
            "initial_candidate_count": len(candidate_prices),
            "final_round_num": final_round_num,
            "final_scores": current_scores
        }
        
        if not current_scores:
            return [], 0, total_time_ms, verification_data

        max_final_score = max(current_scores.values())
        if max_final_score == 0:
            return [], 0, total_time_ms, verification_data
            
        modes = sorted([
            price for price, score in current_scores.items() 
            if score == max_final_score
        ])
        
        return modes, max_final_score, total_time_ms, verification_data