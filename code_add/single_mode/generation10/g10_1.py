import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g10_1(IProtocol):
    """
    显性候选者提前终止的融合协议 (Fused Protocol with Dominant Candidate Early Exit)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并通过交叉与突变，形成了一个全新的解决方案。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        # --- 协议超参数 ---
        # 阶段一参数
        PRECHECK_BINS = 16
        PRECHECK_FRAME_SIZE = 64

        # 阶段二参数 (交叉继承)
        MAX_REFINEMENT_ROUNDS = 5
        GROUP_SIZE = 4
        SUB_FRAME_SIZE = 64
        COLLISION_PROMOTION_THRESHOLD = 0.20
        MIN_SCORE_FOR_COLLISION_PROMOTION = 10

        # 突变引入的全新参数
        DOMINANCE_FACTOR = 2.5 # 显性因子，用于提前终止

        # --- 交叉部分：融合父代B的“迭代式分组精炼”框架与父代A的“非空时隙数”精确评分思想 ---
        # 阶段一：范围分箱剪枝 (继承自父代B的结构，思想共通)
        # 采用父代B中高效的“范围分箱剪枝”策略，快速生成高质量的初始候选集。
        candidate_prices: Set[int] = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)

        for i in range(PRECHECK_BINS):
            p_min = price_min + i * bin_size
            p_max = min(p_min + bin_size - 1, price_max)
            if p_min > price_max:
                break

            total_frames += 1
            # 读写器广播范围和帧参数
            total_bits_sent_by_reader += (p_min.bit_length() + p_max.bit_length() + PRECHECK_FRAME_SIZE.bit_length())

            # --- [物理层模拟] ---
            # 检查此价格区间内是否存在任何标签
            has_response_in_bin = False
            for t in tags:
                if p_min <= t.price <= p_max:
                    has_response_in_bin = True
                    break
            # --- [物理层模拟结束] ---
            total_slots += PRECHECK_FRAME_SIZE 

            if has_response_in_bin:
                candidate_prices.update(range(p_min, p_max + 1))

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = max(0, (total_frames - 1) * 30)
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after range pruning"}

        # 阶段二：迭代式分组精炼 (继承自父代B的框架)
        # 采用多轮迭代、逐步筛选的“锦标赛”模式。探测方式采用父代B中高效的
        # “分组探测”机制以减少通信帧开销。晋级逻辑同样采纳了父代B的精髓：
        # 保留最高分和高碰撞率的潜在竞争者。
        contenders = sorted(list(candidate_prices))
        final_results: Dict[int, Dict] = {}

        for round_num in range(MAX_REFINEMENT_ROUNDS):
            if len(contenders) <= 1: # 如果只剩一个或零个竞争者，则终止
                break
            
            current_round_results: Dict[int, Dict] = {}
            contender_groups = [contenders[i:i + GROUP_SIZE] for i in range(0, len(contenders), GROUP_SIZE)]

            for group in contender_groups:
                total_frames += 1
                seed = generate_random_seed()
                combined_frame_size = len(group) * SUB_FRAME_SIZE
                
                bits_for_prices = sum(p.bit_length() for p in group)
                total_bits_sent_by_reader += (seed.bit_length() + SUB_FRAME_SIZE.bit_length() + bits_for_prices)

                # --- [物理层模拟] ---
                slot_states = [0] * combined_frame_size
                for t in tags:
                    if t.price in group:
                        price_index_in_group = group.index(t.price)
                        slot_offset = price_index_in_group * SUB_FRAME_SIZE
                        slot_id = slot_offset + (simple_hash(t.id, seed) % SUB_FRAME_SIZE)
                        slot_states[slot_id] += 1
                # --- [物理层模拟结束] ---
                total_slots += combined_frame_size

                # 读写器分析组合帧
                for i, price in enumerate(group):
                    sub_frame_start = i * SUB_FRAME_SIZE
                    sub_frame_end = sub_frame_start + SUB_FRAME_SIZE
                    sub_frame_states = slot_states[sub_frame_start:sub_frame_end]
                    
                    # 核心评分逻辑采用父代A鲁棒的“非空时隙数”作为标准
                    score = sum(1 for count in sub_frame_states if count > 0)
                    collisions = sum(1 for count in sub_frame_states if count > 1)
                    collision_rate = collisions / SUB_FRAME_SIZE if SUB_FRAME_SIZE > 0 else 0
                    
                    current_round_results[price] = {
                        'score': score,
                        'collision_rate': collision_rate,
                    }
            
            final_results = current_round_results
            if not final_results:
                break
            
            # --- 突变部分：引入“显性候选者提前终止”全新机制 ---
            # 在两个父代协议中，都需要运行完固定的轮次或者直到候选者数量收敛。
            # 本协议引入一个全新的判断逻辑：在每一轮精炼结束后，如果得分最高的候选者
            # 的分数，显著高于（例如超过2.5倍）第二高的候选者，那么我们有极大的把握
            # 认为它就是最终的众数。此时，协议将提前终止，无需进行后续无效的
            # 精炼轮次，从而在保证准确率的前提下，大幅节约了通信时间。
            scores_list = sorted([res['score'] for res in final_results.values()], reverse=True)
            if len(scores_list) > 1:
                max_score = scores_list[0]
                second_max_score = scores_list[1]
                if max_score > 0 and second_max_score > 0 and max_score >= second_max_score * DOMINANCE_FACTOR:
                    break # 显性候选者出现，提前终止
                elif max_score > 0 and second_max_score == 0:
                    break # 唯一胜出者，提前终止

            # 使用父代B的晋级逻辑筛选下一轮竞争者
            max_score_in_round = max((res['score'] for res in final_results.values()), default=0)
            if max_score_in_round == 0:
                break

            next_contenders = set()
            for price, result in final_results.items():
                if result['score'] == max_score_in_round:
                    next_contenders.add(price)
                if result['score'] >= MIN_SCORE_FOR_COLLISION_PROMOTION and result['collision_rate'] > COLLISION_PROMOTION_THRESHOLD:
                    next_contenders.add(price)
            
            contenders = sorted(list(next_contenders))

        # --- 最终裁决与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 1 * 0.4 # bits_per_slot is 1
        T_overhead = max(0, (total_frames - 1) * 30)
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Used Parent B's iterative refinement framework and grouped probing, with Parent A's robust non-empty slot scoring.",
            "mutation_info": "Introduced 'Dominant Candidate Early Exit' to terminate rounds when a clear winner emerges.",
            "initial_candidate_count": len(candidate_prices),
            "final_results": final_results
        }
        
        if not final_results:
             verification_data["final_contender_count"] = 0
             return [], 0, total_time_ms, verification_data

        # 在最后一轮的结果中找到分数最高的众数
        final_max_score = max((res['score'] for res in final_results.values()), default=0)
        
        if final_max_score == 0:
            verification_data["final_contender_count"] = len(final_results)
            return [], 0, total_time_ms, verification_data
        
        modes = sorted([p for p, res in final_results.items() if res['score'] == final_max_score])
        verification_data["final_contender_count"] = len(modes)
        
        return modes, final_max_score, total_time_ms, verification_data
