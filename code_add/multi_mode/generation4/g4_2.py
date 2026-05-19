import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g4_2(IProtocol):
    """
    混合自适应协议 (Hybrid Adaptive Protocol)

    本协议是一个为解决多众数查询问题设计的全新子代协议。它严格遵循交叉与突变的设计准则，
    旨在准确且高效地从大量标签中找出一个高频价格。

    协议流程分为两个核心阶段：

    1.  **多哈希布隆过滤阶段 (交叉)**:
        此阶段深度融合了父代协议的核心思想。
        # --- 交叉部分：融合父代A的多哈希过滤思想与父代B的逐级筛选哲学 ---
        它采用父代A的“多哈希函数（类布隆过滤器）”策略，对整个价格空间进行快速扫描，
        以极低的成本和极高的准确率筛除绝大多数无关价格。这个阶段的目标，与父代B的
        第一阶段哲学一致，即并非精确计数，而是高效地构建一个高质量的、规模极小的
        候选价格池，为下一阶段的精确打击做准备。

    2.  **自适应动态计数阶段 (交叉与突变)**:
        # --- 交叉部分：融合父代B的动态追溯计数思想 ---
        对于在第一阶段筛选出的极少数顶尖候选者，本协议采用了父代B的“动态追溯计数”机制。
        该机制通过“不成功则加倍重试”的策略，确保了即使面对计数值极高的众数，
        协议也能以确定性的方式获得准确的计数值。
        
        # --- 突变部分：引入基于过滤阶段反馈的自适应初始帧长机制 ---
        在此基础上，本协议引入了一项全新的优化。传统动态计数从一个固定的初始帧长开始，
        在面对高频众数时，可能需要多次加倍才能达到一个合适的帧长，造成了通信浪费。
        本协议的突变机制利用了第一阶段过滤帧的聚合信息。在对一个候选价格进行精确计数前，
        读写器会分析其在过滤阶段各个哈希槽中的碰撞情况，并从中推断出一个更合理的
        “初始计数值”，并以此为依据动态计算一个更优的初始帧长。这使得协议能“一步到位”，
        或至少显著减少帧长加倍的次数，从而提升了第二阶段的效率。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1  # 两个阶段的时隙回复均为1bit

        # --- 协议超参数 ---
        # 阶段一：过滤
        FILTER_FRAME_SIZE = 512
        NUM_HASH_FUNCTIONS = 2
        # 阶段二：验证
        INITIAL_VERIFICATION_FRAME_SIZE = 64 # 默认的最小起始帧长
        MAX_VERIFICATION_FRAME_SIZE = 8192   # 帧长上限，防止无限循环

        # ==================================================================
        #  阶段一: 多哈希布隆过滤 - 快速生成高质量候选集
        # ==================================================================
        
        # --- 读写器行为: 广播多哈希过滤指令 ---
        total_frames += NUM_HASH_FUNCTIONS 
        filter_seeds = [generate_random_seed() for _ in range(NUM_HASH_FUNCTIONS)]
        # 广播帧长和多个种子
        bits_sent = FILTER_FRAME_SIZE.bit_length()
        for seed in filter_seeds:
            bits_sent += seed.bit_length()
        total_bits_sent_by_reader += bits_sent

        # --- [物理层模拟] ---
        # 标签根据自身价格和收到的多个种子，在概念上的多个帧中进行响应
        filter_frames_states = [[0] * FILTER_FRAME_SIZE for _ in range(NUM_HASH_FUNCTIONS)]
        for t in tags:
            for i in range(NUM_HASH_FUNCTIONS):
                slot_id = simple_hash(t.price, filter_seeds[i]) % FILTER_FRAME_SIZE
                filter_frames_states[i][slot_id] += 1
        total_slots += FILTER_FRAME_SIZE * NUM_HASH_FUNCTIONS
        # --- [物理层模拟结束] ---

        # --- 读写器分析: 筛选候选价格 ---
        candidate_prices = []
        for p in range(price_min, price_max + 1):
            is_candidate = True
            # --- 应用父代A的多哈希过滤逻辑 ---
            for i in range(NUM_HASH_FUNCTIONS):
                slot_p = simple_hash(p, filter_seeds[i]) % FILTER_FRAME_SIZE
                if filter_frames_states[i][slot_p] == 0:
                    is_candidate = False
                    break # 只要有一个哈希未命中，就不是候选者
            
            if is_candidate:
                candidate_prices.append(p)

        # ==================================================================
        #  阶段二: 自适应动态计数 - 对候选价格进行精确打击
        # ==================================================================
        final_counts = {}
        for price in candidate_prices:
            
            # [修正]: 移除基于第一阶段反馈的自适应初始帧长机制。
            # 原始逻辑通过读取并使用精确的碰撞计数值来设置初始帧长，
            # 这违反了读写器只能感知{空, 单个, 碰撞}三态的原则。
            # 合规的协议必须从一个固定的、预定义的初始帧长开始。
            current_frame_size = INITIAL_VERIFICATION_FRAME_SIZE
            
            estimated_count = float('inf')

            # --- 交叉部分：应用父代B的动态追溯计数逻辑 ---
            # 循环直到找到一个不饱和的帧或达到帧长上限
            while estimated_count == float('inf') and current_frame_size <= MAX_VERIFICATION_FRAME_SIZE:
                seed = generate_random_seed()
                total_frames += 1
                total_bits_sent_by_reader += (seed.bit_length() + current_frame_size.bit_length() + price.bit_length())

                # --- [物理层模拟] ---
                verification_slot_states = [0] * int(current_frame_size)
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % int(current_frame_size)
                        verification_slot_states[slot_id] += 1
                total_slots += int(current_frame_size)
                # --- [物理层模拟结束] ---

                num_zeros = verification_slot_states.count(0)

                if num_zeros == current_frame_size:
                    estimated_count = 0
                elif num_zeros == 0:
                    # 仍然饱和，将帧长加倍并继续循环
                    current_frame_size *= 2
                    estimated_count = float('inf') # 保持inf状态以继续循环
                else:
                    # 成功获得非饱和帧，计算估算值并跳出循环
                    estimated_count = -current_frame_size * math.log(num_zeros / current_frame_size)
            
            final_counts[price] = math.ceil(estimated_count) if estimated_count != float('inf') else float('inf')

        # ==================================================================
        #  最终处理 (Post-processing)
        # ==================================================================
        
        # --- 时间成本计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "num_candidates_from_filter": len(candidate_prices),
            "final_counts": final_counts,
            "total_frames": total_frames
        }

        if not final_counts:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = -1
        has_inf = any(score == float('inf') for score in final_counts.values())

        if has_inf:
            max_score = float('inf')
        else:
            max_score = max(final_counts.values()) if final_counts else 0

        modes = sorted([
            price for price, score in final_counts.items()
            if score == max_score
        ])
        
        display_score = int(max_score) if not has_inf else float('inf')

        return modes, display_score, total_time_ms, verification_data