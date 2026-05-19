import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g2_4(IProtocol):
    """
    子代协议：自适应过滤与分组验证的混合众数查询 (Hybrid Query with Adaptive Filtering and Grouped Verification)

    本协议通过一个两阶段过程来高效并准确地解决多众数查询问题，其设计深度融合了父代协议的优点并引入了全新的优化机制。

    - 阶段一 (自适应过滤): 
      借鉴父代A的“分治”思想，将价格空间划分为多个桶，在桶内进行独立探测。同时，借鉴父代B的思想，
      不仅识别非空时隙，还重点标记出“冲突”时隙，为第二阶段的资源分配提供关键信息。

    - 阶段二 (分组验证与精确计数): 
      首先，引入全新的“分组验证”机制。对于在第一阶段哈希到同一冲突时隙的多个候选价格，协议会
      将它们编组，在一次通信中并行估算它们的数量，快速淘汰掉其中的非优胜者。随后，对筛选出的
      最终候选者，采用父代A中鲁棒的、基于动态帧调整的精确计数循环来得到最终结果。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        # --- 协议超参数 ---
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # 阶段一 (过滤) 参数
        num_buckets = 32  # 将价格范围划分为32个桶
        filter_frame_size = 128 # 每个桶内的过滤帧大小

        # 阶段二 (验证) 参数
        bits_per_tag_id_reply = 32 # 假设标签回复ID需要32bit
        # 突变机制的参数：分组验证时，为组内每个价格分配的子帧大小
        grouped_verification_subframe_size = 32 
        # 精确计数时，根据候选者来源设定的初始帧大小
        initial_exact_frame_large = 128 # 来自冲突时隙
        initial_exact_frame_small = 32 # 来自成功时隙

        # =================================================================================
        # --- 交叉部分：融合父代A的“分桶”分治思想与父代B的“冲突检测”思想 ---
        # =================================================================================
        
        # 阶段一: 桶式过滤，识别候选价格及其来源类型 (成功/冲突)
        bucket_results = []
        price_range = price_max - price_min + 1
        bucket_size = math.ceil(price_range / num_buckets)

        for i in range(num_buckets):
            start_price = price_min + i * bucket_size
            end_price = min(start_price + bucket_size - 1, price_max)
            if start_price > price_max:
                continue

            total_frames += 1
            seed_filter = generate_random_seed()
            
            total_bits_sent_by_reader += (seed_filter.bit_length() +
                                          filter_frame_size.bit_length() +
                                          start_price.bit_length() +
                                          end_price.bit_length())

            # [物理层模拟]
            slot_counts = defaultdict(int)
            for t in tags:
                if start_price <= t.price <= end_price:
                    slot_id = simple_hash(t.price, seed_filter) % filter_frame_size
                    slot_counts[slot_id] += 1
            # [物理层模拟结束]
            
            total_tag_bits_sent += filter_frame_size * 1 

            non_empty_slots = {slot for slot, count in slot_counts.items() if count > 0}
            collided_slots = {slot for slot, count in slot_counts.items() if count > 1}
            bucket_results.append({
                "range": (start_price, end_price),
                "seed": seed_filter,
                "non_empty": non_empty_slots,
                "collided": collided_slots
            })

        # =================================================================================
        # --- 突变部分：引入“分组验证”全新机制 ---
        # =================================================================================
        
        # 阶段二准备: 生成候选者并按(桶, 时隙)分组
        candidate_groups = defaultdict(list)
        for idx, result in enumerate(bucket_results):
            start_price, end_price = result["range"]
            for p in range(start_price, end_price + 1):
                slot_p = simple_hash(p, result["seed"]) % filter_frame_size
                if slot_p in result["non_empty"]:
                    group_key = (idx, slot_p)
                    candidate_groups[group_key].append(p)
        
        # 阶段二: 分组预选与精确计数
        final_candidates = {} # 存储通过预选的候选者及其来源 {price: is_from_collided_slot}

        for (bucket_idx, slot_idx), prices in candidate_groups.items():
            is_collided = slot_idx in bucket_results[bucket_idx]["collided"]

            if len(prices) > 1 and is_collided:
                # 执行分组验证
                total_frames += 1
                seed_group = generate_random_seed()
                
                bits_for_prices = sum(p.bit_length() for p in prices)
                total_bits_sent_by_reader += (seed_group.bit_length() + 
                                              grouped_verification_subframe_size.bit_length() + 
                                              bits_for_prices)

                # [物理层模拟]
                group_frame_size = len(prices) * grouped_verification_subframe_size
                group_slot_counts = defaultdict(int)
                price_to_offset = {price: i * grouped_verification_subframe_size for i, price in enumerate(prices)}

                for t in tags:
                    if t.price in price_to_offset:
                        offset = price_to_offset[t.price]
                        sub_slot = simple_hash(t.id, seed_group) % grouped_verification_subframe_size
                        group_slot_counts[offset + sub_slot] += 1
                # [物理层模拟结束]
                
                total_tag_bits_sent += group_frame_size * 1

                # 分析分组结果
                price_scores = defaultdict(int)
                for slot, count in group_slot_counts.items():
                    if count > 0:
                        price_index = slot // grouped_verification_subframe_size
                        price_scores[prices[price_index]] += 1
                
                if price_scores:
                    winner_price = max(price_scores, key=price_scores.get)
                    final_candidates[winner_price] = True # 标记为来自冲突
            else:
                final_candidates[prices[0]] = is_collided

        # 对最终候选者进行精确计数
        exact_counts = {}
        for price, is_from_collided in final_candidates.items():
            initial_frame_size = initial_exact_frame_large if is_from_collided else initial_exact_frame_small
            
            identified_tag_ids_for_price = set()
            current_frame_size = initial_frame_size
            
            # (MODIFIED FOR COMPLIANCE) 循环必须基于可观测的信道状态终止
            while True:
                total_frames += 1
                seed_exact = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed_exact.bit_length()

                # [物理层模拟] (MODIFIED FOR COMPLIANCE)
                # 模拟现在正确地反映了所有标签根据广播独立决策的过程
                slot_states = defaultdict(list)
                for t in tags:
                    # 标签的独立决策逻辑：价格匹配且未被静默
                    if t.price == price and t.id not in identified_tag_ids_for_price:
                        slot_id = simple_hash(t.id, seed_exact) % current_frame_size
                        slot_states[slot_id].append(t.id)
                # [物理层模拟结束]
                
                total_tag_bits_sent += current_frame_size * bits_per_tag_id_reply

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
                
                # (MODIFIED FOR COMPLIANCE) 合规的自适应帧长调整
                if num_collision_slots > 0:
                    current_frame_size = min(current_frame_size * 2, 2048)

            exact_counts[price] = len(identified_tag_ids_for_price)

        # =================================================================================
        # 结果处理与时间计算
        # =================================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        verification_data = {
            "protocol_name": "GFinalProtocol",
            "num_candidate_groups": len(candidate_groups),
            "num_final_candidates": len(final_candidates),
            "exact_counts": exact_counts
        }

        if not exact_counts:
            return [], 0, total_time_ms, verification_data
        
        max_count = max(exact_counts.values())
        modes = sorted([price for price, count in exact_counts.items() if count == max_count])
        
        return modes, max_count, total_time_ms, verification_data