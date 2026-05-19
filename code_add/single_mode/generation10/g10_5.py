import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_5(IProtocol):
    """
    混合自适应分组精炼协议 (Hybrid Adaptive Grouped Refinement Protocol)

    本协议是一个为解决大规模RFID众数查询问题而设计的全新子代协议。它严格遵循信息隔离原则，
    其核心设计体现了对两个父代协议思想的深度融合（交叉），并在此基础上引入了一项全新的、旨在
    应对高密度候选者场景的动态剪枝机制（突变），以在保证高准确率的前提下，进一步提升协议效率。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        # --- 协议超参数 ---
        # 阶段一：范围剪枝 (源自父代B)
        PRECHECK_BINS = 16

        # 阶段二：分组精炼 (交叉父代A的逻辑 + 突变机制)
        MAX_REFINEMENT_ROUNDS = 5
        GROUP_SIZE = 4  # (源自父代B)
        BASE_SUB_FRAME_SIZE = 48 # 基础子帧大小
        MAX_SUB_FRAME_SIZE = 1024 # 最大子帧大小

        # 交叉参数: 候选者晋级阈值 (源自父代A)
        COLLISION_PROMOTION_THRESHOLD = 0.25
        MIN_SCORE_FOR_COLLISION_PROMOTION = 10

        # 突变参数: 动态候选者剪枝
        CULLING_CANDIDATE_THRESHOLD = 200 # 触发剪枝的候选者数量阈值
        CULLING_COLLISION_THRESHOLD = 0.30 # 触发剪枝的平均碰撞率阈值
        CULLING_SCORE_RETENTION_RATIO = 0.95 # 在剪枝中保留的、得分至少为最高分95%的候选者

        # --- 交叉部分：融合父代A的“自适应评估”思想与父代B的“范围剪枝”及“分组探测”思想 ---
        # 阶段一：范围分箱剪枝 (源自父代B: g9_2)
        # 协议首先采用父代B中确定性强、能高效剔除无关价格区间的“范围分箱剪枝”策略。
        # 此方法能稳定地生成一个高质量的初始候选集，为后续的精炼阶段奠定基础。
        candidate_prices: Set[int] = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)

        for i in range(PRECHECK_BINS):
            p_min = price_min + i * bin_size
            p_max = min(p_min + bin_size - 1, price_max)
            if p_min > price_max:
                break

            total_frames += 1
            total_bits_sent_by_reader += (p_min.bit_length() + p_max.bit_length())

            # --- [物理层模拟] ---
            # 仅检查此价格区间内是否存在任何标签，这是一个1-bit的响应
            has_response_in_bin = any(p_min <= t.price <= p_max for t in tags)
            # --- [物理层模拟结束] ---
            total_slots += 1 # 简化为1个时隙来检测存在性

            if has_response_in_bin:
                for p in range(p_min, p_max + 1):
                    candidate_prices.add(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = max(0, (total_frames - 1) * 30)
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after range pruning"}

        # 阶段二：迭代式自适应分组精炼
        # 此阶段的核心框架继承自父代B(g9_2)的“分组探测”，以减少通信帧数(T_overhead)。
        # 但与父代B使用固定子帧大小不同，本协议融合了父代A(g8_2)的“自适应”思想，
        # 动态地调整每个组的子帧大小，以更精细地分配通信资源。
        # 晋级逻辑则采纳了父代A的精髓：最高分与高碰撞率者并行晋级。
        contenders = sorted(list(candidate_prices))
        round_results: Dict[int, Dict] = {}
        protocol_log = []

        for round_num in range(MAX_REFINEMENT_ROUNDS):
            if not contenders:
                protocol_log.append(f"Round {round_num+1}: No contenders left.")
                break

            protocol_log.append(f"Round {round_num+1}: Probing {len(contenders)} contenders.")
            current_round_results: Dict[int, Dict] = {}
            contender_groups = [contenders[i:i + GROUP_SIZE] for i in range(0, len(contenders), GROUP_SIZE)]

            for group in contender_groups:
                total_frames += 1
                seed = generate_random_seed()

                # 交叉核心：为每个组动态计算自适应子帧大小 (源自父代A的思想)
                sub_frame_size = BASE_SUB_FRAME_SIZE
                if round_num > 0:
                    # 基于组内成员在上一轮的最大表现来调整帧大小
                    max_prev_score_in_group = 0
                    price_with_max_score = -1
                    for price in group:
                        if price in round_results and round_results[price]['score'] > max_prev_score_in_group:
                            max_prev_score_in_group = round_results[price]['score']
                            price_with_max_score = price
                    
                    if max_prev_score_in_group > 0 and price_with_max_score != -1:
                        # 使用产生该分数的帧大小来进行估计
                        prev_frame_size = round_results[price_with_max_score]['frame_size']

                        # 关键修复：确保Eom-Lee估计器的分母为正
                        if prev_frame_size > max_prev_score_in_group:
                            # 使用Eom-Lee估计器来估算标签数量
                            denominator = prev_frame_size - max_prev_score_in_group
                            estimated_tags = prev_frame_size * math.log(prev_frame_size / (denominator + 1e-6))
                            optimal_size = int(estimated_tags * 2.0) # 使用更激进的乘数
                            sub_frame_size = min(MAX_SUB_FRAME_SIZE, max(BASE_SUB_FRAME_SIZE, optimal_size))
                        else:
                            # 如果帧已饱和 (score >= frame_size)，则激进地增加帧大小
                            sub_frame_size = prev_frame_size * 4
                        
                        # 确保新的帧大小不超过上限
                        sub_frame_size = min(MAX_SUB_FRAME_SIZE, sub_frame_size)

                combined_frame_size = len(group) * sub_frame_size
                bits_for_prices = sum(p.bit_length() for p in group)
                total_bits_sent_by_reader += (seed.bit_length() + sub_frame_size.bit_length() + bits_for_prices)

                # --- [物理层模拟] ---
                slot_states = [0] * combined_frame_size
                for t in tags:
                    if t.price in group:
                        price_index_in_group = group.index(t.price)
                        slot_offset = price_index_in_group * sub_frame_size
                        slot_id = slot_offset + (simple_hash(t.id, seed) % sub_frame_size)
                        slot_states[slot_id] += 1
                # --- [物理层模拟结束] ---
                total_slots += combined_frame_size

                for i, price in enumerate(group):
                    sub_frame_start = i * sub_frame_size
                    sub_frame_end = sub_frame_start + sub_frame_size
                    sub_frame_states = slot_states[sub_frame_start:sub_frame_end]
                    
                    score = sum(1 for c in sub_frame_states if c > 0)
                    collisions = sum(1 for c in sub_frame_states if c > 1)
                    collision_rate = collisions / sub_frame_size if sub_frame_size > 0 else 0
                    
                    current_round_results[price] = {
                        'score': score,
                        'collision_rate': collision_rate,
                        'frame_size': sub_frame_size
                    }

            round_results = current_round_results
            if not round_results:
                break
            
            max_score = max((res['score'] for res in round_results.values()), default=0)
            if max_score == 0:
                break

            # 晋级逻辑 (源自父代A)
            next_contenders = set()
            for price, result in round_results.items():
                if result['score'] == max_score:
                    next_contenders.add(price)
                if result['score'] >= MIN_SCORE_FOR_COLLISION_PROMOTION and result['collision_rate'] > COLLISION_PROMOTION_THRESHOLD:
                    next_contenders.add(price)

            # --- 突变部分：引入“基于碰撞饱和度的动态候选者剪枝”全新机制 ---
            # 两个父代协议均未包含此机制。父代A的提前退出是基于得分的“置信度”，而父代B没有复杂的退出机制。
            # 本协议引入一个全新的判断：在第一轮精炼后，如果候选者数量依然庞大，并且整体碰撞率非常高，
            # 这表明系统处于“饱和”状态，大量非众数候选者干扰了识别过程。
            # 此时，协议将采取激进的剪枝策略，仅保留得分最顶尖的一小部分候选者进入下一轮，
            # 从而快速收敛，将通信资源集中在最有可能成为众数的 kandidat 上。
            if round_num == 0 and len(contenders) > CULLING_CANDIDATE_THRESHOLD:
                if len(round_results) > 0:
                    avg_collision_rate = sum(res['collision_rate'] for res in round_results.values()) / len(round_results)
                    if avg_collision_rate > CULLING_COLLISION_THRESHOLD:
                        culling_score_threshold = max_score * CULLING_SCORE_RETENTION_RATIO
                        culled_contenders = {p for p in next_contenders if round_results[p]['score'] >= culling_score_threshold}
                        protocol_log.append(f"Mutation Triggered: Culled {len(next_contenders) - len(culled_contenders)} contenders due to high collision saturation.")
                        next_contenders = culled_contenders
            
            if not next_contenders:
                # 如果剪枝后或正常晋级后没有候选者，则使用上一轮的结果
                next_contenders = {p for p, res in round_results.items() if res['score'] == max_score}


            contenders = sorted(list(next_contenders))

        # --- 阶段三：最终裁决与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 1 * 0.4 # bits_per_slot is 1
        T_overhead = max(0, (total_frames - 1) * 30)
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Used Parent B's range pruning and grouped probing, fused with Parent A's adaptive frame sizing and dual-criteria promotion.",
            "mutation_info": "Introduced 'Dynamic Candidate Culling' based on collision saturation after the first refinement round to handle high-density scenarios.",
            "initial_candidate_count": len(candidate_prices),
            "final_contender_count": len(contenders),
            "log": protocol_log
        }
        
        if not round_results:
            return [], 0, total_time_ms, verification_data

        final_max_score = max((res['score'] for res in round_results.values()), default=0)
        if final_max_score == 0:
            return [], 0, total_time_ms, verification_data
        
        modes = sorted([p for p, res in round_results.items() if res['score'] == final_max_score])
        
        return modes, final_max_score, total_time_ms, verification_data

