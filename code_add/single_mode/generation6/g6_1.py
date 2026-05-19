import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g6_1(IProtocol):
    """
    混合分级精确众数查询协议 (Hybrid Iterative Refinement Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并通过一个创新的多阶段过滤与评分框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“基于置信度的动态分级探测”机制以实现自适应的精确查询。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTER_FRAME_SIZE = 1024 # 候选价格过滤阶段的帧大小
        INITIAL_SCORING_FRAME_SIZE = 64 # 初始评分帧大小
        MAX_SCORING_ROUNDS = 4 # 最多进行4轮评分
        CONFIDENCE_THRESHOLD = 4.0 # 提前终止的置信度阈值 (最高分/次高分)
        
        # --- 交叉部分：融合父代B的“全局价格哈希过滤”思想与父代A的“非空时隙评分”思想 ---
        # 阶段一：全局候选价格过滤 (源自父代B: g5_1的核心思想)
        # 本协议首先采用父代B的高效策略，通过对所有标签的价格值进行一次性哈希，
        # 快速生成一个高度精炼的候选价格列表。这比父代A的范围剪枝能更显著地缩小搜索空间。
        
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += filter_seed.bit_length() + FILTER_FRAME_SIZE.bit_length()

        # --- [物理层模拟] ---
        filtering_slots = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTER_FRAME_SIZE
            filtering_slots[slot_id] = 1 # 只关心时隙是否非空
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += FILTER_FRAME_SIZE * 1
        
        active_slot_indices = {i for i, state in enumerate(filtering_slots) if state > 0}
        
        candidate_prices = []
        if active_slot_indices:
            # 读写器本地计算哪些价格可能落入活跃时隙
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTER_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)
        
        if not candidate_prices:
            # 如果没有候选价格，提前终止并计算时间
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0 # 只有一个帧
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after filtering"}

        # --- 突变部分：引入“基于置信度的动态分级探测”全新机制 ---
        # 两个父代协议都采用了较为固定的探测流程（父代A逐一探测，父代B采用固定的两级锦标赛）。
        # 本协议引入一个全新的动态机制：协议会进行多轮探测，每一轮探测后，读写器都会评估当前最高分与次高分的比值，
        # 计算出一个“置信度”。如果置信度足够高，意味着已基本锁定胜者，协议将提前终止，避免不必要的通信开销。
        # 如果置信度不足，协议会自动升级，筛选出得分最高的竞争者进入下一轮，并使用更大的帧进行更精确的探测。
        # 这种“逐级确认、动态终止”的逻辑是两个父代协议都不具备的，它使协议能根据数据分布的“区分难度”来智能地调整探测深度。

        current_contenders = candidate_prices
        current_scores = {}
        current_frame_size = INITIAL_SCORING_FRAME_SIZE
        
        for round_num in range(MAX_SCORING_ROUNDS):
            # 对本轮的竞争者进行探测
            for price in current_contenders:
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
                
                total_tag_bits_sent += current_frame_size * 1
                
                # 采用父代A的“非空时隙数”作为核心评分标准，鲁棒且准确
                num_non_empty_slots = sum(1 for count in slot_states if count > 0)
                current_scores[price] = num_non_empty_slots
            
            # --- 突变逻辑核心：分析分数并决策 ---
            if not current_scores or max(current_scores.values()) == 0:
                break # 如果没有得分，则终止

            sorted_scores = sorted(current_scores.values(), reverse=True)
            max_score = sorted_scores[0]
            
            # 检查是否可以提前终止
            if len(sorted_scores) == 1 or max_score == 0:
                break # 只有一个竞争者或最高分为0，可以终止
            
            second_max_score = sorted_scores[1]
            
            if second_max_score > 0 and (max_score / second_max_score) >= CONFIDENCE_THRESHOLD:
                # 置信度足够高，提前终止
                break 

            # 如果未达到最大轮次，则准备下一轮
            if round_num < MAX_SCORING_ROUNDS - 1:
                # 筛选进入下一轮的竞争者（所有得分最高的）
                next_contenders = [p for p, s in current_scores.items() if s == max_score]
                # 如果只有最高分进入下一轮，可以把次高分的也带上，以防万一
                if len(next_contenders) == 1:
                    next_contenders.extend([p for p, s in current_scores.items() if s == second_max_score])
                
                current_contenders = list(set(next_contenders))
                
                # 升级帧大小以获得更高精度
                current_frame_size *= 4
            
        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "mutation_info": "Dynamic scoring rounds with confidence-based early termination.",
            "initial_candidate_count": len(candidate_prices),
            "final_round_num": round_num + 1,
            "final_scores": current_scores
        }
        
        if not current_scores:
            return [], 0, total_time_ms, verification_data

        # 找出最终得分最高的众数
        max_final_score = max(current_scores.values())
        if max_final_score == 0:
            return [], 0, total_time_ms, verification_data
            
        modes = sorted([
            price for price, score in current_scores.items() 
            if score == max_final_score
        ])
        
        return modes, max_final_score, total_time_ms, verification_data
