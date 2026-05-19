import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed,simple_hash


class g8_3(IProtocol):
    """
    子代协议: 混合优先级模式查询协议 (Hybrid Priority Mode-Query Protocol)

    本协议是一个为解决多众数问题而设计的全新子代协议。它严格遵循信息隔离原则，
    其核心设计体现了对两个父代协议思想的深度融合（交叉）与创新优化（突变）。

    交叉策略:
    本协议融合了父代A的“自适应帧长调整”和“分批验证”思想，以及父代B的“碰撞感知”理念。
    在过滤阶段，协议会像父代B一样收集候选价格的碰撞信息，同时像父代A一样根据信道密度动态调整帧长，
    以在不同数据分布下维持较高的过滤效率。在验证阶段，它采纳了父代A的分批处理思想，以控制通信开销。

    突变策略:
    本协议引入了一项在两个父代中都未出现过的全新机制：“基于碰撞分数的动态优先级排序与启发式提前终止”。
    不同于父代B将碰撞信息用于硬性预筛选，本协议将其作为“优先级”的指示器。在验证阶段前，
    所有候选者将根据其在过滤阶段累积的碰撞分数进行降序排列。随后，协议会优先验证最可能成为众数的候选者。
    更重要的是，在每一批验证完成后，协议会进行一次启发式检查：如果当前发现的最高频次远超次高频次，
    协议将提前终止，从而在众数分布明显的情况下大幅节省总执行时间。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 50        # 当候选者数量小于此值时，停止过滤
        max_filter_rounds = 5           # 最大过滤轮次
        high_density_threshold = 0.8    # 帧密度过高阈值 (用于扩大帧)
        low_density_threshold = 0.3     # 帧密度过低阈值 (用于缩小帧)
        min_frame_size = 64             # 过滤帧的最小尺寸

        # 阶段二: 验证相关
        verification_frame_size = 128
        batch_size_threshold = 32       # 验证阶段中，单个批次处理的最大候选者数量

        # --- 突变部分引入的超参数 ---
        early_exit_gap_factor = 2.0     # 提前终止的频次差距因子
        min_significance_score = 15     # 触发提前终止的最低频次要求

        # ==================================================================
        #  阶段一: 自适应过滤与碰撞信息收集
        # --- 交叉部分：融合父代A的自适应帧长调整思想与父代B的碰撞感知理念 ---
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        # 借鉴父代B，为每个潜在价格维护一个“碰撞分数”
        collision_scores: Dict[int, int] = {p: 0 for p in candidate_prices}

        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 读写器广播帧长和哈希种子
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32) # 假设种子是32位

            # --- [物理层模拟] ---
            # 读写器准备接收帧，每个slot记录响应次数
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            # 在过滤阶段，标签只回复1比特表示存在性
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # 读写器分析聚合信号
            new_candidates = set()
            for p in candidate_prices:
                is_still_candidate = True
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    slot_count = filter_frames_states[i][slot_id]

                    if slot_count == 0:
                        is_still_candidate = False
                        break
                    # 如果价格p映射到了一个碰撞时隙，增加其碰撞分数
                    if slot_count > 1:
                        collision_scores[p] += 1
                
                if is_still_candidate:
                    new_candidates.add(p)

            if not new_candidates:
                break
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # 借鉴父代A，根据帧密度自适应调整帧长
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

        # ==================================================================
        #  阶段二: 优先级批处理验证与提前终止
        # --- 突变部分：引入基于碰撞分数的候选者动态排序与验证阶段的启发式提前终止机制 ---
        # --- 交叉部分：验证的核心逻辑继承自父代A的并行位图与分批处理思想 ---
        # ==================================================================
        final_scores: Dict[int, int] = {}
        
        if candidate_prices:
            # 突变核心：根据碰撞分数对候选者进行降序排列，高分者优先验证
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
                # 读写器广播验证参数
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
                
                # 读写器分析聚合信号，计算当前批次中每个价格的得分
                for price, bit_pos in price_to_bit_pos_map.items():
                    score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                    if score > 0:
                        final_scores[price] = final_scores.get(price, 0) + score
                
                # 突变核心：启发式提前终止检查
                if final_scores:
                    sorted_scores = sorted(final_scores.values(), reverse=True)
                    global_max_score = sorted_scores[0]
                    # 只有当存在至少两个不同分数时才进行比较
                    if len(sorted_scores) > 1:
                        global_second_max_score = sorted_scores[1]
                        if global_max_score >= min_significance_score and \
                           global_max_score >= global_second_max_score * early_exit_gap_factor:
                            # 提前终止条件满足，跳出验证循环
                            break 
        
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
