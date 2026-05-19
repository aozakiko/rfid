import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_2(IProtocol):
    """
    混合分层过滤与前瞻性评分协议 (Hybrid Layered Filtering with Lookahead Scoring Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    协议采用一个三阶段的核心流程：
    1.  范围剪枝：快速排除大部分无标签的价格区间。
    2.  协同过滤：在活跃的价格区间内，通过双哈希机制精确生成候选价格集。
    3.  前瞻性评分：对候选价格进行精确评分，并引入一种全新的基于得分饱和度的前瞻性帧调整策略，以提高在高密度区域的探测准确性。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        """
        执行协议的核心逻辑。
        """
        # --- 时间计算与统计变量 ---
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots_replied = 0
        bits_per_slot = 1

        # --- 协议超参数 ---
        # 阶段一参数
        PRECHECK_BINS = 16  # 范围剪枝阶段的分箱数
        PRECHECK_FRAME_SIZE = 128 # 范围剪枝阶段的帧大小

        # 阶段二参数
        FILTER_FRAME_SIZE = 256 # 协同过滤阶段的帧大小

        # 阶段三参数
        INITIAL_SCORING_FRAME_SIZE = 128 # 评分阶段的初始帧大小
        MIN_FRAME_SIZE = 64              # 帧大小动态调整下限
        MAX_FRAME_SIZE = 1024            # 帧大小动态调整上限
        COLLISION_HIGH_THRESHOLD = 0.35  # 碰撞率高阈值，用于增大帧
        COLLISION_LOW_THRESHOLD = 0.1    # 碰撞率低阈值，用于减小帧
        SCORE_SATURATION_THRESHOLD = 0.9 # 触发“前瞻性帧提升”的得分饱和度阈值

        candidate_prices = set()

        # --- 交叉部分：融合父代A的“范围剪枝”思想与父代B的“双哈希协同过滤”思想 ---
        # 协议首先采用父代A(g2_1)的分箱策略，将整个价格范围划分为多个区间，进行宏观的、低成本的探测。
        # 这一步可以快速排除大量不存在任何标签的“死亡区域”，显著缩小后续操作的目标范围。
        # 接着，对于被识别为“活跃”的区间，协议不再像父代A那样对区间内所有价格逐一评分，
        # 而是借鉴并应用了父代B(g9_5)的核心机制——双哈希协同过滤，但将其作用域从全局缩小到了单个活跃区间内。
        # 这种“先粗后精”的混合分层过滤策略，结合了两种父代方法的优点，能够在早期阶段以极高效率生成一个规模小、质量高的最终候选集。

        # 阶段一: 范围剪枝 (Coarse-Grained Range Pruning)
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        active_bins = []

        for i in range(PRECHECK_BINS):
            bin_start = price_min + i * bin_size
            bin_end = min(price_min + (i + 1) * bin_size - 1, price_max)
            if bin_start > price_max:
                continue

            # --- 读写器行为: 广播指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() +
                                          bin_start.bit_length() + bin_end.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * PRECHECK_FRAME_SIZE
            for t in tags:
                if bin_start <= t.price <= bin_end:
                    slot_id = simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE
                    slot_states[slot_id] = 1 # 仅标记存在
            # --- [物理层模拟结束] ---
            total_slots_replied += PRECHECK_FRAME_SIZE

            # --- 读写器分析帧 ---
            if any(state > 0 for state in slot_states):
                active_bins.append((bin_start, bin_end))

        # 阶段二: 协同过滤 (Localized Collaborative Filtering)
        for bin_start, bin_end in active_bins:
            # --- 第一次哈希 ---
            total_frames += 1
            seed1 = generate_random_seed()
            total_bits_sent_by_reader += (seed1.bit_length() + FILTER_FRAME_SIZE.bit_length() +
                                          bin_start.bit_length() + bin_end.bit_length())
            
            slots1 = [0] * FILTER_FRAME_SIZE
            for t in tags:
                if bin_start <= t.price <= bin_end:
                    slot_id = simple_hash(t.price, seed1) % FILTER_FRAME_SIZE
                    slots1[slot_id] = 1
            total_slots_replied += FILTER_FRAME_SIZE
            active_slots1 = {i for i, s in enumerate(slots1) if s > 0}

            # --- 第二次哈希 ---
            total_frames += 1
            seed2 = generate_random_seed()
            total_bits_sent_by_reader += (seed2.bit_length() + FILTER_FRAME_SIZE.bit_length() +
                                          bin_start.bit_length() + bin_end.bit_length())

            slots2 = [0] * FILTER_FRAME_SIZE
            for t in tags:
                if bin_start <= t.price <= bin_end:
                    slot_id = simple_hash(t.price, seed2) % FILTER_FRAME_SIZE
                    slots2[slot_id] = 1
            total_slots_replied += FILTER_FRAME_SIZE
            active_slots2 = {i for i, s in enumerate(slots2) if s > 0}

            # --- 读写器本地生成候选者 ---
            for p in range(bin_start, bin_end + 1):
                if (simple_hash(p, seed1) % FILTER_FRAME_SIZE) in active_slots1 and \
                   (simple_hash(p, seed2) % FILTER_FRAME_SIZE) in active_slots2:
                    candidate_prices.add(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots_replied * bits_per_slot * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after filtering"}

        # 阶段三: 精确评分 (Precise Scoring)
        price_to_score_map = {}
        current_frame_size = INITIAL_SCORING_FRAME_SIZE
        sorted_candidates = sorted(list(candidate_prices))

        # --- 突变部分：引入“基于得分饱和度的前瞻性帧提升”全新机制 ---
        # 两个父代协议都只根据*当前*价格的碰撞率去调整*下一个*价格的帧大小，这是一种“事后反应”策略。
        # 本协议引入一项全新的“事前预判”机制：在对有序的候选价格进行评分时，如果读写器发现当前价格P_i的
        # 得分（非空时隙数）极其接近当前帧大小（例如超过90%），这强烈暗示着帧时隙已“饱和”，
        # 大量标签被压缩在有限的时隙中，真实的标签数量可能远超当前得分。
        # 更重要的是，由于价格的局部聚集性，下一个邻近的价格P_i+1也极有可能是个高计数值。
        # 此时，协议会进入“前瞻模式”，在为P_i+1计算常规帧大小后，额外进行一次“前瞻性提升”，
        # 主动、大幅地增加其探测帧，以期在第一次探测时就获得一个更接近真相的、未饱和的得分。
        # 这种基于“得分饱和度”而非“碰撞率”的预判机制，是两个父代都未曾有过的，它能更有效地应对“超级众数”
        # 及其邻近值的探测，避免因得分饱和导致“超级众数”的评分低于邻近值而被错误淘汰。
        predictive_boost = False

        for price in sorted_candidates:
            frame_size_for_this_round = current_frame_size
            if predictive_boost:
                frame_size_for_this_round = min(MAX_FRAME_SIZE, int(frame_size_for_this_round * 1.5))
                predictive_boost = False # 提升仅生效一次

            # --- 读写器行为: 广播评分指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + frame_size_for_this_round.bit_length() + seed.bit_length()

            # --- [物理层模拟] ---
            slot_states = [0] * frame_size_for_this_round
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % frame_size_for_this_round
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            total_slots_replied += frame_size_for_this_round

            # --- 读写器分析帧 ---
            num_non_empty_slots = sum(1 for c in slot_states if c > 0)
            num_collisions = sum(1 for c in slot_states if c > 1)
            price_to_score_map[price] = num_non_empty_slots

            # --- 执行突变逻辑：检查得分饱和度 ---
            if num_non_empty_slots / frame_size_for_this_round > SCORE_SATURATION_THRESHOLD:
                predictive_boost = True

            # 基于碰撞率，为下一个价格动态调整基础帧大小 (继承自父代)
            collision_rate = num_collisions / frame_size_for_this_round
            if collision_rate > COLLISION_HIGH_THRESHOLD:
                current_frame_size = min(MAX_FRAME_SIZE, int(current_frame_size * 1.5))
            elif collision_rate < COLLISION_LOW_THRESHOLD:
                current_frame_size = max(MIN_FRAME_SIZE, int(current_frame_size * 0.75))
        
        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots_replied * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        if not price_to_score_map:
            return [], 0, total_time_ms, {"reason": "Scoring phase yielded no results"}

        max_score = max(price_to_score_map.values())
        modes = sorted([p for p, s in price_to_score_map.items() if s == max_score])

        verification_data = {
            "crossover_info": "Used Parent A's binning for coarse pruning, then Parent B's dual-hash for fine filtering within active bins.",
            "mutation_info": f"Introduced Lookahead Frame Boost based on score saturation (threshold: {SCORE_SATURATION_THRESHOLD}).",
            "active_bin_count": len(active_bins),
            "final_candidate_count": len(candidate_prices),
            "final_scores": price_to_score_map
        }

        return modes, max_score, total_time_ms, verification_data
