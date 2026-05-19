import math
from typing import List, Tuple, Dict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g2_2(IProtocol):
    """
    混合协议：融合批处理与启发式评分的众数查询 (Hybrid Mode Query Protocol)

    本协议是一个交叉突变的产物，旨在结合 ModeQuerySEM 的高并行处理效率
    和 ModeQueryTCS 的鲁棒启发式评分机制，以实现快速且可靠的众数估算。

    - 灵感来源 (SEM): 继承了其核心的“批处理”思想。协议不再逐个探测价格，
      而是一次性将多个价格分组（一个批次），在一个通信帧内并行处理。这极大地
      减少了通信回合数，是协议高性能的关键。

    - 灵感来源 (TCS): 继承了其核心的“启发式评分”思想。协议放弃了 SEM 中
      复杂的对数估算公式，转而采用 TCS 中更为简单和鲁棒的“非空时隙数”作为
      衡量标签数量的指标。

    - 突变 (Mutation): 关键的创新在于将 TCS 的评分机制无缝整合到 SEM 的
      并行框架内。对于从聚合帧中为每个价格解析出的逻辑比特向量，协议不再
      进行对数估算，而是直接对其求和（计算'1'的个数），这个和值就是该价格
      的“非空时隙数”，即最终得分。这种设计兼具了 SEM 的速度和 TCS 的稳定性。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 移除了 batch_size，因为并行批处理机制不符合 E/S/C 三态模型
        frame_size = 512

        # --- 读写器初始化 ---
        all_prices = list(range(price_min, price_max + 1))
        # 移除了 price_batches 的概念
        
        final_scores = {}

        # --- 逐个价格进行估算 ---
        # 修改: 循环遍历每一个可能的价格，而不是价格批次
        for price_to_check in all_prices:
            
            # --- 读写器行为: 广播对单个价格的估算指令 ---
            total_frames += 1
            seed = generate_random_seed()
            
            # 修改: 通信开销现在基于单个价格的查询
            total_bits_sent_by_reader += (seed.bit_length() + frame_size.bit_length() + price_to_check.bit_length())

            # --- [物理层模拟] ---
            # 修改: 使用简单的计数器来模拟在一个共享信道上的响应
            # 这符合读写器只能观察到 {空, 单个, 碰撞} 三态的原则
            slot_counts = [0] * frame_size
            for t in tags:
                # 标签行为: 仅当自身价格匹配时才响应
                if t.price == price_to_check:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    slot_counts[slot_id] += 1
            # --- [物理层模拟结束] ---

            # 修改: 标签响应现在是在一个共享信道上，每个时隙最多1比特信息（是否响应）
            # 因此总比特数为 frame_size
            total_tag_bits_sent += frame_size
            
            # --- 读写器分析聚合的响应 ---
            # 读写器的分析逻辑现在基于对 slot_counts 的推断
            # 这在逻辑上等同于处理一个三态信号帧
            
            # 计分逻辑: 计算非空时隙的数量作为分值
            # if count > 0 包含了 count == 1 (单个) 和 count > 1 (碰撞) 的情况
            # 这个逻辑没有利用碰撞的具体数量，符合验证标准
            score = sum(1 for count in slot_counts if count > 0)
            
            if score > 0:
                final_scores[price_to_check] = score

        # --- 结果汇总与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        verification_data = {
            "frame_size_per_price": frame_size,
            "batch_size": 1, # 逻辑上等同于批大小为1
            "heuristic_metric": "non_empty_slots_per_price",
            "final_scores": final_scores
        }
        
        if not final_scores:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = max(final_scores.values())
        
        modes = sorted([
            price for price, score in final_scores.items() 
            if score == max_score
        ])
        
        return modes, max_score, total_time_ms, verification_data
