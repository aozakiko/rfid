import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g2_5(IProtocol):
    """
    动态评分过滤众数查询协议 (Dynamic Scoring Filter Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTERING_FRAME_SIZE = 1024       # 过滤阶段帧长 (源于父代A)
        INITIAL_SCORING_FRAME_SIZE = 128  # 初始评分帧长 (源于父代B)
        REFINED_SCORING_FRAME_SIZE = 512  # 精炼评分帧长 (突变部分)
        COLLISION_THRESHOLD = 0.9         # 触发精炼评分的冲突率阈值 (突变部分)

        # --- 交叉部分：融合父代A(g1_4)的“槽位过滤”思想与父代B(ModeQueryTCS)的“启发式评分”思想 ---

        # 阶段一：全局槽位过滤 (Inherited from Parent A: g1_4)
        # 采用父代A的确定性槽位过滤机制，通过一次通信快速识别出所有可能存在标签的价格，
        # 形成一个候选列表，从而避免对整个价格空间进行无效的探测。
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTERING_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        filtering_frame = Frame(num_slots=FILTERING_FRAME_SIZE, bits_per_slot=1)
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTERING_FRAME_SIZE
            filtering_frame.aggregate(slot_id, 1)
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += FILTERING_FRAME_SIZE * 1
        
        active_slot_indices = {i for i, data in enumerate(filtering_frame.slots) if data > 0}
        
        candidate_prices = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTERING_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)

        # 阶段二：对候选者进行启发式评分 (Inspired by Parent B: ModeQueryTCS)
        # 对过滤出的候选价格，借鉴父代B的思想，逐一进行探测并使用“非空时隙数”作为启发式分数。
        price_to_score_map = {}
        
        # --- 突变部分：引入“基于冲突感知的动态帧长调整”全新机制 ---
        # 父代协议均使用固定帧长。本协议引入一个创新：在评分阶段，首先用一个标准大小的帧进行探测。
        # 如果探测结果显示冲突率极高（大部分时隙都被占用），表明标签数量可能远超帧长，
        # 此时初始的“非空时隙数”可能已失去区分度。协议将自动为这个高冲突的价格启动一次
        # 拥有更大帧长的二次探测，以“拉伸”响应空间，获得一个更精确、更有区分度的分数。
        
        refined_prices = set() # 用于记录哪些价格启动了精炼探测

        for price in candidate_prices:
            # --- 初始探测 ---
            total_frames += 1
            scoring_seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + INITIAL_SCORING_FRAME_SIZE.bit_length() + scoring_seed.bit_length())

            # --- [物理层模拟] ---
            slot_states = [0] * INITIAL_SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, scoring_seed) % INITIAL_SCORING_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += INITIAL_SCORING_FRAME_SIZE * 1
            
            non_empty_slots = sum(1 for count in slot_states if count > 0)
            price_to_score_map[price] = non_empty_slots

            # --- 动态调整逻辑 (Mutation) ---
            collision_ratio = non_empty_slots / INITIAL_SCORING_FRAME_SIZE
            if collision_ratio >= COLLISION_THRESHOLD:
                refined_prices.add(price)
                # --- 精炼探测 ---
                total_frames += 1
                refined_seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + REFINED_SCORING_FRAME_SIZE.bit_length() + refined_seed.bit_length())

                # --- [物理层模拟] ---
                refined_slot_states = [0] * REFINED_SCORING_FRAME_SIZE
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, refined_seed) % REFINED_SCORING_FRAME_SIZE
                        refined_slot_states[slot_id] += 1
                # --- [物理层模拟结束] ---

                total_tag_bits_sent += REFINED_SCORING_FRAME_SIZE * 1
                
                # 更新为更精确的分数
                price_to_score_map[price] = sum(1 for count in refined_slot_states if count > 0)

        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        if not price_to_score_map:
            return [], 0, total_time_ms, {"candidates": candidate_prices, "scores": {}}

        max_score = max(price_to_score_map.values())
        modes = sorted([p for p, score in price_to_score_map.items() if score == max_score])

        verification_data = {
            "candidates_after_filtering": len(candidate_prices),
            "prices_refined": list(refined_prices),
            "final_scores": price_to_score_map
        }

        return modes, max_score, total_time_ms, verification_data
