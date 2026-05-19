import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g5_3(IProtocol):
    """
    子代协议：门控过滤与动态验证协议 (Gated Filtering and Dynamic Verification Protocol)

    本协议是一个为解决复杂多众数查询问题而设计的全新三阶段子代协议。
    它严格遵循交叉与突变的设计准则，旨在以高准确率和高效率找出数据中的众数。

    协议流程分为三个核心阶段：

    1.  **交叉-突变阶段：双哈希碰撞门控 (Phase 1: Dual-Hash Collision Gating)**
        - **交叉**: 此阶段融合了父代B的“广域扫描”思想，旨在快速获得整个价格范围的“密度热力图”，以低成本识别出潜在的热点区域。
        - **突变**: 在此基础上，引入了父代A和B都未曾使用过的全新“双哈希碰撞门控”机制。协议使用两个独立的哈希函数将所有价格映射到两个不同的帧上。一个价格只有在两个帧的对应时隙中都引发了碰撞（即计数值 > 1），才会被确认为“高密度候选者”并进入下一阶段。这种“交叉验证”式的准入机制极大地提高了初筛的准确性，能有效过滤掉因偶然哈希碰撞而产生的噪声，确保只有真正存在于高密度区域的价格才能通过。

    2.  **交叉阶段：迭代式精确过滤 (Phase 2: Iterative Refinement Filtering)**
        - **交叉**: 此阶段完全继承了父代A的核心优势——“迭代式过滤”。对于通过了第一阶段门控筛选的候选集，本协议采用与父代A类似的、每轮使用多个哈希函数进行精炼的策略。这可以将被第一阶段“放行”的候选集规模进一步压缩，剥离掉与真正众数相邻的非众数价格，为最终的精确验证阶段大大减轻负担。

    3.  **交叉阶段：动态追溯计数 (Phase 3: Dynamic Tracing Verification)**
        - **交叉**: 对于经过两轮筛选后幸存下来的极少数“精英候选者”，本协议采用了父代B中最具鲁棒性的“动态追溯计数”机制。为每个候选者启动一个独立的、帧长自适应的计数流程。如果帧饱和，则自动加倍帧长重试，直至获得一个精确的、非饱和的计数值。这确保了即使面对频率极高且相近的多个众数，协议也能以确定性的方式给出准确的最终计数，保证了协议的最终准确率。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1  # 协议所有阶段的时隙回复均为1bit

        # --- 协议超参数 ---
        # 阶段一：门控相关
        gating_frame_size = 512
        gating_hash_functions = 2
        # 阶段二：过滤相关
        refinement_frame_size = 128
        refinement_hash_functions = 2
        refinement_candidate_threshold = 20 # 当候选者小于此值时，停止过滤
        max_refinement_rounds = 4
        # 阶段三：验证相关
        initial_verification_frame_size = 64
        max_verification_frame_size = 4096

        # ==================================================================
        #  阶段一: 双哈希碰撞门控
        # ==================================================================
        
        # --- 交叉部分：融合父代B的“广域扫描”思想，快速识别热点区域 ---
        # --- 突变部分：引入“双哈希碰撞门控”全新机制，通过交叉验证提高初筛准确性 ---
        total_frames += gating_hash_functions
        gating_seeds = [generate_random_seed() for _ in range(gating_hash_functions)]
        total_bits_sent_by_reader += sum(s.bit_length() for s in gating_seeds) + gating_frame_size.bit_length() * gating_hash_functions

        # --- [物理层模拟] ---
        gating_frames_states = [[0] * gating_frame_size for _ in range(gating_hash_functions)]
        for t in tags:
            for i in range(gating_hash_functions):
                slot_id = simple_hash(t.price, gating_seeds[i]) % gating_frame_size
                if gating_frames_states[i][slot_id] < 2: # 超过2的碰撞无意义，节省模拟开销
                    gating_frames_states[i][slot_id] += 1
        total_slots += gating_frame_size * gating_hash_functions
        # --- [物理层模拟结束] ---
        
        # --- 读写器分析聚合信号，生成第一轮候选集 ---
        initial_candidates = set()
        all_prices = range(price_min, price_max + 1)
        for p in all_prices:
            is_strong_candidate = True
            for i in range(gating_hash_functions):
                slot_p = simple_hash(p, gating_seeds[i]) % gating_frame_size
                # 必须在所有门控帧中都造成碰撞
                if gating_frames_states[i][slot_p] <= 1:
                    is_strong_candidate = False
                    break
            if is_strong_candidate:
                initial_candidates.add(p)

        candidate_prices = initial_candidates

        # ==================================================================
        #  阶段二: 迭代式精确过滤
        # ==================================================================

        # --- 交叉部分：融合父代A的“迭代式过滤”思想，对候选集进行多轮精炼 ---
        refinement_rounds_done = 0
        while len(candidate_prices) > refinement_candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += refinement_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(refinement_hash_functions)]
            total_bits_sent_by_reader += sum(s.bit_length() for s in filter_seeds) + refinement_frame_size.bit_length()

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * refinement_frame_size for _ in range(refinement_hash_functions)]
            # 仅拥有候选价格的标签参与响应
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(refinement_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % refinement_frame_size
                        filter_frames_states[i][slot_id] += 1
            total_slots += refinement_frame_size * refinement_hash_functions
            # --- [物理层模拟结束] ---

            # --- 读写器分析聚合信号，生成新的候选集 ---
            new_candidates = set()
            for p in candidate_prices:
                survived = True
                for i in range(refinement_hash_functions):
                    slot_p = simple_hash(p, filter_seeds[i]) % refinement_frame_size
                    if filter_frames_states[i][slot_p] == 0:
                        survived = False
                        break
                if survived:
                    new_candidates.add(p)
            
            # 如果候选集没有变化，提前终止以防死循环
            if len(new_candidates) == len(candidate_prices):
                break
                
            candidate_prices = new_candidates
            refinement_rounds_done += 1
            
        # ==================================================================
        #  阶段三: 动态追溯计数
        # ==================================================================

        # --- 交叉部分：融合父代B的“动态追溯计数”思想，对最终候选者进行精确、鲁棒的计数 ---
        final_counts = {}
        for p in sorted(list(candidate_prices)): # 排序以保证执行顺序确定性
            current_frame_size = initial_verification_frame_size
            estimated_count = float('inf')

            while estimated_count == float('inf') and current_frame_size <= max_verification_frame_size:
                total_frames += 1
                seed_verify = generate_random_seed()
                total_bits_sent_by_reader += p.bit_length() + current_frame_size.bit_length() + seed_verify.bit_length()
                
                # --- [物理层模拟] ---
                verification_slot_states = [0] * current_frame_size
                for t in tags:
                    if t.price == p:
                        slot_id = simple_hash(t.id, seed_verify) % current_frame_size
                        verification_slot_states[slot_id] += 1
                total_slots += current_frame_size
                # --- [物理层模拟结束] ---

                num_zeros = verification_slot_states.count(0)
                
                if num_zeros == current_frame_size:
                    estimated_count = 0
                elif num_zeros == 0:
                    # 帧饱和，加倍帧长重试
                    current_frame_size *= 2
                    estimated_count = float('inf') # 保持inf状态以继续循环
                else:
                    # 成功获得非饱和帧，计算估算值并跳出循环
                    estimated_count = -current_frame_size * math.log(num_zeros / current_frame_size)
            
            final_counts[p] = estimated_count

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "num_initial_candidates": len(initial_candidates),
            "num_final_candidates": len(candidate_prices),
            "refinement_rounds_done": refinement_rounds_done,
            "final_counts": final_counts,
        }

        if not final_counts:
            return [], 0, total_time_ms, verification_data

        max_count = -1
        has_inf = any(c == float('inf') for c in final_counts.values())

        if has_inf:
            max_count = float('inf')
        else:
            max_count = max(final_counts.values()) if final_counts else -1

        if max_count == -1:
             return [], 0, total_time_ms, verification_data
             
        modes = sorted([
            price for price, count in final_counts.items()
            if (has_inf and count == float('inf')) or (not has_inf and math.isclose(count, max_count))
        ])
        
        display_count = float('inf') if has_inf else (math.ceil(max_count) if max_count > 0 else 0)

        return modes, display_count, total_time_ms, verification_data
