import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_3(IProtocol):
    """
    融合自适应协议 (Fused Adaptive Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，通过一个多阶段的过滤与评分框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“基于过滤密度的自适应帧长”机制，以动态优化协议在不同数据分布下的性能。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        # --- 时间计算与统计变量 ---
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1 # 标签每次只回复1比特表示存在性

        # --- 协议超参数 ---
        # 交叉部分参数 (Inspired by Parents)
        FILTER_FRAME_SIZE_1 = 1024  # 父代B: 过滤阶段哈希帧1大小
        FILTER_FRAME_SIZE_2 = 1024  # 父代B: 过滤阶段哈希帧2大小
        PROGRESSIVE_SCORING_MAX_ITER = 4 # 父代A: 渐进式评分最大迭代次数
        SATURATION_THRESHOLD = 0.95    # 父代A: 帧饱和度阈值
        EARLY_EXIT_CONFIDENCE_FACTOR = 2.0 # 父代A: 显性候选判定因子
        TIEBREAKER_FRAME_SIZE = 4096   # 父代A: 决胜局帧大小

        # 突变部分参数 (Newly introduced)
        DENSITY_HIGH_THRESHOLD = 0.4   # 用于判断过滤帧是否“高密度”的阈值
        DENSITY_LOW_THRESHOLD = 0.1    # 用于判断过滤帧是否“低密度”的阈值
        BASE_SCORING_FRAME_SIZE = 64   # 评分阶段的基准帧大小

        # --- 交叉部分：融合父代B(g9_5)的“双哈希协同过滤”思想与父代A(g8_1)的“渐进式评分+决胜局”架构 ---
        # 本协议首先采纳了父代B(g9_5)中高效且精准的“双哈希协同过滤”策略来生成候选集。
        # 相比父代A(g8_1)的范围分箱，这种方法能生成一个规模更小、质量更高的候选价格集，
        # 极大地减少了后续评分阶段的工作量。
        # 在获得候选集后，协议切换到父代A(g8_1)的“渐进式评分”、“显性候选提前终止”和“决胜局”的后端处理架构。
        # 这种架构对候选者进行逐级详查，既能通过提前终止来高效处理分布悬殊的场景，
        # 又能通过决胜局来精确裁定得分接近的竞争者，保证了结果的鲁棒性。

        # 阶段一：双哈希协同过滤 (源自父代B: g9_5)
        # --- 第一次哈希过滤 ---
        total_frames += 1
        filter_seed_1 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_1.bit_length() + FILTER_FRAME_SIZE_1.bit_length()

        # [物理层模拟]
        filtering_slots_1 = [0] * FILTER_FRAME_SIZE_1
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_1) % FILTER_FRAME_SIZE_1
            filtering_slots_1[slot_id] = 1
        # [物理层模拟结束]
        total_slots += FILTER_FRAME_SIZE_1
        active_slot_indices_1 = {i for i, state in enumerate(filtering_slots_1) if state > 0}

        # --- 第二次哈希过滤 ---
        total_frames += 1
        filter_seed_2 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_2.bit_length() + FILTER_FRAME_SIZE_2.bit_length()

        # [物理层模拟]
        filtering_slots_2 = [0] * FILTER_FRAME_SIZE_2
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_2) % FILTER_FRAME_SIZE_2
            filtering_slots_2[slot_id] = 1
        # [物理层模拟结束]
        total_slots += FILTER_FRAME_SIZE_2
        active_slot_indices_2 = {i for i, state in enumerate(filtering_slots_2) if state > 0}

        # --- 读写器本地生成最终候选集 ---
        candidate_prices = []
        if active_slot_indices_1 and active_slot_indices_2:
            for p in range(price_min, price_max + 1):
                hash1_in_set = (simple_hash(p, filter_seed_1) % FILTER_FRAME_SIZE_1) in active_slot_indices_1
                hash2_in_set = (simple_hash(p, filter_seed_2) % FILTER_FRAME_SIZE_2) in active_slot_indices_2
                if hash1_in_set and hash2_in_set:
                    candidate_prices.append(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * bits_per_slot * 0.4
            T_overhead = max(0, (total_frames - 1) * 30)
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after dual-hash filtering"}

        # --- 突变部分：引入“基于过滤密度的自适应初始帧长”全新机制 ---
        # 两个父代协议在进入评分阶段时，都采用一个固定的初始帧大小，缺乏对全局数据密度的感知。
        # 本协议引入一个全新的自适应机制：在双哈希过滤后，读写器会首先计算两个过滤帧的“填充密度”。
        # 这个密度值可以被视为一个关于环境中标签价格分布“拥挤程度”的廉价指标。
        # 协议将依据这个密度动态地调整后续渐进式评分的“初始帧大小”。
        # 如果密度高，说明可能存在一个数量庞大的众数，协议会采用一个更大的初始帧来避免首轮即饱和；
        # 如果密度低，则采用一个更小的初始帧来节约时隙资源。
        # 这项突变在过滤和评分两个核心阶段之间建立了一条动态的信息反馈链路，提升了协议的整体适应性。
        density1 = len(active_slot_indices_1) / FILTER_FRAME_SIZE_1
        density2 = len(active_slot_indices_2) / FILTER_FRAME_SIZE_2
        avg_density = (density1 + density2) / 2.0

        adaptive_start_frame_size = BASE_SCORING_FRAME_SIZE
        if avg_density > DENSITY_HIGH_THRESHOLD:
            adaptive_start_frame_size = BASE_SCORING_FRAME_SIZE * 2
        elif avg_density < DENSITY_LOW_THRESHOLD:
            adaptive_start_frame_size = BASE_SCORING_FRAME_SIZE // 2
        
        adaptive_start_frame_size = max(16, adaptive_start_frame_size) # 确保帧长不会过小

        # 阶段二：渐进式评分 (源自父代A: g8_1)
        progressive_scores = {}
        for price in sorted(candidate_prices):
            current_frame_size = adaptive_start_frame_size
            score = 0
            
            for _ in range(PROGRESSIVE_SCORING_MAX_ITER):
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + current_frame_size.bit_length() + seed.bit_length())

                # [物理层模拟]
                slot_states = [0] * current_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % current_frame_size
                        slot_states[slot_id] = 1 # 只关心时隙是否非空
                # [物理层模拟结束]
                total_slots += current_frame_size
                
                score = sum(slot_states)
                
                # 如果帧未饱和，说明分数可信，跳出迭代
                if score < current_frame_size * SATURATION_THRESHOLD:
                    break
                
                # 否则，帧可能因碰撞而饱和，加倍帧长重试
                current_frame_size *= 2
            
            progressive_scores[price] = score
        
        if not progressive_scores:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * bits_per_slot * 0.4
            T_overhead = max(0, (total_frames - 1) * 30)
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores in progressive scoring"}

        # 阶段三：显性候选提前终止 (源自父代A: g8_1)
        sorted_scores = sorted(progressive_scores.items(), key=lambda item: item[1], reverse=True)
        
        if len(sorted_scores) >= 2:
            top_score = sorted_scores[0][1]
            second_score = sorted_scores[1][1]
            if top_score > 0 and top_score >= second_score * EARLY_EXIT_CONFIDENCE_FACTOR:
                max_score = top_score
                modes = sorted([p for p, s in progressive_scores.items() if s == max_score])
                
                T_reader = total_bits_sent_by_reader * 0.025
                T_tag = total_slots * bits_per_slot * 0.4
                T_overhead = max(0, (total_frames - 1) * 30)
                total_time_ms = T_reader + T_tag + T_overhead
                
                verification_data = {
                    "mutation_info": f"Adaptive start frame size set to {adaptive_start_frame_size} based on filter density {avg_density:.2f}.",
                    "decision_info": f"Early exit triggered. Top score {top_score} vs second score {second_score}.",
                    "initial_candidate_count": len(candidate_prices),
                    "final_stage": "Skipped (Early Exit)"
                }
                return modes, max_score, total_time_ms, verification_data

        # 阶段四：决胜局 (源自父代A: g8_1)
        max_progressive_score = sorted_scores[0][1]
        finalists = [p for p, s in progressive_scores.items() if s == max_progressive_score]
        
        final_scores = {p:s for p,s in progressive_scores.items()} # 用旧分数初始化

        if len(finalists) > 1:
            # 对所有并列第一的候选者进行一次大规模、高精度的决胜局探测
            for price in finalists:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + TIEBREAKER_FRAME_SIZE.bit_length() + seed.bit_length())
                
                # [物理层模拟]
                slot_states = [0] * TIEBREAKER_FRAME_SIZE
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % TIEBREAKER_FRAME_SIZE
                        slot_states[slot_id] = 1
                # [物理层模拟结束]
                total_slots += TIEBREAKER_FRAME_SIZE
                final_scores[price] = sum(slot_states) # 用更精确的分数更新

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = max(0, (total_frames - 1) * 30)
        total_time_ms = T_reader + T_tag + T_overhead

        if not final_scores:
            return [], 0, total_time_ms, {"reason": "Empty final scores"}

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])
        
        verification_data = {
            "mutation_info": f"Adaptive start frame size set to {adaptive_start_frame_size} based on filter density {avg_density:.2f}.",
            "decision_info": "Early exit not triggered. Proceeded to final tiebreaker.",
            "initial_candidate_count": len(candidate_prices),
            "finalists": finalists if len(finalists) > 1 else "N/A",
            "tiebreaker_scores": {p:s for p,s in final_scores.items() if p in finalists} if len(finalists) > 1 else "Not needed"
        }
        
        return modes, max_score, total_time_ms, verification_data
