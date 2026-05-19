import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g1_5(IProtocol):
    """
    子代协议：基于两阶段过滤与动态帧调整的众数查询

    本协议通过交叉与突变，融合了父代协议的思想并引入了全新机制，以高效准确地解决多众数查询问题。

    - 交叉思想:
      协议采用两阶段设计。第一阶段（粗粒度过滤）借鉴了父代B将大范围价格映射到小范围帧的思想，
      快速筛选出可能包含众数的“热门”价格区间。第二阶段（细粒度验证）则借鉴了父代A对特定价格
      进行独立、精确探测的思想，仅对第一阶段筛选出的候选价格进行验证，从而避免了对整个价格
      空间的暴力扫描，大幅提升了效率。

    - 突变创新:
      本协议引入了全新的“基于冲突检测的动态帧长调节机制”。在第一阶段，读写器不仅识别出
      哪些时隙非空，还会进一步区分“成功”时隙（仅一个标签响应）和“冲突”时隙（多个标签响应）。
      在第二阶段验证时，协议会为来自“冲突”时隙的候选价格分配一个更大的帧，而为来自“成功”
      时隙的候选价格分配一个较小的帧。这种动态资源分配策略，使得协议能将更多通信资源集中
      用于分辨最可能成为众数的高密度价格区间，从而在保证准确率的同时进一步优化了时间性能。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1  # 默认探测帧的时隙回复为1bit

        # --- 协议超参数 ---
        # 阶段一：过滤帧的帧长，用于将价格空间进行哈希映射
        filter_frame_size = 256
        # 阶段二：为高可能性的候选价格（来自冲突时隙）分配的大帧长
        detail_frame_size_large = 128
        # 阶段二：为低可能性的候选价格（来自成功时隙）分配的小帧长
        detail_frame_size_small = 32

        # ==================================================================
        #  阶段一: 粗粒度过滤 - 识别包含标签的热门价格区间
        # ==================================================================

        # --- 交叉部分：融合父代A的迭代验证思想与父代B的价格哈希映射思想 ---
        # 此处借鉴父代B，将整个价格范围通过哈希函数映射到一个较小的过滤帧中，
        # 以快速定位哪些价格“桶”是活跃的，避免了父代A对每个价格都进行探测的低效。

        # --- 读写器行为: 广播过滤指令 ---
        total_frames += 1
        seed_filter = generate_random_seed()
        total_bits_sent_by_reader += filter_frame_size.bit_length() + seed_filter.bit_length()

        # --- [物理层模拟] ---
        # 标签根据自身价格进行哈希，在过滤帧的对应时隙中响应
        filter_slot_states = [0] * filter_frame_size  # 0: 空闲, 1: 成功, >1: 冲突
        for t in tags:
            slot_id = simple_hash(t.price, seed_filter) % filter_frame_size
            filter_slot_states[slot_id] += 1
        total_slots += filter_frame_size
        # --- [物理层模拟结束] ---

        # --- 读写器分析聚合信号 ---
        # 识别出所有非空时隙和发生冲突的时隙
        non_empty_slots = {i for i, count in enumerate(filter_slot_states) if count > 0}
        
        # --- 突变部分：引入基于第一阶段冲突检测的动态帧长调节机制 ---
        # 这是突变机制的数据基础。读写器不仅要知道一个时隙是否非空，还要记录它是否发生了冲突。
        # 这个信息将在第二阶段用来动态决定验证帧的长度。
        collided_slots = {i for i, count in enumerate(filter_slot_states) if count > 1}


        # ==================================================================
        #  阶段二: 细粒度验证 - 对候选价格进行精确评分
        # ==================================================================
        
        candidate_scores = {}

        # 读写器迭代所有可能的价格，判断其是否落入第一阶段识别出的“热门”时隙中
        for p in range(price_min, price_max + 1):
            # 读写器在本地计算价格 p 应该映射到哪个时隙
            slot_p = simple_hash(p, seed_filter) % filter_frame_size

            # 如果该时隙在第一阶段被确定为非空，则价格 p 成为候选者
            if slot_p in non_empty_slots:
                
                # --- 突变部分：引入基于第一阶段冲突检测的动态帧长调节机制 ---
                # 根据价格p所在的时隙在第一阶段是否发生冲突，动态选择验证帧的长度。
                # 为冲突时隙（更可能包含众数）分配更大的帧以获得更准确的评分。
                current_detail_frame_size = detail_frame_size_large if slot_p in collided_slots else detail_frame_size_small

                # --- 交叉部分：融合父代A的迭代验证思想与父代B的价格哈希映射思想 ---
                # 此处借鉴父代A，为每个被筛选出的候选价格 p，进行一次独立的帧通信以精确评估其数量。
                # 这种方法保证了准确性，且因为只对少数候选者执行，所以效率远高于纯粹的父代A。
                
                # --- 读写器行为: 广播对候选价格 p 的探测指令 ---
                total_frames += 1
                seed_detail = generate_random_seed()
                total_bits_sent_by_reader += p.bit_length() + current_detail_frame_size.bit_length() + seed_detail.bit_length()

                # --- [物理层模拟] ---
                detail_slot_states = [0] * current_detail_frame_size
                for t in tags:
                    if t.price == p:
                        slot_id = simple_hash(t.id, seed_detail) % current_detail_frame_size
                        detail_slot_states[slot_id] += 1
                total_slots += current_detail_frame_size
                # --- [物理层模拟结束] ---

                # --- 读写器分析聚合信号 ---
                # 使用父代A中鲁棒的“非空时隙数”作为评分标准
                num_non_empty = sum(1 for count in detail_slot_states if count > 0)
                if num_non_empty > 0:
                    candidate_scores[p] = num_non_empty
        
        # ==================================================================
        #  最终处理 (Post-processing)
        # ==================================================================
        
        # --- 时间成本计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        # 假设所有通信都使用1bit回复
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "filter_frame_size": filter_frame_size,
            "detail_frame_size_large": detail_frame_size_large,
            "detail_frame_size_small": detail_frame_size_small,
            "num_candidates": len(candidate_scores),
            "final_scores": candidate_scores
        }

        if not candidate_scores:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = max(candidate_scores.values())
        
        # 返回所有并列的众数，并确保排序
        modes = sorted([
            price for price, score in candidate_scores.items()
            if score == max_score
        ])
        
        return modes, max_score, total_time_ms, verification_data
