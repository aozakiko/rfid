import math
import random
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g7_4(IProtocol):
    """
    自适应置信度早退众数查询协议 (Adaptive Confidence-based Early Exit Protocol for Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，有机地融合了两个父代协议的核心思想，并引入了全新的
    “逐个探测与置信度评估”的动态提早终止机制，以在保证高准确率的同时显著降低最差情况下的通信开销。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTER_FRAME_SIZE = 1024       # 候选过滤阶段帧长 (源于父代)
        SCORING_FRAME_SIZE = 256       # 独立评分阶段帧长
        MIN_CANDIDATES_FOR_EXIT = 5    # 触发早退检查所需的最少已评分候选者数量
        CONFIDENCE_THRESHOLD = 5.0     # 早退置信度阈值 (最高分 / (挑战者均分+1))

        # --- 交叉部分：融合父代A的“单价独立探测”思想与父代B的“全局哈希过滤”思想 ---
        # 本协议首先采纳了两个父代协议共有的高效初始策略：使用一个大的哈希帧对整个价格空间进行一次性过滤。
        # 这借鉴了父代B（g6_1）和父代A（g5_2）的共同优点，即通过一次广播通信，将搜索空间从[price_min, price_max]
        # 显著缩小到一个规模小得多的“候选价格”列表。
        # 在获得候选列表后，协议采用了类似于父代A（g5_2）第二阶段的逻辑，即对每个候选价格进行一次独立的帧通信来评估其群体规模。
        # 这种“全局初筛 + 逐个详查”的结合，既利用了全局哈希的高效性，又保持了独立探测逻辑的简洁与准确性。

        # 阶段一：全局候选价格生成 (源自父代 A 和 B)
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        # 读写器无法直接访问标签私有数据。此代码块模拟所有标签对广播指令的匿名、聚合响应。
        # 读写器能观察到的最终结果是 filtering_slots 数组的状态。
        filtering_slots = [0] * FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTER_FRAME_SIZE
            filtering_slots[slot_id] += 1
        # --- [物理层模拟结束] ---

        # 每个时隙仅需1比特表示是否非空
        total_tag_bits_sent += FILTER_FRAME_SIZE * 1

        # 读写器根据非空时隙反推候选价格
        active_slot_indices = {i for i, count in enumerate(filtering_slots) if count > 0}
        
        candidate_prices = []
        if active_slot_indices:
            # 读写器迭代所有可能的价格，检查其哈希值是否落在活动时隙内
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTER_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)
        
        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after initial filtering"}

        # --- 突变部分：引入“基于逐个探测的动态置信度早退”全新机制 ---
        # 父代A对所有候选者都进行探测，父代B则采用分轮次、同步淘汰的策略。两者在每一轮/阶段都需要完成对该阶段所有目标的探测后才能进入下一步决策。
        # 本协议引入一个全新的、更为动态的“早退”机制。在对候选价格进行逐个评分时，每评估完一个新的候选者，
        # 协议都会立刻重新计算当前领先者的“置信度”。该置信度被定义为当前最高分与一组“挑战者”（如得分最接近的5个对手）
        # 的平均分之比。如果这个比值超过一个预设的阈值，意味着当前领先者已经与追赶集团拉开了显著差距，
        # 协议将立即终止后续所有未进行的探测，从而在众数分布极不均衡的场景下（一个价格的标签数远超其他），
        # 节省大量的通信开销。这种“不等一轮打完，见机行事”的决策逻辑是两个父代协议都不具备的。

        # 阶段二：对候选价格进行逐个评分，并实时评估早退可能性
        final_scores = {}
        max_score = -1
        
        # 随机化候选者顺序，避免因价格排序带来的偏差
        random.shuffle(candidate_prices)

        for i, price in enumerate(candidate_prices):
            total_frames += 1
            scoring_seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + SCORING_FRAME_SIZE.bit_length() + scoring_seed.bit_length())

            # --- [物理层模拟] ---
            scoring_slots = [0] * SCORING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, scoring_seed) % SCORING_FRAME_SIZE
                    scoring_slots[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCORING_FRAME_SIZE * 1

            # 读写器分析聚合信号
            current_score = sum(1 for count in scoring_slots if count > 0)
            final_scores[price] = current_score

            if current_score > max_score:
                max_score = current_score
            
            # --- 突变逻辑核心：检查是否可以提前终止 ---
            if i + 1 >= MIN_CANDIDATES_FOR_EXIT and max_score > 0:
                # 找出挑战者（所有得分低于最高分者）
                challenger_scores = sorted([s for s in final_scores.values() if s < max_score], reverse=True)
                
                if challenger_scores:
                    # 使用前5名或所有挑战者计算平均分
                    num_challengers_to_avg = min(len(challenger_scores), 5)
                    avg_challenger_score = sum(challenger_scores[:num_challengers_to_avg]) / num_challengers_to_avg
                    
                    confidence = max_score / (avg_challenger_score + 1e-6) # 加一个极小值避免除零
                    
                    if confidence >= CONFIDENCE_THRESHOLD:
                        # 置信度足够高，提前终止
                        break
        
        # --- 最终处理与时间计算 ---
        if not final_scores:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores calculated for candidates"}

        # 找出得分最高的众数
        final_max_score = 0
        if final_scores:
            final_max_score = max(final_scores.values())

        modes = sorted([p for p, s in final_scores.items() if s == final_max_score])

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "total_candidates": len(candidate_prices),
            "candidates_tested": len(final_scores),
            "early_exit": len(final_scores) < len(candidate_prices),
            "final_scores": final_scores
        }
        
        return modes, final_max_score, total_time_ms, verification_data
