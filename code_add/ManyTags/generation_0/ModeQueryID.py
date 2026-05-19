# protocol/ModeQueryID.py

from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol

class ModeQueryID(IProtocol):
    """
    最终修正版协议：基于逐一识别的精确众数查询 (Singulation-based Exact Mode Query)

    本协议模拟了一个最基础但100%准确的众数查找方法。它通过执行一个完整的
    “逐一识别与读取”(Singulation and Read)流程来实现。该流程会发现场内的
    每一个标签，并单独请求其价格数据。此方法保证了结果的绝对准确性，
    是衡量其他更高效协议性能的基准。
    """
    
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 (用于精确计数成本模拟) ---
        # 假设每次“识别并读取”一个标签的平均开销
        BITS_PER_SINGULATION_QUERY = 96  # 识别一个标签通常需要更复杂的指令序列
        BITS_PER_PRICE_REPLY = 32      # 标签回复其价格数据

        # =================================================================================
        # 协议主体: 读写器发起一次完整的“盘点并读取”
        # =================================================================================

        # --- 读写器行为: 广播 "开始全面盘点并读取价格" 指令 ---
        # 假设该指令是一个简单的“开始”信号，成本计入总开销
        total_frames += 1

        # --- [物理层模拟] ---
        # 读写器无法直接访问标签。此代码块模拟了物理层收到指令后，
        # 逐一识别场内所有标签并读取其价格的完整过程。
        
        # 1. 统计场内标签总数
        num_tags = len(tags)
        
        # 2. 计算精确识别并读取所有标签的总开销
        # 每次成功的识别和读取都可视为一次独立的交互或“帧”
        total_frames += num_tags
        total_bits_sent_by_reader += num_tags * BITS_PER_SINGULATION_QUERY
        total_tag_bits_sent += num_tags * BITS_PER_PRICE_REPLY
        
        # 3. 收集所有标签回复的价格，形成一个匿名列表
        all_prices_reported = [t.price for t in tags]
        
        # --- [物理层模拟结束] ---

        # --- 读写器接收聚合结果: 一个价格列表 ---
        # 读写器不知道哪个价格来自哪个标签，只收到了所有价格的集合。
        
        # --- 读写器在本地进行后处理以找出众数 ---
        price_counts = defaultdict(int)
        for price in all_prices_reported:
            price_counts[price] += 1

        # --- 最终结果处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        # 每次交互都有帧间开销
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "method": "Full Singulation and Read",
            "tags_identified": num_tags,
            "final_exact_counts": dict(price_counts)
        }
        
        if not price_counts:
            return [], 0, total_time_ms, verification_data

        # 1. 找到最大计数值
        max_count = max(price_counts.values())
        
        # 2. 找出所有达到最大计数值的众数
        modes = sorted([price for price, count in price_counts.items() if count == max_count])
        
        return modes, max_count, total_time_ms, verification_data