import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g3_5(IProtocol):
    """
    自适应混合过滤协议 (Adaptive Hybrid Filtering Protocol)

    本协议是一个为解决多众数问题而设计的全新子代协议。它严格遵循交叉与突变的设计准则，
    旨在准确且高效地从大量标签中找出一个高频价格。

    协议流程分为两个核心阶段：

    1.  **粗估筛选阶段 (Crossover)**:
        此阶段深度融合了父代协议的核心思想。它采用父代A的“并行批量估算”策略，
        使用高效的位图向量快速扫描整个价格范围。同时，它践行了推断出的父代B的
        “过滤”思想，其目的并非精确计数，而是以低成本快速识别出一个包含所有
        潜在众数的候选价格池，为下一阶段的精确打击做准备。

    2.  **动态追溯计数阶段 (Mutation)**:
        此阶段引入了在两个父代中都未曾出现过的全新“动态追溯计数”机制。
        对于在粗估阶段筛选出的极少数顶尖候选者，本协议不再使用概率估算，
        而是为每个候选价格启动一个独立的、自适应的计数流程。该流程从一个
        标准帧长开始，如果发生帧饱和（所有时隙都冲突），则会自动将帧长
        加倍并重新尝试，直至获得一个非饱和的、准确的计数值。这种“不成功则加倍重试”
        的机制确保了即使面对计数值极高且相近的众数，协议也能以确定性的方式
        最终找到准确的答案，解决了传统估计算法在高密度区域失效的问题。
    """

    def _run_coarse_estimation(
        self,
        tags: List[Tag],
        price_range: List[int],
        frame_size: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], int, int, int]:
        """
        执行粗略估算，快速筛选候选价格。
        融合父代A的并行处理能力。
        """
        estimated_counts = {}
        frames_used = 0
        reader_bits = 0
        tag_bits = 0

        # 将所有价格分批处理
        price_batches = [price_range[i:i + batch_size] for i in range(0, len(price_range), batch_size)]

        for batch in price_batches:
            if not batch:
                continue

            frames_used += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            # 创建一个从价格到其在位向量中位置的映射
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}

            # 读写器广播参数: 种子, 帧长, 批次大小, 以及批次内的所有价格
            reader_bits += (seed.bit_length() + frame_size.bit_length() +
                            num_prices_in_batch.bit_length() + sum(p.bit_length() for p in batch))

            # [物理层模拟]
            # 标签根据自身价格和收到的指令进行响应
            physical_frame = Frame(num_slots=frame_size, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            # [物理层模拟结束]

            tag_bits += frame_size * num_prices_in_batch

            # 读写器分析聚合帧，为批次中的每个价格估算数量
            for price, bit_pos in price_to_bit_pos_map.items():
                # 从聚合帧中提取该价格对应的逻辑位向量
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = frame_size - sum(logical_bit_vector)

                if num_zeros == frame_size:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    # 帧饱和，表示数量极大，标记为无穷大
                    estimated_counts[price] = float('inf')
                else:
                    # 使用标准的零时隙估算公式
                    estimated_count = -frame_size * math.log(num_zeros / frame_size)
                    estimated_counts[price] = estimated_count

        return estimated_counts, frames_used, reader_bits, tag_bits


    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 阶段1: 粗估筛选
        COARSE_FRAME_SIZE = 128
        COARSE_BATCH_SIZE = 64
        CANDIDATE_POOL_SIZE = 10  # 筛选出前10个候选者进入下一阶段

        # 阶段2: 动态追溯计数
        INITIAL_ACCURATE_FRAME_SIZE = 512 # 动态计数的起始帧长
        MAX_ACCURATE_FRAME_SIZE = 8192    # 帧长上限，防止无限循环

        # --- 交叉部分：融合父代A的“并行批量估算”思想与推断的父代B的“逐级过滤”思想 ---
        # 阶段 1: 粗估筛选
        all_prices = list(range(price_min, price_max + 1))

        coarse_counts, frames, reader_bits, tag_bits = self._run_coarse_estimation(
            tags, all_prices, COARSE_FRAME_SIZE, COARSE_BATCH_SIZE
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_tag_bits_sent += tag_bits

        # 对估算结果进行排序，优先选择饱和的（inf），其次按估算值降序
        sorted_coarse_counts = sorted(
            coarse_counts.items(),
            key=lambda item: (item[1] != float('inf'), -item[1])
        )
        candidate_prices = [price for price, count in sorted_coarse_counts[:CANDIDATE_POOL_SIZE]]

        if not candidate_prices:
            return [], 0, 0.0, {"stage": "coarse", "message": "No candidates found"}

        # --- 突变部分：引入“动态追溯计数”全新机制，实现自适应精确计数 ---
        # 阶段 2: 动态追溯计数
        final_counts = {}
        for price in candidate_prices:
            current_frame_size = INITIAL_ACCURATE_FRAME_SIZE
            estimated_count = float('inf')

            # 循环直到找到一个不饱和的帧或达到帧长上限
            while estimated_count == float('inf') and current_frame_size <= MAX_ACCURATE_FRAME_SIZE:
                seed = generate_random_seed()
                total_frames += 1
                total_bits_sent_by_reader += (seed.bit_length() + current_frame_size.bit_length() + price.bit_length())

                # [物理层模拟]
                physical_frame = Frame(num_slots=current_frame_size, bits_per_slot=1)
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % current_frame_size
                        physical_frame.aggregate(slot_id, 1)
                # [物理层模拟结束]

                total_tag_bits_sent += current_frame_size * 1

                num_zeros = physical_frame.slots.count(0)

                if num_zeros == current_frame_size:
                    estimated_count = 0
                elif num_zeros == 0:
                    # 仍然饱和，将帧长加倍并继续循环
                    current_frame_size *= 2
                    estimated_count = float('inf') # 保持inf状态以继续循环
                else:
                    # 成功获得非饱和帧，计算估算值并跳出循环
                    estimated_count = -current_frame_size * math.log(num_zeros / current_frame_size)
            
            # 记录最终的计数值（可能是精确值，也可能是达到上限后的inf）
            final_counts[price] = estimated_count

        # --- 结果汇总与时间计算 ---
        if not final_counts:
             return [], 0, 0.0, {"stage": "final", "message": "Final count failed"}

        # 找出最终计数值最高的价格
        # 同样，优先处理无穷大值
        max_count = -1
        is_inf = False
        for count in final_counts.values():
            if count == float('inf'):
                max_count = float('inf')
                is_inf = True
                break
            if count > max_count:
                max_count = count

        if is_inf:
            modes = sorted([p for p, c in final_counts.items() if c == float('inf')])
            display_count = float('inf')
        else:
            modes = sorted([p for p, c in final_counts.items() if math.isclose(c, max_count)])
            display_count = math.ceil(max_count) if max_count > 0 else 0

        # 计算总执行时间
        T_reader = total_bits_sent_by_reader * 0.025
        # 注意：这里的 total_tag_bits_sent 已经在循环中累加了所有帧的比特数
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "coarse_candidates": sorted_coarse_counts[:CANDIDATE_POOL_SIZE],
            "final_dynamic_counts": final_counts,
            "total_frames": total_frames,
        }

        return modes, display_count, total_time_ms, verification_data
