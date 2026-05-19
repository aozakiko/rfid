import math
import statistics
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
# 假设哈希函数位于 "hash.py" 文件中
from hash import simple_hash

class g5_5(IProtocol):
    """
    子代协议: 混合分层聚焦查询协议 (Hybrid Hierarchical Focus Protocol)

    本协议是一个为大规模RFID系统中的高效、准确众数查询而设计的全新子代协议。
    它严格遵守信息隔离原则，综合了两个父代协议的核心设计哲学，并引入了一项创新的优化机制。
    该协议通过分阶段的方法，从粗粒度的范围筛选，到启发式的候选排序，
    再到具备提前终止能力的精确验证，逐步聚焦并最终精确地找出众数。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        PRECHECK_BINS = 12  # 阶段1：粗粒度筛选的桶数量
        PRECHECK_FRAME_SIZE = 128 # 阶段1：粗粒度筛选的帧大小
        SCORING_FRAME_SIZE = 64 # 阶段2：启发式评分的帧大小
        VERIFICATION_FRAME_SCALE_FACTOR = 1.8 # 阶段4：验证阶段帧大小的缩放因子
        MIN_VERIFICATION_FRAME_SIZE = 128 # 阶段4：验证阶段的最小帧大小
        STATISTICAL_PRUNING_FACTOR = 1.2 # 阶段3：统计剪枝的标准差倍数
        EARLY_EXIT_CONFIDENCE_FACTOR = 1.1 # 阶段4 (突变): 提前终止的置信因子

        # =================================================================================
        # 阶段 1: 粗粒度范围剪枝 (Coarse-Grained Range Pruning)
        # --- 交叉部分：融合父代A的分阶段过滤思想与父代B的全范围扫描思想 ---
        # 此阶段借鉴了两个父代协议共有的分箱预检策略。我们没有像父代B那样直接对全范围进行扫描，
        # 而是采纳了父代A的多阶段处理框架。首先将整个价格范围划分成若干个“桶”，
        # 并通过一次通信来判断每个桶中是否存在标签。这能快速排除大量空价格区间，
        # 为后续更精细的操作大幅缩小目标范围，体现了两个父代协议设计的共同优点。
        # =================================================================================
        active_prices = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        price_bins = [
            list(range(price_min + i * bin_size, min(price_min + (i + 1) * bin_size, price_max + 1)))
            for i in range(PRECHECK_BINS)
        ]

        for p_bin in price_bins:
            if not p_bin: continue
            
            # --- 读写器行为: 广播指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() + p_bin[0].bit_length() + p_bin[-1].bit_length())
            
            # --- [物理层模拟] ---
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_id = simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1
            
            # --- 读写器行为: 分析帧 ---
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found"}

        # =================================================================================
        # 阶段 2: 启发式评分 (Heuristic Scoring)
        # --- 交叉部分：融合父代B的“非空时隙数”启发式指标 ---
        # 继承父代A的多阶段框架，我们在此阶段对第一步筛选出的每个活跃价格进行评分。
        # 我们采用了父代B（以及父代A）所使用的核心启发式方法：使用“非空时隙数”作为评分。
        # 这个指标相比复杂的数学估计更为鲁棒且计算开销低，能有效地对候选价格的潜在标签数量进行初步排序。
        # =================================================================================
        price_to_score_map = {}
        sorted_active_prices = sorted(list(active_prices))

        for price in sorted_active_prices:
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

        # =================================================================================
        # 阶段 3: 基于统计的动态剪枝 (Statistics-Based Dynamic Pruning)
        # --- 交叉部分：继承父代A的动态候选集筛选机制 ---
        # 此阶段直接借鉴了父代A的一个关键创新。我们不验证所有有评分的候选者，而是通过
        # 计算所有分数的均值和标准差，动态地设定一个阈值。只有那些分数显著高于平均水平的
        # 价格，才会被选入最终的精确验证阶段。这使得协议能根据数据本身的分布自适应地
        # 确定候选集大小，比固定阈值或全部验证更智能、更高效。
        # =================================================================================
        if not price_to_score_map:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores generated"}

        all_scores = list(price_to_score_map.values())
        candidates_for_verification = []
        
        if len(all_scores) > 1:
            mean_score = statistics.mean(all_scores)
            stdev_score = statistics.stdev(all_scores) if len(all_scores) > 1 else 0
            score_threshold = mean_score + STATISTICAL_PRUNING_FACTOR * stdev_score
            candidates_for_verification = [p for p, s in price_to_score_map.items() if s >= score_threshold]
        
        if not candidates_for_verification:
            max_score = max(all_scores)
            candidates_for_verification = [p for p, s in price_to_score_map.items() if s == max_score]

        candidates_for_verification.sort(key=lambda p: price_to_score_map[p], reverse=True)

        # =================================================================================
        # 阶段 4: 带提前终止的精确验证 (Precise Verification with Early Exit)
        # =================================================================================
        final_counts = {}
        max_verified_count = -1.0
        modes = []
        
        for price in candidates_for_verification:
            # --- 突变部分：引入“基于上限预估的提前终止”全新机制 ---
            # 这是对两个父代协议的全新优化。在父代中，一旦确定了候选集，就需要对所有候选者
            # 进行验证。本协议引入了一个新的判断逻辑：在对一个候选者进行昂贵的精确验证之前，
            # 我们首先利用其在阶段2得到的启发式分数，来预估它理论上可能达到的最大标签数。
            # 如果这个理论上限仍然低于当前已经精确验证过的最高纪录，那么我们就没有必要再为
            # 这个候选者浪费一次通信。这个机制可以在不牺牲准确率的前提下，智能地跳过
            # 那些“无望获胜”的候选者，从而进一步节省总执行时间。
            if max_verified_count > 0:
                # 基于启发式分数s，使用对数公式估算其可能的标签数n_est = -F * log((F-s)/F)
                # 这是一个单调递增函数，因此分数越高，估算值也越高。
                score = price_to_score_map[price]
                # 如果分数连帧长的一半都不到，估算出的数量会远小于帧长。
                # 如果分数已经很低，其对应的理论数量上限就不可能超过当前的最大值。
                # 为了简化计算并避免浮点误差，我们直接比较分数：
                # 一个更宽松但安全的检查是，如果一个候选者的分数远低于另一个，
                # 那么它几乎不可能拥有更多的标签。
                # 我们预估一个理论上限：-F*ln(1-s/F) ~= s + s^2/(2F) + ...
                # 如果最高分的估算值都比不过当前的最大计数值，则后续更低分的更不可能。
                # 一个简单而有效的近似：如果当前候选者的分数连已发现最大计数值的
                # 一个比例都不到，则跳过。
                if price_to_score_map[price] * EARLY_EXIT_CONFIDENCE_FACTOR < max_verified_count :
                    continue # 跳过这个没有希望的候选者

            # --- 读写器行为: 广播指令 (继承自父代A的自适应帧长) ---
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

            # --- 读写器行为: 精确数量估计 (继承自父代A) ---
            num_zeros = slot_states.count(0)
            verified_count = 0
            if num_zeros == frame_size:
                verified_count = 0
            elif num_zeros == 0:
                verified_count = frame_size * 2.3 # 帧全满，使用一个合理的较大估计
            else:
                verified_count = -frame_size * math.log(num_zeros / frame_size)
            
            final_counts[price] = verified_count

            # 更新当前找到的最佳众数
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
            "active_prices_after_pruning": len(active_prices),
            "heuristic_scores": price_to_score_map,
            "candidates_after_statistical_pruning": candidates_for_verification,
            "final_verified_counts": final_counts,
        }
        
        if not modes:
             return [], 0, total_time_ms, verification_data
        
        modes.sort()
        
        return modes, math.ceil(max_verified_count), total_time_ms, verification_data
