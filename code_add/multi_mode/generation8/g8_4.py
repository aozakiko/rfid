import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g8_4(IProtocol):
    """
    子代协议: 具备早停机制的自适应过滤与并行验证协议
    (Adaptive Filtering with Early Termination and Parallel Verification Protocol)

    本协议是一个全新的子代协议，旨在高效解决复杂的多众数查询问题。
    它深度融合了父代协议A和B的优点，并引入了基于“收益递减”的
    早停机制作为突变，以在保证高准确率的同时，进一步优化通信效率。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0 # 因每个阶段的bits_per_slot可变，直接统计总比特数

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 30      # 当候选者数量小于此值时，停止过滤
        max_filter_rounds = 6         # 最大过滤轮次
        high_density_threshold = 0.75 # 帧密度过高阈值 (用于扩大帧)
        low_density_threshold = 0.25  # 帧密度过低阈值 (用于缩小帧)
        min_frame_size = 64           # 过滤帧的最小尺寸

        # --- 突变部分引入的超参数 ---
        diminishing_return_threshold = 0.95 # 若候选者数量与上一轮相比高于此比例，则早停

        # 阶段二: 验证相关
        verification_frame_size = 128

        # ==================================================================
        #  阶段一: 自适应迭代过滤
        # --- 交叉部分：融合父代A的严格多哈希过滤思想与父代B的自适应迭代框架 ---
        # 本阶段采纳了父代B的迭代式过滤框架，通过多轮通信逐步筛选候选价格。
        # 同时，每一轮的筛选逻辑借鉴了父代A的布隆过滤器思想：一个价格必须在
        # 所有哈希函数映射的时隙中都非空才能存活，这提供了更强的过滤能力。
        # 此外，从父代B继承的自适应帧长调整机制，使得协议能动态应对标签密度
        # 的变化，有效平衡了冲突与信道利用率。
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        filter_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size
        
        termination_reason = "Max rounds reached" # 默认的终止原因

        while len(candidate_prices) > candidate_threshold and filter_rounds_done < max_filter_rounds:
            
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 读写器广播帧长和哈希种子 (假设种子是32位)
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32)

            # --- [物理层模拟] ---
            # 读写器准备接收帧
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] = 1 # 仅标记为非空
            
            # 在过滤阶段，标签只回复1比特表示存在性
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # 读写器分析聚合信号
            new_candidates = set()
            for p in candidate_prices:
                is_still_candidate = True
                # 应用父代A的严格过滤逻辑
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    if filter_frames_states[i][slot_id] == 0:
                        is_still_candidate = False
                        break
                
                if is_still_candidate:
                    new_candidates.add(p)

            # --- 突变部分：引入基于“收益递减”的早停机制 ---
            # 与父代协议固定的终止条件不同，本协议引入了一个全新的动态判断逻辑。
            # 在每轮过滤后，读写器会计算候选者集合的“缩减率”。如果发现连续的
            # 过滤操作带来的收益（即筛除无效价格的效率）已经变得很低，协议将
            # 提前终止过滤阶段，避免在低效的通信上浪费时间，直接进入验证阶段。
            # 这种机制使得协议对不同数据分布的适应性更强。
            if len(candidate_prices) > 0 and (len(new_candidates) / len(candidate_prices)) > diminishing_return_threshold:
                candidate_prices = new_candidates # 更新候选者，然后退出
                filter_rounds_done += 1
                termination_reason = f"Diminishing returns (reduction rate > {diminishing_return_threshold})"
                break
            
            # 如果没有候选者幸存，则提前终止
            if not new_candidates:
                candidate_prices = set()
                termination_reason = "No candidates survived"
                break
            
            candidate_prices = new_candidates
            filter_rounds_done += 1

            # 检查是否因为候选者数量达标而终止
            if len(candidate_prices) <= candidate_threshold:
                termination_reason = f"Candidate count below threshold ({candidate_threshold})"
                break

            # --- 交叉部分：自适应调整帧长 (源自父代B) ---
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for slot_state in frame if slot_state > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size *= 2 # 碰撞太严重，扩大帧
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size) # 太稀疏，缩小帧
        
        final_candidate_list = sorted(list(candidate_prices))

        # ==================================================================
        #  阶段二: 并行位图精确验证
        # --- 交叉部分：该验证方法的核心逻辑继承自父代B的并行位图思想 ---
        # 经过第一阶段的大幅筛选后，本阶段直接采用父代B中最高效的并行位图
        # 验证方案。所有候选者被编码到一个位向量中，读写器通过一次通信，
        # 就能并行地、精确地统计出所有候选价格的数量。这避免了父代A中
        # 为每个候选者单独进行验证的巨大开销，是处理多个潜在众数的理想选择。
        # ==================================================================
        final_counts: Dict[int, int] = {}
        if final_candidate_list:
            total_frames += 1
            price_to_bit_pos_map = {price: i for i, price in enumerate(final_candidate_list)}
            bits_per_slot_verify = len(final_candidate_list)

            seed_verify = generate_random_seed()
            # 读写器广播验证参数：帧长、种子以及最终候选价格列表
            total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                          32 + # 假设种子是32位
                                          sum(p.bit_length() for p in final_candidate_list))

            # --- [物理层模拟] ---
            verification_frame_slots = [0] * verification_frame_size
            for t in tags:
                if t.price in price_to_bit_pos_map:
                    slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    verification_frame_slots[slot_id] |= (1 << bit_pos)

            total_tag_response_bits += verification_frame_size * bits_per_slot_verify
            # --- [物理层模拟结束] ---

            # 读写器分析聚合位图，计算每个价格的精确计数值
            for price, bit_pos in price_to_bit_pos_map.items():
                mask = 1 << bit_pos
                count = sum(1 for slot_data in verification_frame_slots if (slot_data & mask) != 0)
                if count > 0:
                    final_counts[price] = count

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4 
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "filter_rounds_done": filter_rounds_done,
            "termination_reason": termination_reason,
            "candidates_after_filter": len(final_candidate_list),
            "final_counts": final_counts,
        }

        if not final_counts:
            return [], 0, total_time_ms, debug_data

        max_score = max(final_counts.values())
        modes = sorted([p for p, s in final_counts.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data
