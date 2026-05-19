import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g1_4(IProtocol):
    """
    子代协议：基于分桶估算与自适应验证的混合众数查询 (Bucket-based Estimation and Adaptive Verification Hybrid Mode Query)

    本协议通过一个两阶段过程来高效并准确地解决多众数查询问题。

    - 阶段一 (分桶估算): 借鉴父代B的概率估算思想，并结合类父代A的“分治”策略。协议将整个价格区间
      分割为多个“桶”，在每个桶内独立执行高效的ALOHA估算。这显著降低了多个热门价格因哈希到同一
      时隙而互相干扰的可能性，确保了估算阶段的准确性。

    - 阶段二 (自适应验证): 对第一阶段筛选出的候选价格进行精确计数。此阶段引入了全新的自适应机制，
      它会根据估算结果的分布动态决定需要精确验证的候选者数量，而非使用固定值。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        # --- 协议超参数 ---
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # 阶段一 (估算) 参数
        sog_length_v = 32
        omega_w = 1.2897
        estimation_frame_size = 1024 # 在每个桶内使用，不需要太大
        num_buckets = 32 # 将价格范围划分为32个桶进行查询

        # 阶段二 (验证) 参数
        bits_per_tag_id_reply = 32

        # =================================================================================
        # --- 交叉部分：融合父代B的概率估计算法与父代A的“分治”思想，创建了“分桶估算”阶段 ---
        # =================================================================================
        
        price_stats = {p: {"sum_L": 0.0, "num_slots": 0} for p in range(price_min, price_max + 1)}
        price_range = price_max - price_min + 1
        bucket_size = math.ceil(price_range / num_buckets)

        for i in range(num_buckets):
            start_price = price_min + i * bucket_size
            end_price = min(start_price + bucket_size - 1, price_max)
            if start_price > price_max:
                continue
            
            total_frames += 1
            seed_r = generate_random_seed()
            
            bits_to_send = (seed_r.bit_length() + 
                            estimation_frame_size.bit_length() + 
                            sog_length_v.bit_length() +
                            start_price.bit_length() +
                            end_price.bit_length())
            total_bits_sent_by_reader += bits_to_send

            # [物理层模拟]
            physical_frame = Frame(num_slots=estimation_frame_size, bits_per_slot=sog_length_v)
            for t in tags:
                if start_price <= t.price <= end_price:
                    random_bits = simple_hash(t.id, seed_r)
                    mu = 0
                    for bit_pos in range(sog_length_v - 1, -1, -1):
                        if (random_bits >> bit_pos) & 1:
                            break
                        mu += 1
                    else:
                         mu = sog_length_v

                    sog_string = (1 << (sog_length_v - 1 - mu)) if mu < sog_length_v else 0
                    slot_id = simple_hash(t.price, seed_r) % estimation_frame_size
                    physical_frame.aggregate(slot_id, sog_string)
            # [物理层模拟结束]
            
            total_tag_bits_sent += physical_frame.num_slots * physical_frame.bits_per_slot

            slot_to_prices_map = defaultdict(list)
            for p in range(start_price, end_price + 1):
                slot_idx = simple_hash(p, seed_r) % estimation_frame_size
                slot_to_prices_map[slot_idx].append(p)

            for slot_idx, prices in slot_to_prices_map.items():
                if len(prices) == 1:
                    price_val = prices[0]
                    combined_signal = physical_frame.slots[slot_idx]
                    
                    L = 0
                    for bit_pos in range(sog_length_v - 1, -1, -1):
                        if not ((combined_signal >> bit_pos) & 1):
                            break
                        L += 1
                    else:
                        L = sog_length_v
                    
                    price_stats[price_val]["sum_L"] += L
                    price_stats[price_val]["num_slots"] += 1

        estimated_counts = {}
        for p, stats in price_stats.items():
            s_p = stats["num_slots"]
            if s_p > 0:
                avg_L = stats["sum_L"] / s_p
                estimated_n = omega_w * (2 ** avg_L)
                estimated_counts[p] = estimated_n

        if not estimated_counts:
             return [], 0, 0.0, {"status": "No tags responded in estimation phase"}

        sorted_candidates = sorted(estimated_counts.items(), key=lambda item: item[1], reverse=True)
        
        # --- 突变部分：引入自适应候选选择机制 ---
        top_candidates = []
        if sorted_candidates and sorted_candidates[0][1] > 0:
            max_estimated_count = sorted_candidates[0][1]
            threshold = max_estimated_count * 0.7
            for price, est_count in sorted_candidates:
                if est_count >= threshold:
                    top_candidates.append((price, est_count))
                else:
                    break
            
            if len(top_candidates) < 3:
                top_candidates = sorted_candidates[:min(3, len(sorted_candidates))]
            if len(top_candidates) > 10:
                top_candidates = top_candidates[:10]

        # =================================================================================
        # 阶段二: 对候选者进行精确计数 (修正后的合规逻辑)
        # =================================================================================
        exact_counts = {}
        for price, estimated_n in top_candidates:
            if estimated_n < 1:
                exact_counts[price] = 0
                continue
            
            identified_tag_ids_for_price = set()
            current_frame_size = max(16, int(round(estimated_n)))
            
            while True:
                total_frames += 1
                seed_s = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed_s.bit_length()

                # --- [物理层模拟] ---
                slot_states = defaultdict(list)
                responding_tags_in_round = [
                    t for t in tags if t.price == price and t.id not in identified_tag_ids_for_price
                ]

                if not responding_tags_in_round:
                    break

                for t in responding_tags_in_round:
                    slot_id = simple_hash(t.id, seed_s) % current_frame_size
                    slot_states[slot_id].append(t.id)
                # --- [物理层模拟结束] ---
                
                total_tag_bits_sent += current_frame_size * bits_per_tag_id_reply

                num_collision = 0
                for slot, tag_ids in slot_states.items():
                    if len(tag_ids) == 1:
                        identified_tag_ids_for_price.add(tag_ids[0])
                    elif len(tag_ids) > 1:
                        num_collision += 1

                if num_collision == 0:
                    break

                remaining_tags_estimate = num_collision * 2
                current_frame_size = max(1, remaining_tags_estimate)

            exact_counts[price] = len(identified_tag_ids_for_price)

        # =================================================================================
        # 结果处理与时间计算
        # =================================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4 
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        verification_data = {
            "protocol_name": "ModeQueryHybrid (Fixed)",
            "phase1_candidates": {p: round(n, 2) for p, n in top_candidates if n > 0},
            "phase2_exact_counts": exact_counts
        }

        if not exact_counts:
            if top_candidates:
                best_guess_price = top_candidates[0][0]
                best_guess_count = math.ceil(top_candidates[0][1])
                return [best_guess_price], best_guess_count, total_time_ms, verification_data
            return [], 0, total_time_ms, verification_data
        
        max_count = max(exact_counts.values())
        modes = sorted([price for price, count in exact_counts.items() if count == max_count])
        
        return modes, max_count, total_time_ms, verification_data