import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash
class g5_3(IProtocol):
    """
    混合消除协议 (Hybrid Elimination Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTERING_FRAME_SIZE = 1024   # 过滤阶段帧长
        SCORING_FRAME_SIZE = 128      # 评分阶段帧长
        ELIMINATION_FRAME_SIZE = 64   # 突变阶段的消除帧长
        ELIMINATION_BATCH_SIZE = 50   # 突变阶段的批处理大小

        # --- 交叉部分：融合父代A的“启发式评分”思想与父代B的“槽位过滤”思想 ---
        
        # 阶段一：全局槽位过滤 (源自父代 B 的核心策略)
        # 采用父代B的确定性槽位过滤机制，通过一次通信快速识别出所有可能存在标签的价格，
        # 形成一个候选列表，从而避免对整个价格空间进行无效的探测。
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTERING_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        # 读写器无法直接访问标签私有数据。此代码块模拟所有标签的匿名、聚合响应。
        filtering_slot_states = [0] * FILTERING_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTERING_FRAME_SIZE
            filtering_slot_states[slot_id] += 1
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += FILTERING_FRAME_SIZE * 1
        
        # 读写器根据聚合信号构建候选价格列表
        active_slot_indices = {i for i, count in enumerate(filtering_slot_states) if count > 0}
        
        candidate_prices = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTERING_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)
        
        # --- 突变部分：引入“批量零标签候选者快速消除”全新机制 ---
        # 父代协议的过滤机制可能产生大量无标签的“伪候选者”（由哈希碰撞导致）。
        # 父代B会为每一个伪候选者浪费一次完整的通信帧。本协议引入一个创新的消除阶段：
        # 将候选者分批，并为每批广播一次廉价的“存在性质询”。如果整个批次没有任何一个标签响应，
        # 读写器就可以安全地一次性剔除该批次的所有候选者，显著减少后续评分阶段的无效通信开销。
        
        verified_candidates = []
        num_batches = math.ceil(len(candidate_prices) / ELIMINATION_BATCH_SIZE)
        
        for i in range(num_batches):
            batch = candidate_prices[i*ELIMINATION_BATCH_SIZE : (i+1)*ELIMINATION_BATCH_SIZE]
            if not batch:
                continue
            
            total_frames += 1
            elimination_seed = generate_random_seed()
            # 读写器广播批次内的所有价格、帧长和种子
            # 假设价格最大为1000，需要10比特
            batch_bits = len(batch) * (price_max.bit_length() if price_max > 0 else 1)
            total_bits_sent_by_reader += (batch_bits + ELIMINATION_FRAME_SIZE.bit_length() + elimination_seed.bit_length())

            batch_set = set(batch)
            
            # --- [物理层模拟] ---
            elimination_slot_states = [0] * ELIMINATION_FRAME_SIZE
            is_batch_active = False
            for t in tags:
                if t.price in batch_set:
                    is_batch_active = True # 只要有一个标签响应，就可以提前终止模拟
                    break 
            # 只有当批次中确实有标签时，才会发生实际的信道占用
            if is_batch_active:
                for t in tags:
                     if t.price in batch_set:
                        slot_id = simple_hash(t.id, elimination_seed) % ELIMINATION_FRAME_SIZE
                        elimination_slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += ELIMINATION_FRAME_SIZE * 1
            
            # 读写器分析聚合信号
            if sum(1 for count in elimination_slot_states if count > 0) > 0:
                verified_candidates.extend(batch)

        # 阶段三：对验证后的候选者进行启发式评分 (源自父代 A 的核心策略)
        # 对经过过滤和消除两层筛选的候选价格，借鉴父代A的思想，逐一进行探测，
        # 并使用“非空时隙数”作为启发式分数来估计每个价格的标签数量。
        price_to_score_map = {}
        for price in verified_candidates:
            total_frames += 1
            scoring_seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + SCORING_FRAME_SIZE.bit_length() + scoring_seed.bit_length())

            # --- [物理层模拟] ---
            scoring_slot_states = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, scoring_seed) % SCORING_FRAME_SIZE
                    scoring_slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1
            
            non_empty_slots = sum(1 for count in scoring_slot_states if count > 0)
            if non_empty_slots > 0:
                price_to_score_map[price] = non_empty_slots

        # --- 最终处理与时间成本计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        # total_tag_bits_sent is already calculated based on all frames
        T_tag = total_tag_bits_sent * 0.4 
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        if not price_to_score_map:
            modes, max_score = [], 0
        else:
            max_score = max(price_to_score_map.values())
            modes = sorted([p for p, score in price_to_score_map.items() if score == max_score])

        verification_data = {
            "initial_candidates": len(candidate_prices),
            "candidates_after_elimination": len(verified_candidates),
            "final_scores": price_to_score_map,
            "num_filtering_frames": 1,
            "num_elimination_frames": num_batches,
            "num_scoring_frames": len(verified_candidates)
        }

        return modes, max_score, total_time_ms, verification_data
