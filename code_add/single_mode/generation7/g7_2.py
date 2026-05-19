import math
import statistics
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g7_2(IProtocol):
    """
    子代协议: 混合动力自适应剪枝协议 (Hybrid-Powered Adaptive Pruning Protocol)

    本协议是一个为高效、准确地解决大规模RFID众数查询问题而设计的全新子代协议。
    它严格遵守信息隔离原则，深度融合了两个父代协议的核心设计哲学，并引入了一项
    关于动态剪枝策略的全新突变机制。

    协议流程:
    1. 多样性预检 (源自父代B): 协议启动时，首先通过一次极低开销的通信，快速评估
       环境中价格的多样性。
    2. 自适应过滤 (源自父代B): 根据预检结果，智能地选择一个最优大小的帧来进行
       全局价格哈希过滤，高效地生成初步候选集。
    3. 启发式评分 (交叉思想): 对初步候选集中的每个价格，使用两个父代都认可的
       “非空时隙数”作为启发式评分，快速评估其大致数量。
    4. 动态剪枝 (突变+交叉): 协议的核心创新。它继承了父代A的统计剪枝思想，但
       其“剪枝力度”不再是固定的，而是由第一步的预检结果动态决定。面对价格分布
       复杂的环境，协议会变得“保守”，而在分布简单的环境下则会更“激进”地筛选。
    5. 精确验证 (源自父代A): 对通过了层层筛选的最终优胜者，采用父代A中高精度
       的对数估计算法进行最终计数，确保结果的准确性。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        DIVERSITY_PRECHECK_FRAME_SIZE = 64
        DIVERSITY_THRESHOLD_HIGH = 0.5
        FILTER_FRAME_SIZE_LOW_DIVERSITY = 512
        FILTER_FRAME_SIZE_HIGH_DIVERSITY = 1024
        SCORING_FRAME_SIZE = 48
        VERIFICATION_FRAME_SCALE_FACTOR = 2.0
        MIN_VERIFICATION_FRAME_SIZE = 128
        
        # 突变部分使用的动态参数
        PRUNING_FACTOR_LOW_DIVERSITY = 1.5  # 多样性低时，采用更激进的剪枝策略
        PRUNING_FACTOR_HIGH_DIVERSITY = 0.8 # 多样性高时，采用更保守的剪枝策略

        # =================================================================================
        # 阶段 1: 多样性预检与动态参数调整
        # --- 突变部分：引入“基于多样性预检的动态剪枝因子”全新机制 ---
        # 父代A使用固定的统计剪枝因子，父代B则利用预检结果调整过滤帧大小。
        # 本协议将父代B的“预检”思想进行了升华：预检结果不仅用于调整过滤帧（这是对
        # 父代B思想的继承），更被用来动态设定一个全新的参数——“剪枝因子”。
        # 如果预检发现价格多样性很高（问题复杂），协议会采用一个较小的剪枝因子，在
        # 统计筛选时更为“宽容”，以防过早淘汰潜在的众数。反之，若多样性低，则采用
        # 更大的剪枝因子进行“激进”筛选，加快协议进程。这种根据问题难度动态调整核心
        # 算法攻击性的能力，在两个父代中均未出现。
        # --- 交叉部分：继承父代B的“多样性预检”思想框架 ---
        # 整个预检操作的流程和机制（即使用小帧探测并计算多样性度量）是直接从父代B
        # 中借鉴的成功经验。
        # =================================================================================
        total_frames += 1
        precheck_seed = generate_random_seed()
        total_bits_sent_by_reader += (precheck_seed.bit_length() + DIVERSITY_PRECHECK_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        precheck_slots = [0] * DIVERSITY_PRECHECK_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, precheck_seed) % DIVERSITY_PRECHECK_FRAME_SIZE
            precheck_slots[slot_id] = 1
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += DIVERSITY_PRECHECK_FRAME_SIZE * 1
        
        diversity_metric = sum(precheck_slots) / DIVERSITY_PRECHECK_FRAME_SIZE
        
        # --- [突变逻辑应用] ---
        if diversity_metric > DIVERSITY_THRESHOLD_HIGH:
            main_filter_frame_size = FILTER_FRAME_SIZE_HIGH_DIVERSITY
            dynamic_pruning_factor = PRUNING_FACTOR_HIGH_DIVERSITY
        else:
            main_filter_frame_size = FILTER_FRAME_SIZE_LOW_DIVERSITY
            dynamic_pruning_factor = PRUNING_FACTOR_LOW_DIVERSITY
        
        # =================================================================================
        # 阶段 2: 自适应全局候选价格过滤
        # --- 交叉部分：融合父代B的“自适应全局价格哈希过滤”思想 ---
        # 此阶段完全采纳了父代B的核心策略：根据第一阶段的预检结果，选择一个与之匹配
        # 的帧大小，通过一次大规模价格哈希通信，高效地将价格范围从1000缩小至一个
        # 非常小的初步候选集。
        # =================================================================================
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + main_filter_frame_size.bit_length())

        # --- [物理层模拟] ---
        filtering_slots = [0] * main_filter_frame_size
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % main_filter_frame_size
            filtering_slots[slot_id] = 1
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += main_filter_frame_size * 1
        
        active_slot_indices = {i for i, state in enumerate(filtering_slots) if state > 0}
        
        preliminary_candidates = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % main_filter_frame_size in active_slot_indices:
                    preliminary_candidates.append(p)
        
        if not preliminary_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No preliminary candidates"}

        # =================================================================================
        # 阶段 3 & 4: 启发式评分 与 动态统计剪枝
        # --- 交叉部分：融合父代A的“统计剪枝”思想与父代B的“评分”思想 ---
        # 首先，我们为每个候选者计算一个启发式分数，评分方法（非空时隙数）是两个父代
        # 均采用的鲁棒策略。然后，我们应用父代A中最具智慧的“统计剪枝”逻辑，即根据
        # 分数的均值和标准差来动态设定淘汰门槛，而非使用固定阈值。
        # 整个过程的“攻击性”（剪枝力度）由我们在突变部分引入的 `dynamic_pruning_factor` 控制。
        # =================================================================================
        price_to_score_map = {}
        for price in preliminary_candidates:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + SCORING_FRAME_SIZE.bit_length() + seed.bit_length()

            # --- [物理层模拟] ---
            slot_states = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SCORING_FRAME_SIZE
                    slot_states[slot_id] = 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1
            
            score = sum(slot_states)
            if score > 0:
                price_to_score_map[price] = score

        if not price_to_score_map:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores generated"}

        all_scores = list(price_to_score_map.values())
        finalists = []
        
        if len(all_scores) > 1:
            mean_score = statistics.mean(all_scores)
            stdev_score = statistics.stdev(all_scores) if len(all_scores) > 1 else 0
            score_threshold = mean_score + dynamic_pruning_factor * stdev_score
            finalists = [p for p, s in price_to_score_map.items() if s >= score_threshold]
        
        if not finalists:
            max_score = max(all_scores)
            finalists = [p for p, s in price_to_score_map.items() if s == max_score]

        finalists.sort(key=lambda p: price_to_score_map[p], reverse=True)

        # =================================================================================
        # 阶段 5: 精确验证
        # --- 交叉部分：采用父代A的“对数精确估计”方法 ---
        # 在最后阶段，我们采用了父代A中最精确的计数武器：对数估计算法。此方法能够
        # 透过时隙碰撞的“迷雾”，比简单地数非空时隙数更准确地估计出每个最终候选者
        # 的实际标签数量，为协议的最终裁决提供了最可靠的依据。
        # =================================================================================
        final_counts = {}
        max_verified_count = -1.0
        modes = []
        
        for price in finalists:
            # 帧大小根据其初步分数进行缩放，分数越高，分配的验证资源越多
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
            
            total_tag_bits_sent += frame_size * 1

            num_zeros = slot_states.count(0)
            verified_count = 0
            if num_zeros < frame_size: # 至少有一个标签响应
                if num_zeros == 0: # 帧全满，使用一个合理的较大估计，避免log(0)
                    verified_count = frame_size * 2.39 
                else:
                    verified_count = -frame_size * math.log(num_zeros / frame_size)
            
            final_counts[price] = verified_count

            # 使用一个小的容忍度来比较浮点数
            if verified_count > max_verified_count + 1e-9:
                max_verified_count = verified_count
                modes = [price]
            elif math.isclose(verified_count, max_verified_count, rel_tol=1e-5):
                modes.append(price)

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "diversity_metric": diversity_metric,
            "chosen_filter_frame_size": main_filter_frame_size,
            "chosen_pruning_factor": dynamic_pruning_factor,
            "preliminary_candidate_count": len(preliminary_candidates),
            "finalist_count": len(finalists),
            "final_counts": final_counts,
        }
        
        if not modes:
             return [], 0, total_time_ms, verification_data
        
        modes.sort()
        
        return modes, math.ceil(max_verified_count), total_time_ms, verification_data
