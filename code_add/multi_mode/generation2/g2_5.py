import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g2_5(IProtocol):
    """
    子代协议: 基于并行估算与启发式剪枝的众数查询协议
    (Mode Query Protocol based on Parallel Estimation and Heuristic Pruning)

    本协议通过一个高效的两阶段过程来精确地解决大规模标签环境下的单一众数查询问题。
    它深度融合了父代协议的核心优势，并引入了全新的优化机制。

    - 阶段一 (并行估算): 全面采用父代B的并行SEM（Slot-Encoded Multiplexing）估算方法。
      此阶段将所有价格分批处理，在每个批次内通过单次通信并行估算出该批次所有价格的
      标签数量。这提供了一个快速、全面的群体画像。

    - 阶段二 (剪枝验证): 借鉴父代A的精确验证框架，但进行了关键的创新。在对估算出的
      候选者列表进行逐一精确计数时，引入了动态剪枝逻辑。每当一个候选者的精确计数值
      被确定后，该值会成为一个新的“基准”，用于判断后续候选者是否还有验证的必要，
      从而避免了大量不必要的精确计数开销。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 阶段一 (估算) 参数
        estimation_batch_size = 128  # 每批次并行估算的价格数量
        estimation_frame_size = 512  # 估算阶段的帧长

        # 阶段二 (验证) 参数
        verification_bits_per_reply = 32 # 验证阶段标签回复ID的长度
        max_candidates_to_verify = 20 # 最多验证的候选者数量，防止极端情况

        # =================================================================================
        # --- 交叉部分：融合父代A的两阶段（估算-验证）框架 与 父代B的高效SEM并行估算思想 ---
        # 采用父代A的先估算后验证的宏观流程，但将估算阶段的核心算法替换为父代B更高效的SEM并行估算。
        # =================================================================================

        # --- 阶段一: 并行估算 (源自父代B的SEM机制) ---
        all_prices = list(range(price_min, price_max + 1))
        price_batches = [all_prices[i:i + estimation_batch_size] for i in range(0, len(all_prices), estimation_batch_size)]
        
        estimated_counts = {}

        for batch in price_batches:
            if not batch: continue
            
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            
            total_bits_sent_by_reader += (seed.bit_length() + estimation_frame_size.bit_length() + num_prices_in_batch.bit_length())

            # --- [物理层模拟] ---
            physical_frame = Frame(num_slots=estimation_frame_size, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % estimation_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += estimation_frame_size * num_prices_in_batch
            
            # --- 读写器分析聚合的Frame ---
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = estimation_frame_size - sum(logical_bit_vector)
                
                # --- (MODIFIED FOR COMPLIANCE) ---
                if num_zeros == 0:
                    # 读写器无法知道确切数量，只能标记为非常大（无穷大）
                    estimated_counts[price] = float('inf') 
                elif num_zeros == estimation_frame_size:
                    estimated_counts[price] = 0
                else:
                    estimated_counts[price] = -estimation_frame_size * math.log(num_zeros / estimation_frame_size)

        # --- 筛选候选者 (源自父代A的思路) ---
        # 将inf值视为最大，进行排序
        sorted_candidates = sorted(
            estimated_counts.items(), 
            key=lambda item: (item[1] == float('inf'), item[1]), 
            reverse=True
        )
        
        candidates_to_verify = sorted_candidates[:max_candidates_to_verify]
        
        # =================================================================================
        # --- 阶段二: 精确验证 (MODIFIED FOR COMPLIANCE) ---
        # =================================================================================
        exact_counts = {}
        current_max_verified_count = 0

        for price, estimated_n in candidates_to_verify:
            # --- 突变部分：启发式剪枝 ---
            if estimated_n < current_max_verified_count:
                break
            
            identified_tag_ids_for_price = set()
            # (MODIFIED FOR COMPLIANCE) 增加保护，防止估算值为inf时导致错误
            if estimated_n == float('inf'):
                current_frame_size = 256 # 如果估算值是无穷大，从一个较大的默认值开始
            else:
                current_frame_size = max(16, int(round(estimated_n)))
            
            # 启动迭代式ALOHA协议
            while True:
                total_frames += 1
                seed_s = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed_s.bit_length()

                # --- [物理层模拟] (MODIFIED FOR COMPLIANCE) ---
                # 模拟现在正确地反映了所有标签根据广播独立决策的过程
                slot_states = defaultdict(list)
                for t in tags:
                    # 标签的独立决策逻辑：价格匹配且未被静默
                    if t.price == price and t.id not in identified_tag_ids_for_price:
                        slot_id = simple_hash(t.id, seed_s) % current_frame_size
                        slot_states[slot_id].append(t.id)
                # --- [物理层模拟结束] ---
                
                total_tag_bits_sent += current_frame_size * verification_bits_per_reply

                num_collision_slots = 0
                newly_identified_ids = set()
                for _, tag_ids in slot_states.items():
                    if len(tag_ids) == 1:
                        newly_identified_ids.add(tag_ids[0])
                    elif len(tag_ids) > 1:
                        num_collision_slots += 1

                if newly_identified_ids:
                    identified_tag_ids_for_price.update(newly_identified_ids)

                # (MODIFIED FOR COMPLIANCE) 合规的终止条件
                if not newly_identified_ids and num_collision_slots == 0:
                    break
                
                # (MODIFIED FOR COMPLIANCE) 合规的帧长自适应
                if num_collision_slots > 0:
                    current_frame_size = min(current_frame_size * 2, 2048)

            exact_counts[price] = len(identified_tag_ids_for_price)
            if exact_counts[price] > current_max_verified_count:
                current_max_verified_count = exact_counts[price]

        # --- 结果处理与时间计算 ---
        if not exact_counts:
            return [], 0, 0.0, {"status": "No candidates to verify"}

        max_count = 0
        if exact_counts:
             max_count = max(exact_counts.values())

        modes = sorted([p for p, c in exact_counts.items() if c == max_count])

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        verification_data = {
            "protocol_name": "EvolvedModeQueryProtocol",
            "phase1_top_candidates": {p: (round(n, 2) if n != float('inf') else 'inf') for p, n in candidates_to_verify},
            "phase2_exact_counts": exact_counts
        }
        
        return modes, max_count, total_time_ms, verification_data