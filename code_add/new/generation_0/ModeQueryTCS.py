# protocol/ModeQueryTCS.py

from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed,simple_hash

class ModeQueryTCS(IProtocol):
    """
    修正版协议：基于鲁棒启发式指标的TCS众数查询
    
    本协议实现了TCS（Tag Collision Separation）的基本思想，但进行了关键修正以
    保证其逻辑正确性并严格遵守信息隔离原则。协议迭代所有可能的价格，并为每个
    价格运行一次独立的帧通信，通过一个更鲁棒的启发式指标——“非空时隙数”
    （成功数+冲突数），来评估每个价格下的标签群体规模，从而找出最可能的众数。
    """
    
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        frame_size = 128
        
        # [修正] 使用更有意义的变量名 price_to_score_map
        price_to_score_map = {}

        # ----------- 迭代查询所有价格 -----------
        for p in range(price_min, price_max + 1):
            
            # --- 读写器行为: 广播对价格 p 的探测指令 ---
            total_frames += 1
            seed_s = generate_random_seed()
            # 读写器广播Select(p), frame_size, seed
            total_bits_sent_by_reader += p.bit_length() + frame_size.bit_length() + seed_s.bit_length()

            # --- [物理层模拟] ---
            # 读写器无法访问标签私有数据。此代码块模拟所有标签的匿名、聚合响应。
            # 读写器能观察到的最终结果是 slot_states 数组。
            slot_states = [0] * frame_size  # 0: 空闲, 1: 成功, >1: 冲突
            
            # 1. 标签根据广播的 p 和自身的 price 判断是否响应
            # 2. 响应的标签根据广播的 seed 和自身的 id 计算时隙并回应
            for t in tags:
                if t.price == p:
                    slot_id = simple_hash(t.id, seed_s) % frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---

            # 探测阶段，每个时隙回复1bit即可
            total_tag_bits_sent += frame_size * 1

            # --- 读写器分析聚合信号 ---
            # [修正] 采用更鲁棒的“非空时隙数”作为评分
            num_non_empty_slots = sum(1 for count in slot_states if count > 0)
            
            if num_non_empty_slots > 0:
                price_to_score_map[p] = num_non_empty_slots

        # --- 最终处理 (Post-processing) ---
        
        # --- 时间成本计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": frame_size,
            "heuristic_metric": "non_empty_slots",
            "final_scores": price_to_score_map
        }
        
        if not price_to_score_map:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = max(price_to_score_map.values())
        
        # [修正] 返回所有并列的众数，并确保排序
        modes = sorted([
            price for price, score in price_to_score_map.items() 
            if score == max_score
        ])
        
        # 返回众数列表和它们对应的最高分
        return modes, max_score, total_time_ms, verification_data