import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_2(IProtocol):
    """
    混合分组精炼众数查询协议 (Hybrid Grouped Refinement Protocol)

    本协议是一个为解决大规模RFID众数查询问题而设计的全新子代协议。它严格遵循信息隔离原则，
    其核心设计体现了对两个父代协议思想的深度融合（交叉），并在此基础上引入了一项全新的通信
    优化机制（突变），旨在保证高准确率的同时，大幅降低通信开销。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        # --- 协议超参数 ---
        # 阶段一：范围剪枝 (源自父代B)
        PRECHECK_BINS = 16
        PRECHECK_FRAME_SIZE = 64 # 用于确认区间是否非空

        # 阶段二：分组精炼 (交叉父代A的逻辑 + 突变机制)
        MAX_REFINEMENT_ROUNDS = 4
        # 突变参数: 将多个候选者分组到单帧中进行探测
        GROUP_SIZE = 4 
        SUB_FRAME_SIZE = 64 # 分配给组内每个候选者的子帧大小
        
        # 交叉参数: 候选者晋级阈值 (源自父代A)
        COLLISION_PROMOTION_THRESHOLD = 0.20 # 认定为高碰撞的阈值
        MIN_SCORE_FOR_COLLISION_PROMOTION = 10 # 满足高碰撞晋级所需的最低分

        # --- 交叉部分：融合父代A的“迭代式竞争晋级”思想与父代B的“范围分箱剪枝”思想 ---
        # 阶段一：范围分箱剪枝 (源自父代B: g8_1)
        # 协议首先采用父代B中确定性强、能高效剔除无关价格区间的“范围分箱剪枝”策略。
        # 相比父代A的概率性哈希过滤，此方法能更稳定地生成一个高质量的初始候选集。
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
            # 仅检查此价格区间内是否存在任何标签
            has_response_in_bin = False
            for t in tags:
                if p_min <= t.price <= p_max:
                    has_response_in_bin = True
                    break
            # --- [物理层模拟结束] ---
            total_slots += PRECHECK_FRAME_SIZE # 即使只检查存在性，也消耗了帧的通信时间

            if has_response_in_bin:
                for p in range(p_min, p_max + 1):
                    candidate_prices.add(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * 1 * 0.4
            T_overhead = max(0, (total_frames - 1) * 30)
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after range pruning"}

        # 阶段二：迭代式分组精炼
        # 此阶段的核心框架继承自父代A(g8_2)，采用多轮迭代、逐步筛选的“锦标赛”模式。
        # 晋级逻辑同样采纳了父代A的精髓：不仅最高分可以晋级，高碰撞率的潜在竞争者也会被保留，
        # 以防因碰撞过多而被错误淘汰。
        contenders = sorted(list(candidate_prices))
        round_results: Dict[int, Dict] = {}

        for round_num in range(MAX_REFINEMENT_ROUNDS):
            if not contenders:
                break
            
            current_round_results: Dict[int, Dict] = {}
            
            # --- 突变部分：引入“候选者分组探测”全新机制 ---
            # 两个父代协议在评分阶段都是对每个候选价格单独进行一次或多次通信探测，当候选者众多时，
            # 这会导致巨大的通信帧开销(T_overhead)。本协议引入了全新的“分组探测”机制：
            # 将多个候选者（例如4个）打包成一个“组”，在一次通信中，使用一个更大的组合帧同时对组内
            # 所有候选者进行探测。组合帧内被逻辑地划分为多个子帧，每个候选者独占一个。
            # 读写器只需分析这一个组合帧的返回信号，就能解析出组内所有成员各自的得分和碰撞率。
            # 这个机制通过大幅减少总通信帧数，显著降低了协议的T_overhead和总执行时间。
            
            contender_groups = [contenders[i:i + GROUP_SIZE] for i in range(0, len(contenders), GROUP_SIZE)]
            
            for group in contender_groups:
                total_frames += 1
                seed = generate_random_seed()
                combined_frame_size = len(group) * SUB_FRAME_SIZE
                
                # 读写器广播组内所有价格、种子和子帧大小
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

                # 读写器分析组合帧，解析出每个候选者的结果
                for i, price in enumerate(group):
                    sub_frame_start = i * SUB_FRAME_SIZE
                    sub_frame_end = sub_frame_start + SUB_FRAME_SIZE
                    sub_frame_states = slot_states[sub_frame_start:sub_frame_end]
                    
                    score = sum(1 for count in sub_frame_states if count > 0)
                    collisions = sum(1 for count in sub_frame_states if count > 1)
                    collision_rate = collisions / SUB_FRAME_SIZE if SUB_FRAME_SIZE > 0 else 0
                    
                    current_round_results[price] = {
                        'score': score,
                        'collision_rate': collision_rate,
                    }

            round_results = current_round_results
            if not round_results:
                break
                
            # 使用父代A的晋级逻辑筛选下一轮竞争者
            max_score = max((res['score'] for res in round_results.values()), default=0)
            if max_score == 0 and round_num > 0: # 如果所有候选者都没有响应，则提前终止
                break

            next_contenders = set()
            for price, result in round_results.items():
                # 规则1: 最高分晋级
                if result['score'] == max_score:
                    next_contenders.add(price)
                # 规则2: 高碰撞率者也晋级
                if result['score'] >= MIN_SCORE_FOR_COLLISION_PROMOTION and result['collision_rate'] > COLLISION_PROMOTION_THRESHOLD:
                    next_contenders.add(price)
            
            contenders = sorted(list(next_contenders))

        # --- 阶段三：最终裁决与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * 1 * 0.4 # bits_per_slot is 1
        T_overhead = max(0, (total_frames - 1) * 30)
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Used Parent B's range pruning for initial candidates and Parent A's iterative refinement with dual-criteria (score + collision) promotion.",
            "mutation_info": "Introduced 'Grouped Probing' to test multiple contenders in a single frame, reducing communication overhead.",
            "initial_candidate_count": len(candidate_prices),
            "final_contender_count": len(contenders),
            "final_results": round_results
        }
        
        if not round_results:
            return [], 0, total_time_ms, verification_data

        final_max_score = max((res['score'] for res in round_results.values()), default=0)
        if final_max_score == 0:
            return [], 0, total_time_ms, verification_data
        
        modes = sorted([p for p, res in round_results.items() if res['score'] == final_max_score])
        
        return modes, final_max_score, total_time_ms, verification_data
