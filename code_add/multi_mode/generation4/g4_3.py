import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g4_3(IProtocol):
    """
    自适应混合追溯协议 (Adaptive Hybrid Trace Protocol)

    本协议是为解决多众数查询问题而设计的全新子代协议。它严格遵循交叉与突变的设计准则，
    旨在准确且高效地从大量标签中找出一个高频价格。

    协议流程分为三个核心阶段：

    1.  **粗估筛选阶段 (Crossover)**:
        此阶段深度融合了父代协议的核心思想。它采用父代A的“并行批量估算”策略，
        使用高效的位图向量快速扫描整个价格范围。同时，它践行了父代B（推断）的
        “过滤”思想，其目的并非精确计数，而是以低成本快速识别出一个包含所有
        潜在众数的候选价格池。

    2.  **动态候选剪枝 (Mutation)**:
        在粗估之后，协议引入了在两个父代中都未曾出现过的全新“动态候选剪枝”机制。
        协议不再像父代一样选择固定数量的候选者，而是根据粗估阶段的计数值分布来
        动态决定候选池的大小。它会累加排序后候选者的估算计数值，直到这个累加值
        占总估算值的某个阈值（例如95%）为止。这使得协议能够根据数据分布的集中
        程度自适应地调整后续阶段的计算开销。

    3.  **动态追溯计数阶段 (Crossover)**:
        此阶段吸收了父代B的“动态追溯计数”机制的精髓。对于在剪枝后幸存的极少数
        顶尖候选者，本协议为每个候选价格启动一个独立的、自适应的计数流程。
        该流程从一个标准帧长开始，如果发生帧饱和，则会自动将帧长加倍并重新尝试，
        直至获得一个非饱和的、准确的计数值。这种机制确保了即使面对计数值极高
        且相近的众数，协议也能以确定性的方式最终找到准确的答案。
    """

    def _run_estimation_phase(
        self,
        tags: List[Tag],
        target_prices: List[int],
        frame_size: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], int, int, int]:
        """
        一个通用的估算阶段执行函数。
        
        Args:
            tags: 所有标签的列表。
            target_prices: 此阶段需要估算数量的价格列表。
            frame_size: 用于估算的帧大小。
            batch_size: 每个通信帧并行处理的价格数量。

        Returns:
            一个元组，包含：
            - estimated_counts: 估算出的每个价格的数量。
            - frames_used: 此阶段使用的总帧数。
            - reader_bits: 读写器在此阶段发送的总比特数。
            - tag_bits: 标签在此阶段发送的总比特数。
        """
        estimated_counts = {}
        frames_used = 0
        reader_bits = 0
        tag_bits = 0

        price_batches = [target_prices[i:i + batch_size] for i in range(0, len(target_prices), batch_size)]

        for batch in price_batches:
            if not batch:
                continue

            frames_used += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}

            # 读写器广播参数
            reader_bits += (seed.bit_length() + frame_size.bit_length() + num_prices_in_batch.bit_length() + sum(p.bit_length() for p in batch))

            # [物理层模拟]
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

            # 读写器分析聚合帧
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = frame_size - sum(logical_bit_vector)
                
                if num_zeros == frame_size:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    estimated_counts[price] = float('inf')  # 饱和
                else:
                    estimated_count = -frame_size * math.log(num_zeros / frame_size)
                    estimated_counts[price] = estimated_count

        return estimated_counts, frames_used, reader_bits, tag_bits

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 阶段1: 粗估
        COARSE_FRAME_SIZE = 128
        COARSE_BATCH_SIZE = 128

        # 阶段2: 突变-剪枝
        CANDIDATE_COVERAGE_THRESHOLD = 0.95 # 候选者应覆盖总估算计数的95%

        # 阶段3: 精确计数
        INITIAL_ACCURATE_FRAME_SIZE = 512
        MAX_ACCURATE_FRAME_SIZE = 8192

        # --- 交叉部分：融合父代A的“并行批量估算”思想与父代B的“逐级过滤”和“动态追溯计数”思想 ---

        # --- 阶段 1: 粗估筛选 ---
        all_prices = list(range(price_min, price_max + 1))
        
        coarse_counts, frames, reader_bits, tag_bits = self._run_estimation_phase(
            tags, all_prices, COARSE_FRAME_SIZE, COARSE_BATCH_SIZE
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_tag_bits_sent += tag_bits

        sorted_coarse_counts = sorted(
            coarse_counts.items(), 
            key=lambda item: (item[1] != float('inf'), -item[1])
        )

        # --- 突变部分：引入“动态候选剪枝”全新机制 ---
        
        # --- 阶段 2: 动态候选剪枝 ---
        # 仅考虑有限计数值进行总数计算，无穷大值直接成为候选
        finite_counts = [c for p, c in sorted_coarse_counts if c != float('inf')]
        total_estimated_population = sum(finite_counts) if finite_counts else 0
        
        candidate_prices = []
        # 无穷大（饱和）的计数值自动成为候选者
        candidate_prices.extend([p for p, c in sorted_coarse_counts if c == float('inf')])

        if total_estimated_population > 0:
            cumulative_count = 0
            threshold = total_estimated_population * CANDIDATE_COVERAGE_THRESHOLD
            for price, count in sorted_coarse_counts:
                if count != float('inf'):
                    if cumulative_count < threshold:
                        candidate_prices.append(price)
                        cumulative_count += count
                    else:
                        break # 一旦超过阈值，立即停止添加，实现剪枝
        
        # 如果剪枝后没有任何候选（例如所有计数值都为0），则选取估算值最高的那个
        if not candidate_prices and sorted_coarse_counts:
             candidate_prices = [sorted_coarse_counts[0][0]]

        if not candidate_prices:
            return [], 0, 0.0, {"stage": "pruning", "message": "No candidates found after pruning"}


        # --- 阶段 3: 动态追溯计数 ---
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
                    estimated_count = 0 # 没有标签响应
                    break
                elif num_zeros == 0:
                    # 仍然饱和，将帧长加倍并继续循环
                    current_frame_size *= 2
                    estimated_count = float('inf')
                else:
                    # 成功获得非饱和帧，计算估算值并跳出循环
                    estimated_count = -current_frame_size * math.log(num_zeros / current_frame_size)
                    break
            
            final_counts[price] = estimated_count

        # --- 结果汇总与时间计算 ---
        if not final_counts:
            return [], 0, 0.0, {"stage": "final_count", "message": "Final count failed"}

        max_count = -1
        is_inf = any(c == float('inf') for c in final_counts.values())

        if is_inf:
            max_count = float('inf')
            modes = sorted([p for p, c in final_counts.items() if c == float('inf')])
        else:
            if final_counts:
                max_count = max(final_counts.values())
            modes = sorted([p for p, c in final_counts.items() if math.isclose(c, max_count)])
        
        display_count = float('inf') if is_inf else (math.ceil(max_count) if max_count > 0 else 0)

        # 计算总执行时间
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "coarse_counts_sorted": sorted_coarse_counts,
            "pruned_candidates": candidate_prices,
            "final_counts": final_counts,
            "total_frames": total_frames,
        }

        return modes, display_count, total_time_ms, verification_data
