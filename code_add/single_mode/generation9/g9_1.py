import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_1(IProtocol):
    """
    混合淘汰众数查询协议 (Hybrid Elimination Mode Query Protocol)

    本协议是一个为大规模RFID系统设计的全新子代协议，旨在高效、准确地解决众数查询问题。
    它严格遵循信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。

    协议流程分为三个核心阶段：
    1. 范围预检： 继承父代A的分箱思想，快速过滤掉大量不存在标签的价格区间。
    2. 候选者识别与优化探测： 融合了父代B的迭代思想，并引入了全新的“空帧跳过”突变机制，
       以极高的效率对活跃价格进行评分，并根据父代B的“双标准”晋级逻辑选出少量精英候选者。
    3. 冠军赛： 对精英候选者进行一轮高精度的最终探测，以确定最终的众数。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        PRECHECK_BINS = 16          # 阶段一：范围预检的分箱数量
        PRECHECK_FRAME_SIZE = 128   # 阶段一：范围预检的帧大小

        SCORING_FRAME_SIZE = 64     # 阶段二：评分阶段的基础帧大小
        CHAMPIONSHIP_FRAME_SIZE = 512 # 阶段三：冠军赛使用的帧大小，用于精确区分
        
        # 交叉参数 (源自父代B)
        COLLISION_PROMOTION_THRESHOLD = 0.15 # 认定为高碰撞的阈值
        
        # 突变参数
        EFS_GROUP_SIZE = 8 # 空帧跳过(EFS)机制的批处理大小

        # --- 阶段一：范围预检 (继承自父代A的分箱思想) ---
        # 采用父代A的分箱策略，通过少量通信快速确定哪些价格“范围”是存在标签的“活跃范围”。
        # 这是“由粗到精”的第一步，可以有效避免对整个价格空间进行盲目搜索。
        active_prices = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        price_bins = [
            list(range(price_min + i * bin_size, min(price_min + (i + 1) * bin_size, price_max + 1)))
            for i in range(PRECHECK_BINS)
        ]

        for p_bin in price_bins:
            if not p_bin: continue
            
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() + 
                                          p_bin[0].bit_length() + p_bin[-1].bit_length())
            
            # --- [物理层模拟] ---
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_id = simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1
            
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found"}

        # --- 阶段二：候选者识别与优化探测 ---
        
        # --- 突变部分：引入“空帧跳过 (EFS)”全新机制 ---
        # 两个父代协议在探测时都是对候选价格逐一进行查询。本协议引入一个全新的优化：
        # 将连续的活跃价格分组（例如每8个一组），首先对整个组进行一次联合查询。
        # 如果返回的帧是完全空的（所有时隙都为0），则证明这个组内没有任何价格拥有标签，
        # 从而可以安全地跳过对组内所有价格的单独查询，显著减少了在稀疏区域的通信开销。
        # 只有当联合查询帧为非空时，才需要对组内价格进行逐一探测。
        
        price_to_score_map = {}
        sorted_active_prices = sorted(list(active_prices))
        
        price_groups = [
            sorted_active_prices[i:i + EFS_GROUP_SIZE]
            for i in range(0, len(sorted_active_prices), EFS_GROUP_SIZE)
        ]

        prices_to_probe_individually = []
        for group in price_groups:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + SCORING_FRAME_SIZE.bit_length() +
                                          group[0].bit_length() + group[-1].bit_length())

            # --- [物理层模拟 - 组查询] ---
            slot_states = [0] * SCORING_FRAME_SIZE
            group_set = set(group)
            for t in tags:
                if t.price in group_set:
                    slot_id = simple_hash(t.id, seed) % SCORING_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1

            if any(count > 0 for count in slot_states):
                prices_to_probe_individually.extend(group)

        # 对通过了EFS筛选的价格进行逐一精确评分
        for price in prices_to_probe_individually:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + SCORING_FRAME_SIZE.bit_length() + seed.bit_length()

            # --- [物理层模拟 - 单价查询] ---
            slot_states = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SCORING_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1

            num_non_empty = sum(1 for c in slot_states if c > 0)
            if num_non_empty > 0:
                num_collisions = sum(1 for c in slot_states if c > 1)
                collision_rate = num_collisions / SCORING_FRAME_SIZE
                price_to_score_map[price] = {'score': num_non_empty, 'collision_rate': collision_rate}

        if not price_to_score_map:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores recorded after probing"}

        # --- 交叉部分：融合父代A的“分阶段探测”框架与父代B的“优胜者与高碰撞者晋级”的迭代优化思想 ---
        # 本部分将两个父代的核心逻辑进行有机结合。
        # 整体上，我们采用了父代A的“预检-详查”两阶段框架。
        # 在详查阶段结束后，我们不直接选出得分最高的作为胜者，而是引入了父代B更精妙的候选者筛选逻辑：
        # 不仅得分最高的候选者可以晋级，那些虽然得分不高但表现出“高碰撞率”的候选者同样有资格晋级。
        # 这是因为高碰撞可能意味着其背后有大量标签，只是因为帧大小不足而未能充分展现其实力。
        # 这种“双标准”晋级法，使得协议能够识别出被低估的潜在胜者，大大提高了准确性。
        
        max_prelim_score = max((res['score'] for res in price_to_score_map.values()), default=0)
        
        contenders = set()
        for price, result in price_to_score_map.items():
            # 规则1：最高分晋级
            if result['score'] == max_prelim_score:
                contenders.add(price)
            # 规则2：高碰撞率者晋级
            if result['collision_rate'] > COLLISION_PROMOTION_THRESHOLD:
                 contenders.add(price)
        
        contenders = sorted(list(contenders))

        # --- 阶段三：冠军赛 ---
        # 对从上一阶段脱颖而出的精英候选者，进行最后一轮高精度、大帧的探测。
        # 这一思想借鉴了父代B的多轮迭代思想，但将其简化为一轮最终的“冠军赛”。
        # 使用远大于评分阶段的帧，可以最大程度地减少碰撞，让候选者们的真实实力（标签数量）
        # 更准确地反映在“非空时隙数”这一指标上，从而做出最准确的裁决。
        final_scores = {}
        if not contenders:
             # 如果没有候选者（不太可能发生，但作为保护），直接从初步结果中选出
            modes = sorted([p for p, res in price_to_score_map.items() if res['score'] == max_prelim_score])
            final_max_score = max_prelim_score
        else:
            for price in contenders:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + CHAMPIONSHIP_FRAME_SIZE.bit_length() + seed.bit_length()

                # --- [物理层模拟] ---
                slot_states = [0] * CHAMPIONSHIP_FRAME_SIZE
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % CHAMPIONSHIP_FRAME_SIZE
                        slot_states[slot_id] += 1
                # --- [物理层模拟结束] ---
                
                total_tag_bits_sent += CHAMPIONSHIP_FRAME_SIZE * 1
                
                final_scores[price] = sum(1 for c in slot_states if c > 0)

            if not final_scores:
                final_max_score = 0
                modes = []
            else:
                final_max_score = max(final_scores.values())
                modes = sorted([p for p, s in final_scores.items() if s == final_max_score])

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Fused Parent A's phased framework with Parent B's dual-criteria (score + collision) promotion logic.",
            "mutation_info": "Introduced Empty Frame Skip (EFS) to bypass detailed probing of empty price groups.",
            "active_price_count": len(active_prices),
            "probed_price_count": len(prices_to_probe_individually),
            "contender_count": len(contenders),
            "final_scores": final_scores
        }

        return modes, final_max_score, total_time_ms, verification_data
