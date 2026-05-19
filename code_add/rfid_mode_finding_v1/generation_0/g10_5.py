import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_5(IProtocol):
    """
    子代协议: 混合缓刑优先级协议 (Hybrid Probationary Priority Protocol)

    本协议是一个为解决复杂多众数查询问题而设计的全新子代协议。它严格遵循信息隔离原则，
    其核心设计体现了对父代协议A和B思想的深度融合（交叉），并引入了全新的优化机制（突变）。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 50      # 当候选者数量小于此值时，停止过滤
        max_filter_rounds = 6         # 最大过滤轮次

        # (交叉) 继承自父代A和B的自适应帧长调整参数
        high_density_threshold = 0.8
        low_density_threshold = 0.3
        min_frame_size = 64

        # (交叉) 继承自父代B的停滞感知参数
        stagnation_threshold = 0.05
        min_rounds_before_stagnation_check = 2

        # 阶段二: 验证相关
        verification_frame_size = 128
        batch_size_threshold = 32     # (交叉) 继承自父代A的分批验证思想

        # (交叉) 继承自父代A的提前终止参数
        early_exit_gap_factor = 2.0
        min_significance_score = 15

        # --- 突变部分引入的超参数 ---
        max_probation_strikes = 2     # 候选者在被淘汰前，可以连续不产生碰撞的最大轮次数

        # ==================================================================
        #  阶段一: 缓刑制自适应过滤
        # --- 交叉部分：融合父代A的自适应帧长与碰撞分数累积思想，与父代B的停滞检测思想 ---
        # --- 突变部分：引入全新的“缓刑制”过滤机制，取代父代B刚性的“碰撞强度”规则 ---
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        
        # (交叉) 继承自父代A，为每个价格维护一个“碰撞分数”，用于后续验证阶段的优先级排序
        collision_scores: Dict[int, int] = {p: 0 for p in candidate_prices}
        
        # (突变) 引入全新的“缓刑”数据结构，记录每个候选者连续未产生碰撞的次数
        probation_strikes: Dict[int, int] = {p: 0 for p in candidate_prices}

        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            prev_candidate_count = len(candidate_prices)

            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32) # 假设种子是32位

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # 读写器分析聚合信号
            new_candidates = set()
            eliminated_this_round = set()

            for p in candidate_prices:
                is_present_in_all_frames = True
                round_collision_count = 0
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    slot_count = filter_frames_states[i][slot_id]

                    if slot_count == 0:
                        is_present_in_all_frames = False
                        break
                    if slot_count > 1:
                        round_collision_count += 1
                
                if not is_present_in_all_frames:
                    eliminated_this_round.add(p)
                    continue

                # (交叉) 只要候选者存在，就为其累积碰撞分数 (父代A思想)
                if round_collision_count > 0:
                    collision_scores[p] += round_collision_count

                # --- 突变部分：引入“缓刑制”全新机制 ---
                # 该机制是对父代B“碰撞强度”过滤规则的优化。父代B要求每一轮都必须有碰撞，
                # 过于严苛，可能误删真实的众数。本协议的“缓刑制”更为稳健：
                # 1. 如果一个价格在本轮中产生了碰撞，它就被认为是“安全的”，其缓刑计数清零。
                # 2. 如果它虽然存在于所有时隙中，但并未产生任何碰撞（“幸运儿”），
                #    则其缓刑计数加一。
                # 3. 只有当一个价格的缓刑计数累积到上限时，它才会被淘汰。
                # 这使得协议既能有效剔除持续不碰撞的低频价格，又能容忍高频价格因哈希运气
                # 而产生的偶然单例情况，从而在保证过滤效率的同时提升了准确性。
                if round_collision_count > 0:
                    probation_strikes[p] = 0 # 产生碰撞，表现良好，重置缓刑计数
                else:
                    probation_strikes[p] += 1 # 未产生碰撞，记一次警告

                if probation_strikes[p] >= max_probation_strikes:
                    eliminated_this_round.add(p) # 警告次数过多，淘汰
                else:
                    new_candidates.add(p)

            if not new_candidates:
                break
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # (交叉) 继承自父代A和B的帧密度自适应调整逻辑
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)
            
            # (交叉) 继承自父代B的停滞感知提前终止逻辑
            if filter_rounds_done >= min_rounds_before_stagnation_check:
                reduction_ratio = (prev_candidate_count - len(candidate_prices)) / prev_candidate_count if prev_candidate_count > 0 else 0
                if reduction_ratio < stagnation_threshold:
                    break

        # ==================================================================
        #  阶段二: 优先级批处理验证
        # --- 交叉部分：融合父代A的基于碰撞分数的动态排序、分批验证与启发式提前终止思想 ---
        # ==================================================================
        final_scores: Dict[int, int] = {}
        
        if candidate_prices:
            # (交叉) 根据过滤阶段累积的碰撞分数对候选者进行降序排列，高分者优先验证 (父代A思想)
            prioritized_candidate_list = sorted(list(candidate_prices), key=lambda p: collision_scores.get(p, 0), reverse=True)
            
            num_candidates = len(prioritized_candidate_list)
            num_batches = math.ceil(num_candidates / batch_size_threshold)
            
            for i in range(num_batches):
                batch_candidates = prioritized_candidate_list[i * batch_size_threshold : (i + 1) * batch_size_threshold]
                if not batch_candidates: continue

                total_frames += 1
                price_to_bit_pos_map = {price: j for j, price in enumerate(batch_candidates)}
                bits_per_slot = len(batch_candidates)

                seed_verify = generate_random_seed()
                total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                              seed_verify.bit_length() +
                                              sum(p.bit_length() for p in batch_candidates))

                # --- [物理层模拟] ---
                verification_frame_slots = [0] * verification_frame_size
                for t in tags:
                    if t.price in price_to_bit_pos_map:
                        slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                        bit_pos = price_to_bit_pos_map[t.price]
                        verification_frame_slots[slot_id] |= (1 << bit_pos)
                
                total_tag_response_bits += verification_frame_size * bits_per_slot
                # --- [物理层模拟结束] ---
                
                for price, bit_pos in price_to_bit_pos_map.items():
                    score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                    if score > 0:
                        final_scores[price] = final_scores.get(price, 0) + score
                
                # (交叉) 启发式提前终止检查 (父代A思想)
                if final_scores:
                    sorted_scores = sorted(final_scores.values(), reverse=True)
                    global_max_score = sorted_scores[0]
                    if len(sorted_scores) > 1:
                        global_second_max_score = sorted_scores[1]
                        if global_max_score >= min_significance_score and \
                           global_max_score >= global_second_max_score * early_exit_gap_factor:
                            break # 提前终止条件满足

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "filter_rounds_done": filter_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data
