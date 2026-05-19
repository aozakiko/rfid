# protocol/ModeQuerySEM.py

import math
import random
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol,simple_hash

class ModeQuerySEM(IProtocol):
    """
    修正版协议：分批次SEM众数估算协议 (Batched SEM Mode Estimation Protocol)

    本协议实现了分批次SEM（Slot-Encoded Multiplexing）的估算思想，并进行了
    关键修正以保证其逻辑正确性并严格遵守信息隔离原则。协议将所有价格分批处理，
    在每个批次内，并行地对多个价格的标签数量进行估算。最终，协议找出估算值
    最高的若干个价格作为最可能的众数。
    """
    
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        batch_size = 100
        frame_size = 512 # 每批次使用的帧长固定
        
        # --- 读写器初始化 ---
        all_prices = list(range(price_min, price_max + 1))
        price_batches = [all_prices[i:i + batch_size] for i in range(0, len(all_prices), batch_size)]
        
        final_counts = {}

        # --- 逐批估算 ---
        for batch in price_batches:
            if not batch: continue
            
            # --- 读写器行为: 广播对一批价格的并行估算指令 ---
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            # 读写器知道自己要如何解析回复，因此在本地创建此映射
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            
            # 广播 seed, frame_size, num_prices_in_batch (或整个batch的价格列表)
            total_bits_sent_by_reader += (seed.bit_length() + frame_size.bit_length() + num_prices_in_batch.bit_length())

            # --- [物理层模拟] ---
            # 读写器无法访问标签。此代码块模拟所有标签根据收到的指令
            # (seed, price_map) 产生匿名的、聚合的响应帧。
            physical_frame = Frame(num_slots=frame_size, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---

            # 累加标签通信开销
            total_tag_bits_sent += frame_size * num_prices_in_batch
            
            # --- 读写器分析聚合的 Frame 对象 ---
            # 读写器只接收到 physical_frame，并使用它自己的映射表来解析
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = frame_size - sum(logical_bit_vector)
                
                if num_zeros == frame_size:
                    final_counts[price] = 0
                elif num_zeros == 0:
                    final_counts[price] = float('inf')
                else:
                    estimated_count = -frame_size * math.log(num_zeros / frame_size)
                    final_counts[price] = estimated_count

        # --- 结果汇总与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        verification_data = {
            "frame_size_per_batch": frame_size,
            "batch_size": batch_size,
            "final_estimated_counts": final_counts
        }
        
        if not final_counts:
            return [], 0, total_time_ms, verification_data

        # --- 找出估算值最高的众数 ---
        max_count = 0
        # 优先处理inf的情况
        if float('inf') in final_counts.values():
            max_count = float('inf')
        else:
            # 在所有有限的计数值中寻找最大值
            valid_counts = [c for c in final_counts.values()]
            if valid_counts:
                max_count = max(valid_counts)
        
        modes = []
        if max_count > 0:
            # [修正] 返回所有并列的众数
            if max_count == float('inf'):
                modes = sorted([price for price, count in final_counts.items() if count == float('inf')])
            else:
                # 使用 math.isclose 处理浮点数比较
                modes = sorted([price for price, count in final_counts.items() if math.isclose(count, max_count)])
        
        # 将估算值转换为整数以便返回，或保持inf
        final_max_count = math.ceil(max_count) if max_count != float('inf') else float('inf')
        
        return modes, final_max_count, total_time_ms, verification_data