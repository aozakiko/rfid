import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g5_2(IProtocol):
    """
    自适应混合众数查询协议 (Adaptive Hybrid Protocol for Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，有机地融合了两个父代协议的核心思想，并引入了全新的
    “基于冲突感知的自适应重估”机制，以在保证高准确率的同时优化通信开销。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 交叉阶段 (源于父代A)
        CANDIDATE_FILTER_FRAME_SIZE = 1024
        # 突变阶段 (全新机制)
        SCOUTING_FRAME_SIZE = 64
        CONFIRMATION_FRAME_SIZE = 512
        COLLISION_THRESHOLD = 0.2  # 冲突率阈值，超过此值触发重估

        # --- 交叉部分：融合父代A的“全局哈希过滤”思想与父代B的“单价迭代评分”思想 ---
        # 本协议首先采用父代A（g4_1）中最高效的策略：使用一个大的哈希帧对整个价格空间进行一次性过滤。
        # 这种方法通过将标签按其价格哈希到不同的时隙中，可以快速识别出哪些时隙中可能存在标签，
        # 从而生成一个规模远小于总价格区间的候选价格列表。
        # 随后，对于这个候选列表，我们不再使用父代A复杂的多阶段验证，而是借鉴了父代B（ModeQueryTCS）
        # 的核心逻辑：对每个候选价格进行一次独立的帧通信来评估其群体规模。
        # 这种“全局初筛 + 逐个详查”的结合，兼顾了父代A的广度搜索效率和父代B的深度验证简洁性。

        # 阶段一：全局候选价格生成 (源自父代A)
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + CANDIDATE_FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        # 读写器无法直接访问标签私有数据，此代码块模拟所有标签对广播指令的匿名、聚合响应。
        # 读写器能观察到的最终结果是 filtering_slots 数组的状态。
        filtering_slots = [0] * CANDIDATE_FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % CANDIDATE_FILTER_FRAME_SIZE
            filtering_slots[slot_id] += 1
        # --- [物理层模拟结束] ---

        # 每个时隙仅需1比特表示是否非空
        total_tag_bits_sent += CANDIDATE_FILTER_FRAME_SIZE * 1

        # 读写器根据非空时隙反推候选价格
        active_slot_indices = {i for i, count in enumerate(filtering_slots) if count > 0}
        
        candidate_prices = []
        if active_slot_indices:
            # 读写器迭代所有可能的价格，检查其哈希值是否落在活动时隙内
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % CANDIDATE_FILTER_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)
        
        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after initial filtering"}

        # --- 突变部分：引入“基于冲突感知的自适应重估”全新机制 ---
        # 两个父代协议在评分时都使用固定的帧长策略。父代A虽有大小帧之分，但切换是预设的流程，
        # 而非根据实时网络状态的反馈。父代B则自始至终使用一个固定的帧长。
        # 本协议引入一个全新的动态优化机制：在对候选价格进行评分时，我们首先使用一个较小的、
        # 低成本的“侦察帧”进行初步探测。同时，我们不仅仅关心得分（非空时隙数），还密切关注
        # “时隙冲突率”。如果某个价格的冲突率超过了预设阈值，协议会判定其得分可能因过多冲突
        # 而被严重低估。此时，协议将自动触发一次“确认”流程，专门为这个高嫌疑价格启动一次
        # 使用高精度“确认帧”（更大的frame_size）的二次探测，以获取更准确的计数值。
        # 这种机制能够智能地将宝贵的通信资源动态分配给最需要精确计数的“拥挤”价格，
        # 避免了对所有候选者都使用高成本大帧的浪费，也解决了小帧无法准确评估高密度群体的难题。
        
        # 阶段二：对候选价格进行自适应评分
        final_scores = {}
        scouting_details = {}

        for price in candidate_prices:
            # Pass 1: 使用侦察帧进行初步探测
            total_frames += 1
            scout_seed = generate_random_seed()
            total_bits_sent_by_reader += (price.bit_length() + SCOUTING_FRAME_SIZE.bit_length() + scout_seed.bit_length())

            # --- [物理层模拟] ---
            scout_slots = [0] * SCOUTING_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, scout_seed) % SCOUTING_FRAME_SIZE
                    scout_slots[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += SCOUTING_FRAME_SIZE * 1

            # 读写器分析侦察帧的聚合信号
            num_non_empty = sum(1 for count in scout_slots if count > 0)
            num_collisions = sum(1 for count in scout_slots if count > 1)
            collision_rate = num_collisions / SCOUTING_FRAME_SIZE if SCOUTING_FRAME_SIZE > 0 else 0

            scouting_details[price] = {'score': num_non_empty, 'collision_rate': collision_rate}
            
            # 决策：是否需要使用确认帧进行重估
            if collision_rate > COLLISION_THRESHOLD:
                # Pass 2: 冲突率过高，使用确认帧进行精确重估
                total_frames += 1
                confirm_seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + CONFIRMATION_FRAME_SIZE.bit_length() + confirm_seed.bit_length())

                # --- [物理层模拟] ---
                confirm_slots = [0] * CONFIRMATION_FRAME_SIZE
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, confirm_seed) % CONFIRMATION_FRAME_SIZE
                        confirm_slots[slot_id] += 1
                # --- [物理层模拟结束] ---

                total_tag_bits_sent += CONFIRMATION_FRAME_SIZE * 1
                
                final_score = sum(1 for count in confirm_slots if count > 0)
                final_scores[price] = final_score
            else:
                # 冲突率在可接受范围内，侦察得分即为最终得分
                final_scores[price] = num_non_empty
        
        # --- 最终处理与时间计算 ---
        if not final_scores:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No scores calculated for candidates"}

        # 找出得分最高的众数
        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "candidate_count": len(candidate_prices),
            "scouting_details": scouting_details,
            "final_scores": final_scores
        }
        
        return modes, max_score, total_time_ms, verification_data
