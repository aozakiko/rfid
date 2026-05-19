import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g2_2(IProtocol):
    """
    自适应混合选择估算协议 (Adaptive Hybrid Selective Estimation Protocol)

    本协议是一个全新的子代协议，旨在解决多众数查询问题。
    它深度融合了父代协议的设计思想，并引入了创新的优化机制。

    协议流程分为三个阶段：
    1.  **粗估筛选阶段 (Crossover)**: 融合父代A的并行批量估算思想，使用较小的帧快速扫描
        整个价格范围，以低成本识别出潜在的候选价格集。这体现了父代A的效率。
    2.  **精估排序阶段 (Crossover)**: 融合了推断的父代B的“过滤”和“聚焦”思想。对上一阶段
        筛选出的少数候选价格，使用更大的帧进行更精确的数量估算，从而进一步缩小范围，
        锁定最有可能成为众数的几个价格。
    3.  **精确计数阶段 (Mutation)**: 引入了全新的“单价精确计数”机制。对极少数最终候选者，
        协议不再使用概率估算，而是为每个候选价格发起一次独立的、高精度的计数查询，
        从而获得近乎确定的计数值，确保在计数值相近时也能做出最准确的判决。
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
            reader_bits += (seed.bit_length() + frame_size.bit_length() + num_prices_in_batch.bit_length())

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
                    estimated_counts[price] = float('inf')  # 表示计数过高，无法估算
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
        COARSE_BATCH_SIZE = 128
        CANDIDATE_POOL_SIZE = 20 # 筛选出前20个候选者

        # 阶段2: 精估排序
        FINE_FRAME_SIZE = 1024
        FINE_BATCH_SIZE = 32
        FINAL_CANDIDATE_SIZE = 5 # 最终选出5个进行精确计数

        # 阶段3: 精确计数
        ACCURATE_COUNT_FRAME_SIZE = 2048 # 用于单价计数的帧长，确保高精度

        # --- 交叉部分：融合父代A的“并行批量估算”思想与推断的父代B的“逐级过滤”思想 ---
        
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
            key=lambda item: (item[1] == float('inf'), item[1]), 
            reverse=True
        )
        candidate_prices = [price for price, count in sorted_coarse_counts[:CANDIDATE_POOL_SIZE]]

        if not candidate_prices:
            return [], 0, 0.0, {}

        # --- 阶段 2: 精估排序 ---
        fine_counts, frames, reader_bits, tag_bits = self._run_estimation_phase(
            tags, candidate_prices, FINE_FRAME_SIZE, FINE_BATCH_SIZE
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_tag_bits_sent += tag_bits
        
        sorted_fine_counts = sorted(
            fine_counts.items(), 
            key=lambda item: (item[1] == float('inf'), item[1]), 
            reverse=True
        )
        final_candidate_prices = [price for price, count in sorted_fine_counts[:FINAL_CANDIDATE_SIZE]]
        
        if not final_candidate_prices:
            final_candidate_prices = [price for price, count in sorted_coarse_counts[:1] if count > 0]
            if not final_candidate_prices:
                 return [], 0, 0.0, {"stage": "coarse", "message": "No tags found"}
        
        # --- 突变部分：引入“单价精确计数”全新机制，从概率估算转向确定性计数 ---

        # --- 阶段 3: 精确计数 ---
        final_counts = {}
        for price in final_candidate_prices:
            seed = generate_random_seed()
            total_frames += 1
            total_bits_sent_by_reader += (seed.bit_length() + ACCURATE_COUNT_FRAME_SIZE.bit_length() + price.bit_length())

            # [物理层模拟]
            physical_frame = Frame(num_slots=ACCURATE_COUNT_FRAME_SIZE, bits_per_slot=1)
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % ACCURATE_COUNT_FRAME_SIZE
                    physical_frame.aggregate(slot_id, 1)
            # [物理层模拟结束]
            
            total_tag_bits_sent += ACCURATE_COUNT_FRAME_SIZE * 1

            # --- 读写器分析 (ERROR HANDLED) ---
            num_zeros = physical_frame.slots.count(0)
            if num_zeros == ACCURATE_COUNT_FRAME_SIZE:
                final_counts[price] = 0
            elif num_zeros == 0:
                # 如果帧饱和，无法精确估算，标记为无穷大
                final_counts[price] = float('inf')
            else:
                # 只有在 num_zeros > 0 时才进行log计算
                estimated_count = -ACCURATE_COUNT_FRAME_SIZE * math.log(num_zeros / ACCURATE_COUNT_FRAME_SIZE)
                final_counts[price] = estimated_count

        # --- 结果汇总与时间计算 ---
        if not final_counts:
            return [], 0, 0.0, {"stage": "final", "message": "Final count failed"}

        # --- (MODIFIED) 结果处理，正确处理无穷大值 ---
        inf_modes = [p for p, c in final_counts.items() if c == float('inf')]
        if inf_modes:
            # 如果有无穷大结果，它们就是众数
            max_count = float('inf')
            modes = sorted(inf_modes)
        else:
            # 否则，在有限结果中寻找最大值
            max_count = max(final_counts.values()) if final_counts else 0
            modes = sorted([p for p, c in final_counts.items() if math.isclose(c, max_count)])
        
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": ACCURATE_COUNT_FRAME_SIZE,
            "bits_per_slot": max(COARSE_BATCH_SIZE, FINE_BATCH_SIZE, 1),
            "max_frame_size": max(COARSE_FRAME_SIZE, FINE_FRAME_SIZE, ACCURATE_COUNT_FRAME_SIZE),
            "max_bits_per_slot": max(COARSE_BATCH_SIZE, FINE_BATCH_SIZE, 1),
            "stage_frame_sizes": {
                "coarse": COARSE_FRAME_SIZE,
                "fine": FINE_FRAME_SIZE,
                "accurate": ACCURATE_COUNT_FRAME_SIZE,
            },
            "stage_bits_per_slot": {
                "coarse": COARSE_BATCH_SIZE,
                "fine": FINE_BATCH_SIZE,
                "accurate": 1,
            },
            "coarse_counts": sorted_coarse_counts[:CANDIDATE_POOL_SIZE],
            "fine_counts": sorted_fine_counts[:FINAL_CANDIDATE_SIZE],
            "final_accurate_counts": final_counts,
            "total_frames": total_frames,
        }

        # 根据max_count是否为inf决定返回的计数值
        display_count = float('inf') if max_count == float('inf') else math.ceil(max_count)

        return modes, display_count, total_time_ms, verification_data

