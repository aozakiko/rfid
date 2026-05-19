import math
import statistics
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
# The hash function is assumed to be in a file named "hash.py"
from hash import simple_hash

class g4_3(IProtocol):
    """
    子代协议: 混合启发式众数查询 (Hybrid Heuristic Protocol for Mode Query)

    本协议是一个为大规模RFID系统中的高效、准确众数查询而设计的全新子代协议。
    它严格遵守信息隔离原则，综合了两个父代协议的核心设计哲学，并引入了一项创新的优化机制。
    该协议通过分阶段的方法，逐步缩小候选集范围，最终精确地找出众数。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- Protocol Hyperparameters ---
        PRECHECK_BINS = 10  # 粗粒度筛选的桶数量
        PRECHECK_FRAME_SIZE = 128 # 粗粒度筛选的帧大小
        HEURISTIC_FRAME_SIZE = 64 # 启发式评分的帧大小
        VERIFICATION_FRAME_SCALE_FACTOR = 1.5 # 验证阶段帧大小的缩放因子
        MIN_VERIFICATION_FRAME_SIZE = 64 # 验证阶段的最小帧大小
        STATISTICAL_PRUNING_FACTOR = 1.5 # 统计剪枝的标准差倍数

        # =================================================================================
        # 阶段 1: 粗粒度范围剪枝 (Coarse-Grained Range Pruning)
        # --- 交叉部分：融合父代A的分阶段过滤思想与父代B的全范围扫描思想 ---
        # 此阶段借鉴了父代A的多阶段过滤框架。与父代B的逐一价格扫描不同，我们首先快速地
        # 将整个价格范围划分成若干个“桶”，并一次性地检查每个桶中是否存在标签。
        # 这样可以迅速排除大量没有任何标签存在的连续价格区间，显著减少后续处理的范围。
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
        # 在父代A的框架基础上，我们在此阶段用父代B的核心思想（使用非空时隙数作为启发式指标）
        # 替换了父代A原有的复杂数学估计。对第一阶段筛选出的每个活跃价格，我们都运行一个
        # 通信帧，并计算其“非空时隙数”作为评分。这个评分更稳定且计算成本更低，
        # 能有效地对候选价格的标签数量进行排序。
        # =================================================================================
        price_to_score_map = {}
        sorted_active_prices = sorted(list(active_prices))

        for price in sorted_active_prices:
            # --- 读写器行为: 广播对价格 p 的探测指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + HEURISTIC_FRAME_SIZE.bit_length() + seed.bit_length()

            # --- [物理层模拟] ---
            slot_states = [0] * HEURISTIC_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % HEURISTIC_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += HEURISTIC_FRAME_SIZE * 1

            # --- 读写器分析聚合信号 ---
            num_non_empty_slots = sum(1 for count in slot_states if count > 0)
            if num_non_empty_slots > 0:
                price_to_score_map[price] = num_non_empty_slots

        # =================================================================================
        # 阶段 3: 基于统计的动态剪枝 (Statistics-Based Dynamic Pruning)
        # --- 突变部分：引入“基于分数分布的动态候选集筛选”全新机制 ---
        # 这是对两个父代协议的全新优化。父代A验证固定数量的顶尖候选者，父代B则没有
        # 验证阶段。本协议引入了一个动态剪枝逻辑：我们首先计算所有候选价格启发式分数的
        # 均值和标准差，然后只将那些分数显著高于平均水平（例如，高于“均值 + k * 标准差”）
        # 的价格作为最终候选者。这种方法能根据当前数据的实际分布自适应地确定需要精确
        # 验证的候选集大小，避免了固定阈值带来的僵化，从而更智能地节省通信开销。
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
            stdev_score = statistics.stdev(all_scores)
            score_threshold = mean_score + STATISTICAL_PRUNING_FACTOR * stdev_score
            
            # 筛选出分数足够高的候选者
            candidates_for_verification = [
                price for price, score in price_to_score_map.items() if score >= score_threshold
            ]
        
        # 如果统计剪枝后没有候选者，或者候选者太少，则回退到选择分数最高的
        if not candidates_for_verification:
            max_score = max(all_scores)
            candidates_for_verification = [p for p, s in price_to_score_map.items() if s == max_score]

        # 按启发式分数从高到低排序，准备进行最终验证
        candidates_for_verification.sort(key=lambda p: price_to_score_map[p], reverse=True)

        # =================================================================================
        # 阶段 4: 自适应精确验证 (Adaptive & Precise Verification)
        # =================================================================================
        final_counts = {}
        max_verified_count = -1.0
        modes = []
        
        for price in candidates_for_verification:
            # --- 读写器行为: 广播指令 ---
            # 再次融合父代A的思想：根据前一阶段的结果（此处为启发式分数）自适应调整帧长
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

            # --- 读写器行为: 精确数量估计 ---
            num_zeros = slot_states.count(0)
            verified_count = 0
            if num_zeros == frame_size:
                verified_count = 0
            elif num_zeros == 0:
                # 帧全满，给一个较大的估计值
                verified_count = frame_size * 2.5 
            else:
                # 使用标准的对数估计算法
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
            "active_price_count_after_pruning": len(active_prices),
            "heuristic_scores": price_to_score_map,
            "candidates_after_statistical_pruning": candidates_for_verification,
            "final_verified_counts": final_counts,
        }
        
        if not modes:
             return [], 0, total_time_ms, verification_data
        
        modes.sort()
        
        return modes, math.ceil(max_verified_count), total_time_ms, verification_data
