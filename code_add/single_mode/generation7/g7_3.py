import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g7_3(IProtocol):
    """
    分层自适应解析协议 (Hierarchical Adaptive Resolution Protocol - HARP)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，深度融合了两个父代协议的核心思想，并引入了全新的
    “基于预估规模的决赛帧动态调整”机制，以实现通信资源的最优化分配。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 交叉阶段参数
        FILTER_FRAME_SIZE = 1024  # 全局过滤帧
        SEEDING_FRAME_SIZE = 64   # 预选赛/侦察帧
        COLLISION_PROMOTION_THRESHOLD = 0.15 # 预选赛冲突率超过15%的候选者直接晋级
        MIN_SEEDING_SCORE_TO_CONSIDER = 5 # 预选赛得分低于此值，即使冲突很高也不予考虑，避免噪声
        
        # 突变阶段参数
        MIN_CHAMPIONSHIP_FRAME_SIZE = 128 # 决赛最小帧
        MAX_CHAMPIONSHIP_FRAME_SIZE = 1024 # 决赛最大帧


        # --- 交叉部分：融合父代A的“全局哈希过滤+锦标赛”思想与父代B的“基于冲突的评估”思想 ---
        # 协议第一阶段：采用两个父代都使用的“全局价格哈希过滤”策略，使用一个固定的、较大的帧快速筛选出候选价格集合。
        # 协议第二阶段：借鉴父代A（g5_1）的“锦标赛”分层思想，对所有候选者进行一次低成本的“预选赛”。
        # 但在晋级规则上，融合了父代B（g5_2）的核心洞见：我们不仅看预选赛得分，还关注“冲突率”。
        # 一个得分不高但冲突率极高的候选者，也可能是一个被严重低估的真正众数，因此也应获得晋级资格。
        # 这种“得分”与“冲突”双轨制的晋级机制是两个父代思想的直接有机结合。

        # 阶段一：全局候选价格生成
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        filtering_slots = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTER_FRAME_SIZE
            filtering_slots[slot_id] = 1 # 只关心时隙是否非空
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += FILTER_FRAME_SIZE * 1

        active_slot_indices = {i for i, state in enumerate(filtering_slots) if state > 0}
        
        candidate_prices = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTER_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)
        
        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after filtering"}

        # 阶段二：预选赛 & 智能晋级
        seeding_results = {}
        for price in candidate_prices:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + SEEDING_FRAME_SIZE.bit_length() + seed.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * SEEDING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SEEDING_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SEEDING_FRAME_SIZE * 1
            
            num_non_empty = sum(1 for count in slot_states if count > 0)
            num_collisions = sum(1 for count in slot_states if count > 1)
            collision_rate = num_collisions / SEEDING_FRAME_SIZE if SEEDING_FRAME_SIZE > 0 else 0
            seeding_results[price] = {'score': num_non_empty, 'collision_rate': collision_rate}

        if not seeding_results:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores in seeding pass"}

        # 确定晋级决赛的选手
        max_seeding_score = max((res['score'] for res in seeding_results.values()), default=0)
             
        finalists = set()
        for price, result in seeding_results.items():
            # 规则1: 得分最高的选手晋级 (父代A思想)
            if result['score'] == max_seeding_score and max_seeding_score > 0:
                finalists.add(price)
            # 规则2: 冲突率极高的潜力选手晋级 (父代B思想)
            if result['score'] > MIN_SEEDING_SCORE_TO_CONSIDER and result['collision_rate'] > COLLISION_PROMOTION_THRESHOLD:
                finalists.add(price)
        
        finalist_prices = sorted(list(finalists))

        # --- 突变部分：引入“基于预估规模的决赛帧动态调整”全新机制 ---
        # 两个父代协议在决赛/确认阶段都采用固定的帧大小。父代A（g5_1）使用固定的CHAMPIONSHIP_PASS_FRAME_SIZE，
        # 父代B（g5_2）使用固定的CONFIRMATION_FRAME_SIZE。
        # 本协议引入一个全新的动态优化机制：在进行最终计数的决赛阶段，我们不再对所有决赛选手都使用
        # 同一个“一刀切”的大帧，而是利用预选赛中收集到的信息（非空时隙数），为每一位决赛选手
        # “量身定制”一个最优的决赛帧大小。
        # 对于预估标签数较少的选手，使用一个较小的、高效的帧；对于预估数量庞大的选手，则动态分配一个
        # 足以容纳它们、抗冲突能力强的大帧。
        # 这种针对性的资源分配策略是父代协议所不具备的，它能够在保证精度的前提下，最大限度地减少不必要的通信开销。

        def calculate_optimal_frame_size(estimated_tags: int) -> int:
            """根据预估标签数动态计算最优帧大小"""
            # 理论上最优帧大小约等于标签数。我们使用1.5倍作为系数以留出余量，减少冲突。
            optimal_size = int(estimated_tags * 1.5)
            # 将大小限制在预设的最小和最大决赛帧之间
            return max(MIN_CHAMPIONSHIP_FRAME_SIZE, min(optimal_size, MAX_CHAMPIONSHIP_FRAME_SIZE))

        # 阶段三：自适应决赛
        final_scores = {}
        championship_details = {}
        for price in finalist_prices:
            # 使用预选赛的得分作为标签数量的初步估计
            estimated_tags_count = seeding_results[price]['score']
            # 为该价格动态计算本次决赛的帧大小
            dynamic_frame_size = calculate_optimal_frame_size(estimated_tags_count)
            
            championship_details[price] = {'estimated_tags': estimated_tags_count, 'chosen_frame_size': dynamic_frame_size}
            
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + dynamic_frame_size.bit_length() + seed.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * dynamic_frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % dynamic_frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += dynamic_frame_size * 1
            final_scores[price] = sum(1 for count in slot_states if count > 0)

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        # 确定最终结果
        if not final_scores:
            # 如果决赛圈无人晋级或无人得分，则退回到使用预选赛的结果
            modes = sorted([p for p, res in seeding_results.items() if res['score'] == max_seeding_score]) if max_seeding_score > 0 else []
        else:
            max_score = max(final_scores.values())
            modes = sorted([p for p, s in final_scores.items() if s == max_score])

        verification_data = {
            "crossover_info": "Fused g5-1's tournament with g5-2's collision awareness for promotion.",
            "mutation_info": "Introduced dynamic frame sizing for each finalist in the championship round.",
            "candidate_count": len(candidate_prices),
            "finalist_count": len(finalist_prices),
            "finalists": finalist_prices,
            "seeding_results": seeding_results,
            "championship_details": championship_details,
            "final_scores": final_scores
        }
        
        return modes, max_score, total_time_ms, verification_data
