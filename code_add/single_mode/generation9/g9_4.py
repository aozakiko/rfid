import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_4(IProtocol):
    """
    混合加权累积评分众数查询协议 (Hybrid Weighted Cumulative Scoring Mode Query Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，通过一个创新的多阶段框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“加权累积评分”机制，使得评分过程能够综合历史信息，做出更鲁棒的决策。
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
        FILTER_FRAME_SIZE = 1024          # 过滤阶段的帧大小
        INITIAL_SCORING_FRAME_SIZE = 64   # 评分阶段的初始帧大小
        MAX_SCORING_ROUNDS = 4            # 评分阶段最多进行的轮次
        CONFIDENCE_THRESHOLD = 3.0        # 提前终止的置信度阈值 (基于累积加权分数)
        FRAME_ESCALATION_FACTOR = 2.0     # 评分轮次间的帧大小增长因子
        COLLISION_HIGH_THRESHOLD = 0.4    # 碰撞率高阈值，用于增大帧
        COLLISION_LOW_THRESHOLD = 0.15    # 碰撞率低阈值，用于减小帧
        MIN_FRAME_SIZE = 32               # 动态帧大小下限
        MAX_FRAME_SIZE = 2048             # 动态帧大小上限

        # --- 交叉部分：融合父代A和B共有的“双哈希协同过滤”思想 ---
        # 阶段一：双层哈希候选价格过滤
        # 本协议采纳了两个父代协议中都采用且被验证为最高效的“双哈希协同过滤”策略来生成初始候选集。
        # 读写器使用两个独立的哈希函数进行两次全局探测。一个价格只有在两轮探测中都映射到非空时隙，
        # 才会被接纳为候选价格。这种“双重确认”策略能极大地降低哈希碰撞引入的噪声，
        # 生成一个比任何单层过滤都更精简、更准确的候选集，为后续阶段奠定坚实基础。

        # --- 第一次哈希过滤 ---
        total_frames += 1
        filter_seed_1 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_1.bit_length() + FILTER_FRAME_SIZE.bit_length()
        
        # [物理层模拟]
        filtering_slots_1 = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_1) % FILTER_FRAME_SIZE
            filtering_slots_1[slot_id] = 1
        # [物理层模拟结束]
        
        total_tag_bits_sent += FILTER_FRAME_SIZE * 1
        active_slot_indices_1 = {i for i, state in enumerate(filtering_slots_1) if state > 0}

        # --- 第二次哈希过滤 ---
        total_frames += 1
        filter_seed_2 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_2.bit_length() + FILTER_FRAME_SIZE.bit_length()
        
        # [物理层模拟]
        filtering_slots_2 = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_2) % FILTER_FRAME_SIZE
            filtering_slots_2[slot_id] = 1
        # [物理层模拟结束]

        total_tag_bits_sent += FILTER_FRAME_SIZE * 1
        active_slot_indices_2 = {i for i, state in enumerate(filtering_slots_2) if state > 0}

        # --- 读写器本地生成最终候选集 ---
        candidate_prices = []
        if active_slot_indices_1 and active_slot_indices_2:
            for p in range(price_min, price_max + 1):
                hash1_in_set = (simple_hash(p, filter_seed_1) % FILTER_FRAME_SIZE) in active_slot_indices_1
                hash2_in_set = (simple_hash(p, filter_seed_2) % FILTER_FRAME_SIZE) in active_slot_indices_2
                if hash1_in_set and hash2_in_set:
                    candidate_prices.append(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after dual-hash filtering"}

        # --- 交叉部分：融合父代A的“迭代分级与帧升级框架”与父代B的“基于碰撞率的帧内动态调整”思想 ---
        # 阶段二：混合自适应迭代评分
        # 本阶段的宏观框架采纳了父代A的“多轮迭代，逐级确认，帧长升级”的核心思想，对候选者进行多轮评分，且每轮的基础帧长都会增长。
        # 同时，在每一轮的每一次单独探测中，又融入了父代B的“基于碰撞率动态调整帧大小”的微操策略。
        # 这种“宏观分轮升级”与“微观动态调整”的有机结合，使得协议既能从战略上逐步聚焦，又能从战术上精细化每一次通信的资源分配。
        
        # --- 突变部分：引入“加权累积评分”全新机制 ---
        # 两个父代协议在每一轮评分结束后，都会完全抛弃该轮的评分结果，下一轮从零开始重新计算。
        # 本协议引入一项全新的优化：引入“加权累积评分”机制。每一轮探测得到的分数（非空时隙数）不会被抛弃，
        # 而是会乘以一个与该轮基础帧长相关的权重，然后累加到该候选价格的总分上。
        # 这个设计的逻辑是：后续轮次使用更大的帧进行探测，其结果更精确，因此其分数应该被赋予更高的权重。
        # 这种机制使得最终的决策是基于所有轮次的历史信息综合得出的，而不是仅仅依赖最后一轮可能存在噪声的结果。
        # 它显著增强了协议的稳定性，避免了因单轮的随机性波动而过早淘汰真正的众数。

        current_contenders = candidate_prices
        cumulative_scores = {price: 0 for price in candidate_prices} # [突变核心]
        base_scoring_frame_size = INITIAL_SCORING_FRAME_SIZE
        final_round_num = 0

        for round_num in range(MAX_SCORING_ROUNDS):
            final_round_num = round_num + 1
            
            # [突变核心] 本轮分数的权重与基础帧长成正比
            round_weight = int(base_scoring_frame_size)
            
            probe_frame_size = int(base_scoring_frame_size)
            
            # 对本轮的竞争者进行探测
            current_round_scores = {}
            for price in current_contenders:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + probe_frame_size.bit_length() + seed.bit_length()

                # [物理层模拟]
                slot_states = [0] * probe_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % probe_frame_size
                        slot_states[slot_id] += 1
                # [物理层模拟结束]
                
                total_tag_bits_sent += probe_frame_size * 1
                
                num_non_empty_slots = 0
                num_collisions = 0
                for count in slot_states:
                    if count > 0:
                        num_non_empty_slots += 1
                    if count > 1:
                        num_collisions += 1
                
                # 记录本轮原始分，并更新累积加权分
                current_round_scores[price] = num_non_empty_slots
                cumulative_scores[price] += num_non_empty_slots * round_weight # [突变核心]
                
                # [交叉逻辑 - 源自父代B] 动态调整下一次探测的帧大小
                if num_non_empty_slots > 0:
                    collision_rate = num_collisions / num_non_empty_slots
                    if collision_rate > COLLISION_HIGH_THRESHOLD:
                        probe_frame_size = min(MAX_FRAME_SIZE, int(probe_frame_size * 1.5))
                    elif collision_rate < COLLISION_LOW_THRESHOLD:
                        probe_frame_size = max(MIN_FRAME_SIZE, int(probe_frame_size * 0.75))

            # [交叉逻辑 - 源自父代A & 突变] 基于累积加权分，评估是否提前终止
            if not cumulative_scores or max(cumulative_scores.values()) == 0:
                break

            sorted_scores = sorted(cumulative_scores.values(), reverse=True)
            max_score = sorted_scores[0]
            
            if len(sorted_scores) < 2 or sorted_scores[1] == 0:
                break # 只有一个有效得分或次高分为0，可以终止

            second_max_score = sorted_scores[1]
            
            if (max_score / second_max_score) >= CONFIDENCE_THRESHOLD:
                break # 置信度足够高，提前终止

            # [交叉逻辑 - 源自父代A] 准备下一轮
            if round_num < MAX_SCORING_ROUNDS - 1:
                # 筛选进入下一轮的竞争者（所有累积得分不低于次高分的）
                next_contenders = [p for p, s in cumulative_scores.items() if s >= second_max_score]
                current_contenders = list(set(next_contenders))
                # 增加下一轮的基础帧大小
                base_scoring_frame_size *= FRAME_ESCALATION_FACTOR
        
        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Fused dual-hash filtering (A&B), iterative scoring with frame escalation (A), and intra-round dynamic frame sizing (B).",
            "mutation_info": "Introduced a weighted cumulative scoring mechanism where scores from each round are weighted and accumulated.",
            "initial_candidate_count": len(candidate_prices),
            "final_round_num": final_round_num,
            "final_scores": {k:v for k,v in sorted(cumulative_scores.items(), key=lambda item: item[1], reverse=True) if v > 0}
        }
        
        if not cumulative_scores:
            return [], 0, total_time_ms, verification_data

        max_final_score = max(cumulative_scores.values())
        if max_final_score == 0:
            return [], 0, total_time_ms, verification_data
            
        modes = sorted([
            price for price, score in cumulative_scores.items() 
            if score == max_final_score
        ])
        
        return modes, int(max_final_score), total_time_ms, verification_data