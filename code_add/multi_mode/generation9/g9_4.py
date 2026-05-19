import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_4(IProtocol):
    """
    子代协议: 基于碰撞感知的启发式过滤协议
    (Collision-Aware Heuristic Filtering Protocol)

    本协议通过深度融合父代协议A和B的核心思想，并引入全新的、基于碰撞分数的
    启发式剪枝机制作为“突变”，旨在更高效地解决大规模标签环境下的多众数查询问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_reply_bits = 0

        # --- 协议超参数 ---
        initial_frame_size = 256
        num_hash_functions = 2
        verification_frame_size = 128
        candidate_stop_threshold = 32  # 当候选者数量 <= 此值时，停止过滤
        max_filter_rounds = 5

        # --- 交叉部分的超参数 (源自父代B) ---
        high_density_threshold = 0.8
        low_density_threshold = 0.3
        min_frame_size = 64
        
        # --- (已移除) 突变部分的超参数 ---
        # 由于启发式剪枝机制违规，相关参数已失效

        # ==================================================================
        #  阶段一: 迭代式启发过滤
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        current_frame_size = initial_frame_size
        
        for round_num in range(max_filter_rounds):
            if len(candidate_prices) <= candidate_stop_threshold:
                break

            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 读写器广播帧长和哈希种子 (假设种子是32位)
            total_bits_sent_by_reader += num_hash_functions * (current_frame_size.bit_length() + 32)

            # --- [物理层模拟] ---
            # --- 交叉部分：融合父代A的碰撞计数思想与父代B的迭代框架 ---
            # 已修正: 物理层模拟现在只向上层提供二态信息 {空, 非空}。
            # 时隙的值为 0 (空) 或 1 (非空)，不再是精确的碰撞计数。
            filter_frames_states = [[0] * current_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_frame_size
                        filter_frames_states[i][slot_id] = 1 # 任何响应都使时隙变为1(非空)
            
            # 在过滤阶段，标签回复1比特表示存在性
            total_tag_reply_bits += current_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # --- 读写器分析 ---
            # --- 交叉部分：融合父代A的严格过滤思想 ---
            # 采用父代A的核心过滤逻辑：一个价格必须在其所有哈希函数映射的时隙中都“非空”，
            # 才能成为初步候选者。这是比简单ALOHA更强的约束，能有效过滤掉大量无关价格。
            preliminary_candidates = set()
            for p in candidate_prices:
                is_preliminary_candidate = True
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_frame_size
                    if filter_frames_states[i][slot_id] == 0:
                        is_preliminary_candidate = False
                        break
                if is_preliminary_candidate:
                    preliminary_candidates.add(p)

            if not preliminary_candidates:
                candidate_prices = set()
                break
            
            # --- (已移除) 突变部分：基于碰撞分数的启发式剪枝全新机制 ---
            # 原始协议的违规剪枝逻辑已被移除。
            # 所有通过初步筛选的候选者都将保留到下一轮。
            candidate_prices = preliminary_candidates

            # --- 交叉部分：融合父代B的自适应帧长调整 ---
            # 继承父代B的优秀机制，根据本轮通信的“帧密度”来动态调整下一轮的帧长，
            # 以此来平衡冲突和信道利用率。此逻辑是合规的，因为它仅依赖于 {空, 非空} 信息。
            total_slots_in_round = num_hash_functions * current_frame_size
            non_empty_slots = sum(sum(1 for slot_state in frame if slot_state > 0) for frame in filter_frames_states)
            density = non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_frame_size *= 2 # 碰撞严重，扩大帧
            elif density < low_density_threshold:
                new_size = current_frame_size // 2
                current_frame_size = max(min_frame_size, new_size) # 过于稀疏，缩小帧

        final_candidate_list = sorted(list(candidate_prices))

        # ==================================================================
        #  阶段二: 并行位图精确验证
        # --- 交叉部分：继承父代A和B共有的高效并行验证机制 ---
        # ==================================================================
        final_counts: Dict[int, int] = {}
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            bits_per_slot_verify = len(final_candidate_list)
            
            seed_verify = generate_random_seed()
            total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                          32 + # seed
                                          sum(p.bit_length() for p in final_candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)

            total_tag_reply_bits += verification_frame_size * bits_per_slot_verify
            # --- [物理层模拟结束] ---

            # 读写器解码，精确计数
            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                count = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if count > 0:
                    final_counts[price] = count
        
        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_reply_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_info = {
            "final_candidates_count": len(final_candidate_list),
            "final_counts": final_counts,
        }

        if not final_counts:
            return [], 0, total_time_ms, debug_info

        max_score = max(final_counts.values())
        modes = sorted([p for p, s in final_counts.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_info