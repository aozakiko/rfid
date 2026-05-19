import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_3(IProtocol):
    """
    混合剪枝与成本效益查询协议 (Hybrid Pruning & Cost-Benefit Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，通过一个多阶段的过滤与评分框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“成本效益提前终止”机制，以在保证高准确率的同时，实现对通信资源的智能化管理。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        # --- 时间计算与统计变量 ---
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        # --- 协议超参数 ---
        # 交叉部分参数
        PRECHECK_BINS = 16          # 范围剪枝阶段的分箱数 (源自父代A)
        FILTER_FRAME_SIZE = 1024    # 双哈希过滤阶段的帧大小 (源自父代B)
        INITIAL_SCORING_FRAME_SIZE = 64 # 迭代评分初始帧大小 (源自父代B)
        MAX_SCORING_ROUNDS = 5      # 迭代评分最大轮次 (源自父代B)

        # 突变部分参数
        COST_BENEFIT_THRESHOLD = 50.0 # 成本效益决策阈值 (全新)

        # --- 交叉部分：融合父代A的“范围分箱剪枝”思想与父代B的“双哈希协同过滤”思想 ---
        # 本协议的设计哲学是“先粗后精”。
        # 首先，采纳父代A(g8_1)中确定性的“范围分箱剪枝”策略，快速、低成本地识别出包含标签的“活跃价格区间”，
        # 从而排除掉大片无任何标签的价格范围。
        # 接着，在这些已确认的活跃区间内，我们借鉴父代B(g8_3)中更为精细的“双哈希协同过滤”思想，
        # 对所有标签进行两次全局哈希探测。一个价格最终要成为候选者，必须同时满足两个条件：
        # 1. 它所属的价格区间在第一步中被证明是“活跃的”。
        # 2. 它在两次全局哈希探测中均映射到非空时隙。
        # 这种“确定性范围筛选”与“概率性双重验证”的结合，能产生一个规模远小于任何单一父代方法、
        # 且质量极高的候选集，为后续的精确评分奠定坚实基础。

        # --- 阶段一：范围分箱剪枝 (源自父代A: g8_1) ---
        active_bins = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        
        for i in range(PRECHECK_BINS):
            p_min = price_min + i * bin_size
            p_max = min(p_min + bin_size - 1, price_max)
            if p_min > price_max:
                break
            
            total_frames += 1
            # 广播指令，仅需确认区间内有无标签，用一个极小的帧即可
            QUERY_FRAME_SIZE = 1 
            total_bits_sent_by_reader += p_min.bit_length() + p_max.bit_length() + QUERY_FRAME_SIZE.bit_length()
            
            # --- [物理层模拟] ---
            has_response = False
            for t in tags:
                if p_min <= t.price <= p_max:
                    has_response = True
                    break
            # --- [物理层模拟结束] ---
            total_slots += QUERY_FRAME_SIZE

            if has_response:
                active_bins.add(i)

        # --- 阶段二：双哈希过滤 (源自父代B: g8_3) ---
        # 第一次哈希
        total_frames += 1
        seed1 = generate_random_seed()
        total_bits_sent_by_reader += seed1.bit_length() + FILTER_FRAME_SIZE.bit_length()
        slots1 = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slots1[simple_hash(t.price, seed1) % FILTER_FRAME_SIZE] = 1
        total_slots += FILTER_FRAME_SIZE
        active_slots1 = {i for i, s in enumerate(slots1) if s > 0}

        # 第二次哈希
        total_frames += 1
        seed2 = generate_random_seed()
        total_bits_sent_by_reader += seed2.bit_length() + FILTER_FRAME_SIZE.bit_length()
        slots2 = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slots2[simple_hash(t.price, seed2) % FILTER_FRAME_SIZE] = 1
        total_slots += FILTER_FRAME_SIZE
        active_slots2 = {i for i, s in enumerate(slots2) if s > 0}

        # --- 读写器本地生成最终候选集 (交叉融合点) ---
        candidate_prices = []
        for p in range(price_min, price_max + 1):
            bin_index = math.floor((p - price_min) / bin_size)
            if bin_index in active_bins: # 条件1：价格在活跃区间内
                hash1_ok = (simple_hash(p, seed1) % FILTER_FRAME_SIZE) in active_slots1
                hash2_ok = (simple_hash(p, seed2) % FILTER_FRAME_SIZE) in active_slots2
                if hash1_ok and hash2_ok: # 条件2：通过双哈希验证
                    candidate_prices.append(p)
        
        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = max(0, (total_frames - 1) * 30)
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after hybrid pruning"}

        # --- 阶段三：迭代评分 (框架源自父代B: g8_3) ---
        current_contenders = candidate_prices
        current_scores = {}
        final_round_num = 0

        for round_num in range(MAX_SCORING_ROUNDS):
            final_round_num = round_num + 1
            
            # --- 突变部分：引入“成本效益提前终止”全新机制 ---
            # 两个父代协议的“早停”机制都仅基于候选者之间的“分数差异”。
            # 本协议引入一个全新的、更智能的决策维度：通信成本。
            # 在进入下一轮更精确（也更昂贵）的评分之前，读写器会进行一次成本效益分析。
            # 它会估算下一轮评分所需的通信开销（由竞争者数量决定），并将其与当前已确立的领先优势（分数差距）进行比较。
            # 如果“为拉开当前差距所需付出的预期成本”过高，意味着继续探测的边际效益很低，
            # 协议将果断终止，并接受当前的结果。这个机制避免了在多个候选者计数值非常接近时，
            # 系统依然投入大量资源进行“无效”的精确区分，体现了对通信资源更为精细的管理策略。
            if round_num > 0 and len(current_scores) >= 2:
                sorted_s = sorted(current_scores.values(), reverse=True)
                score_gap = sorted_s[0] - sorted_s[1]
                
                # 估算下一轮的成本：与竞争者数量正相关
                cost_of_next_round = len(current_contenders) * INITIAL_SCORING_FRAME_SIZE * (2**round_num)
                
                if score_gap > 0:
                    # 如果成本效益比超过阈值，则提前终止
                    if (cost_of_next_round / score_gap) > COST_BENEFIT_THRESHOLD:
                        break

            # 对本轮的竞争者进行探测
            probe_frame_size = INITIAL_SCORING_FRAME_SIZE * (2**round_num)
            for price in current_contenders:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + probe_frame_size.bit_length() + seed.bit_length()
                
                # [物理层模拟]
                slot_states = [0] * probe_frame_size
                for t in tags:
                    if t.price == price:
                        slot_states[simple_hash(t.id, seed) % probe_frame_size] = 1
                # [物理层模拟结束]
                total_slots += probe_frame_size
                current_scores[price] = sum(slot_states)
            
            if not current_scores or max(current_scores.values()) == 0:
                break

            sorted_scores = sorted(current_scores.items(), key=lambda item: item[1], reverse=True)
            if len(sorted_scores) < 2 or sorted_scores[1][1] == 0:
                break # 只有一个有效得分或其余均为0，可以终止

            # 准备下一轮 (源自父代B)
            if round_num < MAX_SCORING_ROUNDS - 1:
                second_max_score = sorted_scores[1][1]
                current_contenders = [p for p, s in current_scores.items() if s >= second_max_score]

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 1 * 0.4
        T_overhead = max(0, (total_frames - 1) * 30)
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Fused A's range pruning with B's dual-hash filtering and iterative scoring.",
            "mutation_info": f"Introduced Cost-Benefit Early Termination (Final Round: {final_round_num})",
            "initial_candidate_count": len(candidate_prices),
            "final_scores": {k: v for k, v in sorted(current_scores.items(), key=lambda item: item[1], reverse=True)}
        }
        
        if not current_scores:
            return [], 0, total_time_ms, verification_data
        
        max_final_score = max(current_scores.values())
        if max_final_score == 0:
            return [], 0, total_time_ms, verification_data
            
        modes = sorted([p for p, s in current_scores.items() if s == max_final_score])
        
        return modes, max_final_score, total_time_ms, verification_data
