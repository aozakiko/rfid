import math
import statistics
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g7_1(IProtocol):
    """
    子代协议: 优势众数捷径协议 (Dominant Mode Shortcut Protocol)

    本协议是一个全新的、功能完整的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并通过强制交叉与强制突变，形成了一个独特的解决方案。

    协议流程:
    1. [交叉] 全局价格哈希过滤 (源自父代B): 使用一次大通信帧，让所有标签根据自身价格进行哈希，
       快速生成一个初步的候选价格集，极大地缩小问题规模。
    2. [交叉] 逐价自适应评分 (源自父代A): 对候选集中的每个价格进行评分。评分过程中，帧大小会根据
       上一轮查询的碰撞率动态调整，智能地适应不同价格的标签密度。
    3. [突变] 优势众数提前终止 (全新机制): 在评分结束后，协议会检查是否存在一个“超级优胜者”。
       如果最高分远超第二高分，协议将直接认定其为众数并提前终止，为数据分布有明显峰值的场景
       提供了巨大的效率提升。
    4. [交叉] 统计学剪枝 (源自父代B): 如果不存在优势众数，则采用统计学方法（均值和标准差）
       动态计算淘汰线，只保留得分真正优异的候选者进入决赛。
    5. [交叉] 高精度对数验证 (源自父代B): 对通过剪枝的最终候选者，使用高精度的对数估计算法
       进行最终的数量核实，确保结果的准确性。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0  # 记录所有帧的时隙总数, bits_per_slot=1

        # --- 协议超参数 ---
        FILTER_FRAME_SIZE = 1024       # 阶段1: 全局过滤帧大小
        INITIAL_SCORING_FRAME_SIZE = 64 # 阶段2: 评分阶段的初始帧大小
        MIN_FRAME_SIZE = 32            # 阶段2: 帧大小动态调整下限
        MAX_FRAME_SIZE = 512           # 阶段2: 帧大小动态调整上限
        COLLISION_HIGH_THRESHOLD = 0.4 # 阶段2: 判断碰撞率高，需增大帧的阈值
        COLLISION_LOW_THRESHOLD = 0.1  # 阶段2: 判断碰撞率低，可减小帧的阈值
        DOMINANCE_FACTOR = 3.0         # 阶段3: 判断优势众数的因子 (s1 > s2 * factor)
        STATISTICAL_PRUNING_FACTOR = 1.0 # 阶段4: 统计剪枝的标准差倍数
        VERIFICATION_FRAME_SCALE_FACTOR = 2.0 # 阶段5: 验证阶段帧大小的缩放因子
        MIN_VERIFICATION_FRAME_SIZE = 128   # 阶段5: 验证阶段的最小帧大小

        # =================================================================================
        # 阶段 1: 全局价格哈希过滤
        # --- 交叉部分：融合父代B的“全局价格哈希过滤”思想与父代A的“分阶段”处理流程 ---
        # 采用父代B（g6_3）高效的初步筛选机制。通过让所有标签根据自身价格（price）在
        # 一个共享大帧中哈希，读写器能一次性反推出所有可能存在的价格，形成候选集。
        # 这比父代A的逐区间扫描在效率上更优，构成了协议的第一阶段。
        # =================================================================================
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        filtering_slots = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTER_FRAME_SIZE
            filtering_slots[slot_id] += 1
        # --- [物理层模拟结束] ---
        
        total_slots += FILTER_FRAME_SIZE

        active_slot_indices = {i for i, count in enumerate(filtering_slots) if count > 0}
        
        preliminary_candidates = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTER_FRAME_SIZE in active_slot_indices:
                    preliminary_candidates.append(p)
        
        if not preliminary_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4 # bits_per_slot is 1
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No preliminary candidates found"}

        # =================================================================================
        # 阶段 2: 逐价自适应评分
        # --- 交叉部分：融合父代A的“基于碰撞率的动态帧调整”思想与父代B的“候选集评分”流程 ---
        # 我们对阶段1筛选出的候选集进行评分。评分的核心逻辑继承自父代A（g2_1）的突变机制：
        # 读写器根据当前价格探测的碰撞情况，动态调整用于下一个价格探测的帧大小。
        # 这使得评分过程能细粒度地适应每个候选价格的拥挤程度。
        # =================================================================================
        price_to_score_map = {}
        current_frame_size = INITIAL_SCORING_FRAME_SIZE
        
        for price in preliminary_candidates:
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
            
            total_slots += current_frame_size

            num_non_empty_slots = 0
            num_collisions = 0
            for count in slot_states:
                if count > 0:
                    num_non_empty_slots += 1
                if count > 1:
                    num_collisions += 1
            
            if num_non_empty_slots > 0:
                price_to_score_map[price] = num_non_empty_slots

            collision_rate = num_collisions / current_frame_size if current_frame_size > 0 else 0
            if collision_rate > COLLISION_HIGH_THRESHOLD:
                current_frame_size = min(MAX_FRAME_SIZE, int(current_frame_size * 1.5))
            elif collision_rate < COLLISION_LOW_THRESHOLD:
                current_frame_size = max(MIN_FRAME_SIZE, int(current_frame_size * 0.75))

        if not price_to_score_map:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores generated in scoring phase"}

        # =================================================================================
        # 阶段 3: 优势众数提前终止
        # --- 突变部分：引入“基于分数优势的提前终止”全新机制 ---
        # 此机制在两个父代协议中均未出现。在评分结束后，协议会快速检查是否存在一个分数
        # “一骑绝尘”的候选者。通过比较最高分与次高分的比率，如果差距足够大（超过
        # DOMINANCE_FACTOR），协议会自信地认为已找到众数并立即终止，从而跳过后续
        # 所有的统计计算和验证通信，实现对特定数据分布的“捷径”优化。
        # =================================================================================
        if len(price_to_score_map) >= 2:
            sorted_scores = sorted(price_to_score_map.items(), key=lambda item: item[1], reverse=True)
            top_candidate_price, top_score = sorted_scores[0]
            second_top_score = sorted_scores[1][1]
            
            if top_score > 0 and second_top_score * DOMINANCE_FACTOR < top_score:
                T_reader = total_bits_sent_by_reader * 0.025
                T_tag = total_slots * 1 * 0.4
                T_overhead = (total_frames - 1) * 30
                total_time_ms = T_reader + T_tag + T_overhead
                
                verification_data = {
                    "reason": "Dominant mode shortcut taken",
                    "dominant_price": top_candidate_price,
                    "dominant_score": top_score,
                    "second_score": second_top_score,
                }
                # 使用评分作为最终结果，因为未进行精确验证
                return [top_candidate_price], top_score, total_time_ms, verification_data

        # =================================================================================
        # 阶段 4: 统计学剪枝
        # --- 交叉部分：继承父代B的“动态候选集筛选”思想 ---
        # 如果不存在明显的优势众数，协议将回退到更稳健的流程。此阶段完全继承父代B（g6_3）的
        # 智能剪枝逻辑。通过计算所有分数的均值和标准差来动态设定淘汰线，确保只有
        # 统计上显著的候选者才能进入最终验证，比固定阈值更具适应性。
        # =================================================================================
        candidates_for_verification = []
        all_scores = list(price_to_score_map.values())
        
        if len(all_scores) > 1:
            mean_score = statistics.mean(all_scores)
            stdev_score = statistics.stdev(all_scores)
            score_threshold = mean_score + STATISTICAL_PRUNING_FACTOR * stdev_score
            candidates_for_verification = [p for p, s in price_to_score_map.items() if s >= score_threshold]
        
        if not candidates_for_verification:
            max_score = max(all_scores) if all_scores else 0
            candidates_for_verification = [p for p, s in price_to_score_map.items() if s == max_score]

        # =================================================================================
        # 阶段 5: 高精度对数验证
        # --- 交叉部分：采用父代B的“对数精确估计”方法 ---
        # 对于通过了层层筛选的最终候选者，我们采用父代B（g6_3）的精确计数方法。该方法
        # 使用对数公式(-F * log(空槽位数 / 总槽位数))来高精度地估计每个价格的标签数量，
        # 是确定最终胜者的可靠保证，确保了协议的“准确率优先”原则。
        # =================================================================================
        final_counts = {}
        
        for price in candidates_for_verification:
            frame_size = max(MIN_VERIFICATION_FRAME_SIZE, int(price_to_score_map[price] * VERIFICATION_FRAME_SCALE_FACTOR))
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + frame_size.bit_length() + seed.bit_length()
            
            # --- [物理层模拟] ---
            slot_states = [0] * frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_slots += frame_size

            num_zeros = slot_states.count(0)
            verified_count = 0
            if num_zeros == frame_size:
                verified_count = 0
            elif num_zeros == 0:
                verified_count = frame_size * 2.39 
            else:
                verified_count = -frame_size * math.log(num_zeros / frame_size)
            final_counts[price] = verified_count

        if not final_counts:
            max_score = max(price_to_score_map.values())
            modes = sorted([p for p, s in price_to_score_map.items() if s == max_score])
            estimated_count = max_score
        else:
            max_verified_count = max(final_counts.values())
            modes = sorted([p for p, c in final_counts.items() if math.isclose(c, max_verified_count, rel_tol=1e-5)])
            estimated_count = math.ceil(max_verified_count)

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 1 * 0.4  # bits_per_slot is 1
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "preliminary_candidate_count": len(preliminary_candidates),
            "final_verified_counts": final_counts,
            "mutation_info": "Dominant mode shortcut was not triggered"
        }
        
        return modes, estimated_count, total_time_ms, verification_data