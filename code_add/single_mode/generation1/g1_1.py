import math
import random
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
# 注意：请确保您的哈希函数文件名是 "hash.py"
from hash import simple_hash

class g1_1(IProtocol):
    """
    修正版协议：动态分层剪枝众数查询 (Dynamic Hierarchical Pruning Mode Query)

    该协议通过三个阶段来高效地查找众数价格。本版本经过重构，严格遵守信息隔离原则，
    将读写器逻辑与物理层模拟完全分离，并修正了逻辑漏洞。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        PRECHECK_BINS = 4
        PRECHECK_FRAME_SIZE = 32
        ESTIMATION_BATCH_SIZE = 100
        ESTIMATION_FRAME_SIZE = 256
        TOP_K_CANDIDATES = 10
        SHOWDOWN_FRAME_SIZE = 128

        # =================================================================================
        # Phase 1: 预检剪枝
        # =================================================================================
        active_prices = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        price_bins = [
            list(range(price_min + i * bin_size, min(price_min + (i + 1) * bin_size, price_max + 1)))
            for i in range(PRECHECK_BINS)
        ]

        for p_bin in price_bins:
            if not p_bin: continue
            
            # --- 读写器行为: 广播指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() + p_bin[0].bit_length() + p_bin[-1].bit_length()
            
            # --- [物理层模拟] ---
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_id = simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1 # 1 bit reply
            
            # --- 读写器行为: 分析帧 ---
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {}

        # =================================================================================
        # Phase 2: 并行估算
        # =================================================================================
        estimated_counts = {}
        sorted_active_prices = sorted(list(active_prices))
        price_batches = [
            sorted_active_prices[i:i + ESTIMATION_BATCH_SIZE] 
            for i in range(0, len(sorted_active_prices), ESTIMATION_BATCH_SIZE)
        ]

        for batch in price_batches:
            if not batch: continue
            
            # --- 读写器行为: 广播指令 ---
            num_prices_in_batch = len(batch)
            total_frames += 1
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            total_bits_sent_by_reader += (seed.bit_length() + ESTIMATION_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())
            
            # --- [物理层模拟] ---
            physical_frame = Frame(num_slots=ESTIMATION_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % ESTIMATION_FRAME_SIZE
                    reply_vector = 1 << price_to_bit_pos_map[t.price]
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += ESTIMATION_FRAME_SIZE * num_prices_in_batch

            # --- 读写器行为: 分析帧 ---
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = ESTIMATION_FRAME_SIZE - sum(logical_bit_vector)
                
                if num_zeros == ESTIMATION_FRAME_SIZE:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    estimated_counts[price] = ESTIMATION_FRAME_SIZE * 3 
                else:
                    count = -ESTIMATION_FRAME_SIZE * math.log(num_zeros / ESTIMATION_FRAME_SIZE)
                    estimated_counts[price] = count
        
        if not estimated_counts:
            # 此处计算并返回总时间
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {}
            
        candidate_prices = sorted(estimated_counts, key=estimated_counts.get, reverse=True)[:TOP_K_CANDIDATES]

        # =================================================================================
        # Phase 3: 冲突裁决
        # =================================================================================
        showdown_results = {}
        for price in candidate_prices:
            # --- 读写器行为: 广播指令 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += seed.bit_length() + SHOWDOWN_FRAME_SIZE.bit_length() + price.bit_length()
            
            # --- [物理层模拟] ---
            slot_states = [0] * SHOWDOWN_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % SHOWDOWN_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += SHOWDOWN_FRAME_SIZE * 1

            # --- 读写器行为: 分析帧 ---
            num_collisions = sum(1 for count in slot_states if count > 1)
            showdown_results[price] = num_collisions

        # --- 最终结果处理 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        if not showdown_results:
            return [], 0, total_time_ms, {}

        max_collisions = max(showdown_results.values())
        
        # [修正] 修复只返回一个众数的逻辑错误
        modes = sorted([p for p, count in showdown_results.items() if count == max_collisions])
        
        verification_data = {
            "precheck_frame_size": PRECHECK_FRAME_SIZE,
            "estimation_frame_size": ESTIMATION_FRAME_SIZE,
            "showdown_frame_size": SHOWDOWN_FRAME_SIZE,
            "top_k_candidates": candidate_prices, # 注意这只是进入第三阶段的候选
            "final_collision_counts": showdown_results
        }
        
        return modes, max_collisions, total_time_ms, verification_data