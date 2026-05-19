import math
import statistics
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed

# Assume hash and base classes are in these files, as per the problem description.
from hash import simple_hash

class g6_2(IProtocol):
    """
    子代协议: 混合冲突自适应协议 (Hybrid Collision-Adaptive Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，通过一个创新的多阶段框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“冲突感知动态重试”机制以在关键时刻保证计数的准确性。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 交叉阶段参数
        GLOBAL_FILTER_FRAME_SIZE = 1024 # 全局过滤帧大小
        SCORING_FRAME_SIZE = 64         # 候选者评分帧大小
        STATISTICAL_PRUNING_FACTOR = 1.5 # 统计剪枝的标准差倍数
        
        # 突变阶段参数
        INITIAL_VERIFICATION_FRAME_SIZE = 128 # 初始验证帧大小
        COLLISION_THRESHOLD_RATIO = 0.01      # 冲突阈值 (空槽位占比低于1%则认为冲突严重)
        FRAME_RESIZE_FACTOR = 2.0             # 帧重置大小的倍数
        MAX_VERIFICATION_ATTEMPTS = 3         # 单个价格的最大验证次数

        # =================================================================================
        # 阶段 1: 全局哈希过滤 (Global Hash Filtering)
        # --- 交叉部分：融合父代A(g5_1)的“全局价格哈希过滤”思想与父代B(g5_5)的“范围剪枝”目标 ---
        # 本协议首先采用父代A的核心策略，通过对价格值进行一次性哈希，快速生成一个候选价格哈希表。
        # 相比父代B逐个检查价格区间的做法，这种方法在价格范围广泛时通信成本更低。
        # 此举旨在实现父代B的“快速排除大量空价格区间”的目标，但采用了父代A更高效的实现方式。
        # =================================================================================
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + GLOBAL_FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        filtering_slots = [0] * GLOBAL_FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % GLOBAL_FILTER_FRAME_SIZE
            filtering_slots[slot_id] = 1 # 只关心存在性
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += GLOBAL_FILTER_FRAME_SIZE * 1
        
        active_slot_indices = {i for i, state in enumerate(filtering_slots) if state > 0}
        
        initial_candidates = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % GLOBAL_FILTER_FRAME_SIZE in active_slot_indices:
                    initial_candidates.append(p)
        
        if not initial_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after global filtering"}

        # =================================================================================
        # 阶段 2: 启发式评分与统计剪枝 (Heuristic Scoring & Statistical Pruning)
        # --- 交叉部分：融合父代B(g5_5)的“统计剪枝”机制与父代A(g5_1)的“锦标赛”晋级思想 ---
        # 我们首先对所有初选候选者进行一轮快速评分，评分标准采用两个父代共有的“非空时隙数”。
        # 随后，我们不采用父代A简单的取最高分进入决赛的模式，而是采纳了父代B更智能的统计剪枝策略：
        # 计算所有分数的均值和标准差，动态设定一个晋级阈值。
        # 这相当于举办了一场更公平的“锦标赛”，只有表现远超平均水平的选手才能进入最终的验证环节。
        # =================================================================================
        price_to_score_map = {}
        for price in initial_candidates:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + SCORING_FRAME_SIZE.bit_length() + seed.bit_length()

            # --- [物理层模拟] ---
            slot_states = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SCORING_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1
            num_non_empty_slots = sum(1 for count in slot_states if count > 0)
            if num_non_empty_slots > 0:
                price_to_score_map[price] = num_non_empty_slots

        if not price_to_score_map:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores generated in scoring phase"}

        all_scores = list(price_to_score_map.values())
        finalists = []
        if len(all_scores) > 1:
            mean_score = statistics.mean(all_scores)
            stdev_score = statistics.stdev(all_scores)
            score_threshold = mean_score + STATISTICAL_PRUNING_FACTOR * stdev_score
            finalists = [p for p, s in price_to_score_map.items() if s >= score_threshold]
        
        if not finalists:
            max_score = max(all_scores) if all_scores else 0
            finalists = [p for p, s in price_to_score_map.items() if s == max_score]

        finalists.sort(key=lambda p: price_to_score_map[p], reverse=True)

        # =================================================================================
        # 阶段 3: 冲突感知精确验证 (Collision-Aware Precise Verification)
        # --- 突变部分：引入“冲突感知动态重试”全新机制 ---
        # 两个父代协议在验证阶段都采用固定的策略：父代A使用一个更大的固定帧，父代B则根据分数进行一次性缩放。
        # 本协议引入了一个全新的、动态的、以准确率为核心的闭环反馈机制。
        # 在对一个候选者进行验证后，读写器会分析返回帧的“健康状况”。如果空时隙数量过少（低于阈值），
        # 表明发生了严重的哈希冲突，当前的计数值是不可信的（很可能被严重低估）。
        # 此时，协议不会草率地接受这个结果，而是会为这同一个候选者自动发起一次或多次额外的验证，
        # 并且每次都使用一个更大的帧，直到冲突缓解、获得一个可信的计数值为止。
        # 这种为保证关键候选者计数准确性而进行的“动态追问”能力，是两个父代协议都不具备的。
        # =================================================================================
        final_counts = {}
        max_verified_count = -1
        modes = []
        
        for price in finalists:
            current_frame_size = INITIAL_VERIFICATION_FRAME_SIZE
            num_attempts = 0
            verified_count = 0

            while num_attempts < MAX_VERIFICATION_ATTEMPTS:
                num_attempts += 1
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed.bit_length()
                
                # --- [物理层模拟] ---
                slot_states = [0] * current_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % current_frame_size
                        slot_states[slot_id] += 1
                # --- [物理层模拟结束] ---
                
                total_tag_bits_sent += current_frame_size * 1

                num_zeros = slot_states.count(0)
                
                # 基于空槽位进行数量估计
                if num_zeros == current_frame_size:
                    verified_count = 0
                    break # 数量为0，无需重试
                elif num_zeros / current_frame_size < COLLISION_THRESHOLD_RATIO:
                    # 冲突严重，计数值可能不准，增大帧并准备重试
                    current_frame_size = int(current_frame_size * FRAME_RESIZE_FACTOR)
                    # 临时记录一个基于当前帧的估计值，以防这是最后一次尝试
                    verified_count = -current_frame_size * math.log(max(1, num_zeros) / current_frame_size)
                else:
                    # 冲突在可接受范围，计算结果可信，跳出重试循环
                    verified_count = -current_frame_size * math.log(num_zeros / current_frame_size)
                    break
            
            final_counts[price] = verified_count

            if verified_count > max_verified_count:
                max_verified_count = verified_count
                modes = [price]
            elif math.isclose(verified_count, max_verified_count):
                modes.append(price)

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "mutation_info": "Collision-Adaptive Verification was used.",
            "initial_candidate_count": len(initial_candidates),
            "finalist_count_before_verification": len(finalists),
            "heuristic_scores": price_to_score_map,
            "final_verified_counts": final_counts,
        }
        
        if not modes:
             return [], 0, total_time_ms, verification_data
        
        modes.sort()
        
        return modes, math.ceil(max_verified_count), total_time_ms, verification_data