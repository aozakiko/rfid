import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol,simple_hash



class ModeQueryTKQ(IProtocol):
    """
    实现了基于TKQ思想的价格众数查询协议。
    """
    
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        # *** 修改点 1：更新了返回类型以包含用于验证的字典 (Dict) ***
        total_time_ms = 0.0
        
        # ----------- 阶段一: 读写器初始化 -----------
        num_price_values = price_max - price_min + 1
        frame_size = num_price_values  # 帧长f设置为价格值的总数
        sog_length_v = 32              # SOG字符串长度
        omega_w = 1.2897               # 估算器常量
        num_rounds = 3                 # 为提高估算稳定性，执行多轮
        
        price_stats = {p: {"sum_L": 0.0, "num_slots": 0, "estimated_n": 0.0} 
                       for p in range(price_min, price_max + 1)}

        # --- 多轮估算循环 ---
        for _ in range(num_rounds):
            # ----------- 阶段二: 指令广播 -----------
            seed_r = generate_random_seed()
            
            bits_to_send = (seed_r.bit_length() + frame_size.bit_length() + sog_length_v.bit_length())
            time_send_ms = bits_to_send * 0.025
            total_time_ms += time_send_ms
            total_time_ms += 30.0 # 帧间开销

            # ----------- 阶段三: 标签处理 (模拟) -----------
            tag_replies = []
            for t in tags:
                # 1. 生成SOG字符串
                random_bits = simple_hash(t.id, seed_r)
                
                # --- [内联 _find_leading_zeros 逻辑] ---
                mu = 0
                count = 0
                for i in range(sog_length_v - 1, -1, -1):
                    if (random_bits >> i) & 1:
                        mu = count
                        break
                    count += 1
                else:
                    mu = sog_length_v
                # --- [内联结束] ---

                sog_string = (1 << (sog_length_v - 1 - mu)) if mu < sog_length_v else 0

                # 2. 根据价格选择时隙
                slot_id = simple_hash(t.price, seed_r) % frame_size
                tag_replies.append({'slot_id': slot_id, 'sog_string': sog_string})

            # ----------- 阶段四: 物理帧聚合与回复时间 -----------
            physical_frame = Frame(num_slots=frame_size, bits_per_slot=sog_length_v)
            for reply in tag_replies:
                physical_frame.aggregate(reply['slot_id'], reply['sog_string'])
                
            time_reply_ms = frame_size * sog_length_v * 0.4
            total_time_ms += time_reply_ms

            # ----------- 阶段五: 结果分析 -----------
            # 1. 预测价格到时隙的映射，识别同构时隙
            slot_to_prices_map = defaultdict(list)
            for p in range(price_min, price_max + 1):
                slot_idx = simple_hash(p, seed_r) % frame_size
                slot_to_prices_map[slot_idx].append(p)

            # 2. 处理同构时隙
            for slot_idx, prices in slot_to_prices_map.items():
                if len(prices) == 1:
                    price_val = prices[0]
                    combined_signal = physical_frame.slots[slot_idx]
                    
                    # b. 计算前导'1'的长度 L
                    # --- [内联 _find_leading_ones 逻辑] ---
                    L = 0
                    count = 0
                    for i in range(sog_length_v - 1, -1, -1):
                        if not ((combined_signal >> i) & 1):
                            L = count
                            break
                        count += 1
                    else:
                        L = sog_length_v
                    # --- [内联结束] ---
                    
                    # c. 更新统计数据
                    price_stats[price_val]["sum_L"] += L
                    price_stats[price_val]["num_slots"] += 1
                    
                    # d. 重新估算数量
                    s_p = price_stats[price_val]["num_slots"]
                    if s_p > 0:
                        avg_L = price_stats[price_val]["sum_L"] / s_p
                        estimated_n = omega_w * (2 ** avg_L)
                        price_stats[price_val]["estimated_n"] = estimated_n

        # ----------- 最终处理 (Post-processing) -----------
        # *** 修改点 2：创建用于验证的字典 ***
        verification_data = {
            "frame_size": frame_size,
            "bits_per_slot": sog_length_v
        }

        if not price_stats:
            return [], 0, total_time_ms, verification_data

        max_count = 0
        for stats in price_stats.values():
            if stats["estimated_n"] > max_count:
                max_count = stats["estimated_n"]
        
        mode_prices = []
        if max_count > 0:
            all_modes = sorted([price for price, stats in price_stats.items() 
                                if math.isclose(stats["estimated_n"], max_count)])
            if all_modes:
                # 只返回最大的众数
                mode_prices = [all_modes[-1]]
        
        # *** 修改点 3：在返回元组中增加验证字典 ***
        return mode_prices, math.ceil(max_count), total_time_ms, verification_data