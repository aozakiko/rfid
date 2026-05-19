# protocol/ModeQueryEE.py

import math
import time
from typing import List, Tuple, Dict
from base import Tag, IProtocol

class ModeQueryEE(IProtocol):
    """
    修正版协议：基于精确计数的“逐价清点”众数查询法
    
    本协议的核心思想保持不变：对每个可能的价格，通过反复寻找并计数拥有该价格
    的标签，来获得该价格的精确数量。此版本经过重构，严格遵守了信息隔离原则
    和逻辑正确性标准。
    
    优点:
    - 正确性极高：由于是精确计数，最终结果100%正确。
    
    缺点:
    - 效率极低：执行时间与 (价格范围 * 众数平均数量) 成正比。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_time_ms = 0.0
        price_exact_counts = {}

        # --- 协议超参数 (用于EE子协议模拟) ---
        EE_FRAME_SLOTS = 16
        TAG_ID_BITS = 32
        TIME_PER_EE_FRAME_MS = 58.8  # (2*0.2*32*1) + (16*1.0) + 30.0 = 58.8 ms
        TIME_PER_SILENT_CMD_MS = 6.4

        # --- 读写器逻辑: 遍历所有价格并发起计数指令 ---
        for p in range(price_min, price_max + 1):
            
            # --- [物理层模拟] ---
            # 读写器广播一个高级指令：“开始对价格p进行精确计数”。
            # 以下代码块模拟了物理层执行该指令的全过程，读写器无法看到内部细节。
            
            # 1. 物理层根据读写器广播的p，确定响应的标签群体
            current_price_tags_dict = {t.id: t for t in tags if t.price == p}
            
            count_for_p = 0
            time_cost_for_p = 0.0
            
            if current_price_tags_dict:
                # 2. 物理层反复执行EE子协议来寻找并“静默”最小ID的标签
                while current_price_tags_dict:
                    # a. 模拟一次EE协议以找到当前群体中的最小ID
                    if not current_price_tags_dict: break
                    
                    time_cost_for_ee_round = 0.0
                    all_ids = list(current_price_tags_dict.keys())
                    l_bound, h_bound = min(all_ids), max(all_ids) + 1

                    while (h_bound - l_bound) > 1:
                        time_cost_for_ee_round += TIME_PER_EE_FRAME_MS
                        d = (h_bound - l_bound) / EE_FRAME_SLOTS
                        if d == 0: break

                        min_slot_idx_found = float('inf')
                        for tag_id in current_price_tags_dict:
                            if l_bound <= tag_id < h_bound:
                                slot_idx = math.floor((tag_id - l_bound) / d)
                                if slot_idx < min_slot_idx_found:
                                    min_slot_idx_found = slot_idx
                        
                        if min_slot_idx_found != float('inf'):
                            first_non_empty_slot = int(min_slot_idx_found)
                            new_l = l_bound + math.ceil(d * first_non_empty_slot)
                            new_h = l_bound + math.ceil(d * (first_non_empty_slot + 1))
                            l_bound, h_bound = int(new_l), int(new_h)
                        else:
                            break
                    
                    min_id = int(l_bound)
                    found_tag = current_price_tags_dict.get(min_id)
                    time_cost_for_p += time_cost_for_ee_round

                    # b. 如果找到，计数+1，并模拟使其静默
                    if found_tag:
                        count_for_p += 1
                        del current_price_tags_dict[found_tag.id]
                        time_cost_for_p += TIME_PER_SILENT_CMD_MS
                    else:
                        # 理论上应该总能找到，除非l_bound计算有误或字典为空
                        break
            
            # --- [物理层模拟结束] ---

            # --- 读写器接收聚合结果并更新状态 ---
            # 读写器只得到一个最终计数值和总开销
            if count_for_p > 0:
                price_exact_counts[p] = count_for_p
                total_time_ms += time_cost_for_p
        
        # --- 读写器在本地进行最终结果分析 ---
        max_count = 0
        modes = []
        
        if price_exact_counts:
            max_count = max(price_exact_counts.values())
            if max_count > 0:
                # [修正] 找出所有达到最大计数的众数
                modes = sorted([
                    price for price, count in price_exact_counts.items() 
                    if count == max_count
                ])

        verification_data = {
            "ee_frame_slots": EE_FRAME_SLOTS,
            "final_exact_counts": price_exact_counts
        }
        
        return modes, max_count, total_time_ms, verification_data