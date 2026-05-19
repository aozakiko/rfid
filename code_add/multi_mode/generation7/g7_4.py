import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g7_4(IProtocol):
    """
    子代协议: 动态追溯与置信度终止的混合自适应协议
    (Hybrid Adaptive Protocol with Dynamic Backtracking and Confidence-based Termination)

    本协议为一个全新的子代协议，旨在高效解决复杂的多众数查询问题。
    它严格遵循交叉与突变的设计准则，创造了一个两阶段的解决方案。

    阶段一: 粗估筛选 (交叉)
    此阶段深度融合了父代协议的核心思想。它采用父代B的“并行批量估算”策略，
    利用高效的位图向量快速扫描整个价格范围，以低成本识别出一个包含所有潜在众数的
    候选价格池。这个过程与父代A的“多哈希过滤”思想在目标上异曲同工——都是为了快速缩小问题规模。

    阶段二: 动态精确验证 (交叉)
    对于筛选出的少数顶尖候选者，本协议采用了父代B中“动态追溯”的核心机制，但将其与父代A的
    “并行处理”思想相结合。协议会为所有顶尖候选者在一个共享的帧中进行同步计数。如果任何一个
    候选者的计数值导致其逻辑信道饱和，整个帧的长度将自动加倍并对所有候选者进行重试。
    这种“一饱和、全重试”的策略确保了即使面对计数值极高且相近的多个众数，也能同步地获得
    所有顶尖候选者的精确计数值。

    全新机制: 基于置信度的提前终止 (突变)
    在交叉融合的基础上，本协议引入了一项在两个父代中都未曾出现过的全新机制。在粗估阶段结束后，
    协议会进行一次“置信度检查”。如果估算出的最高频价格的计数值，显著高于（例如超过2倍）
    第二名的计数值，并且其自身也达到了一个可信的阈值，协议将判断已找到唯一的主导众数，
    从而自信地提前终止流程，跳过整个动态精确验证阶段。这个启发式决策极大地优化了
    在存在单一主导众数场景下的协议执行效率。
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
        此函数逻辑主要借鉴自父代B的并行批量估算方法。
        """
        estimated_counts = {}
        frames_used = 0
        reader_bits = 0
        tag_bits = 0

        price_batches = [price_range[i:i + batch_size] for i in range(0, len(price_range), batch_size)]

        for batch in price_batches:
            if not batch:
                continue

            frames_used += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}

            reader_bits += (seed.bit_length() + frame_size.bit_length() + sum(p.bit_length() for p in batch))

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

            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = frame_size - sum(logical_bit_vector)

                if num_zeros == frame_size:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    estimated_counts[price] = float('inf')
                else:
                    estimated_count = -frame_size * math.log(num_zeros / frame_size)
                    estimated_counts[price] = estimated_count

        return estimated_counts, frames_used, reader_bits, tag_bits

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0

        # --- 协议超参数 ---
        # 阶段1: 粗估
        COARSE_FRAME_SIZE = 128
        COARSE_BATCH_SIZE = 64
        CANDIDATE_POOL_SIZE = 15

        # 阶段2: 精确验证
        INITIAL_ACCURATE_FRAME_SIZE = 256
        MAX_ACCURATE_FRAME_SIZE = 16384

        # 突变机制相关参数
        EARLY_TERMINATION_RATIO = 2.0
        EARLY_TERMINATION_THRESHOLD = len(tags) / (price_max - price_min + 1) * 2.5


        # --- 交叉部分：融合父代B的“并行批量估算”思想与父代A的“并行验证”思想基础 ---
        # 阶段 1: 粗估筛选
        all_prices = list(range(price_min, price_max + 1))
        coarse_counts, frames, reader_bits, tag_bits = self._run_coarse_estimation(
            tags, all_prices, COARSE_FRAME_SIZE, COARSE_BATCH_SIZE
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_tag_response_bits += tag_bits

        sorted_coarse_counts = sorted(
            coarse_counts.items(),
            key=lambda item: (item[1] != float('inf'), -item[1])
        )

        # --- 突变部分：引入“基于置信度的提前终止”全新机制 ---
        if len(sorted_coarse_counts) >= 2:
            top_candidate_price, top_count = sorted_coarse_counts[0]
            second_candidate_price, second_count = sorted_coarse_counts[1]

            # 避免除以零，并将inf视为有效高计数值
            is_dominant = (second_count == 0) or \
                          (second_count != float('inf') and top_count != float('inf') and top_count / second_count >= EARLY_TERMINATION_RATIO)
            is_significant = (top_count != float('inf') and top_count > EARLY_TERMINATION_THRESHOLD) or (top_count == float('inf'))

            if is_dominant and is_significant:
                final_count = math.ceil(top_count) if top_count != float('inf') else 9999 # 使用一个高值表示
                T_reader = total_bits_sent_by_reader * 0.025
                T_tag = total_tag_response_bits * 0.4
                T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
                total_time_ms = T_reader + T_tag + T_overhead
                verification_data = {
                    "termination_reason": "Early confidence-based termination",
                    "top_candidate_estimate": (top_candidate_price, top_count),
                    "second_candidate_estimate": (second_candidate_price, second_count)
                }
                return [top_candidate_price], final_count, total_time_ms, verification_data

        # 阶段 2: 动态精确验证
        candidate_prices = [price for price, count in sorted_coarse_counts[:CANDIDATE_POOL_SIZE] if count > 0]
        final_counts = {}

        if candidate_prices:
            current_frame_size = INITIAL_ACCURATE_FRAME_SIZE
            is_verification_complete = False
            price_to_bit_pos_map = {price: i for i, price in enumerate(candidate_prices)}
            bits_per_slot = len(candidate_prices)

            while not is_verification_complete and current_frame_size <= MAX_ACCURATE_FRAME_SIZE:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += (seed.bit_length() + current_frame_size.bit_length() + sum(p.bit_length() for p in candidate_prices))

                # [物理层模拟]
                physical_frame = Frame(num_slots=current_frame_size, bits_per_slot=bits_per_slot)
                candidate_price_set = set(candidate_prices)
                for t in tags:
                    if t.price in candidate_price_set:
                        slot_id = simple_hash(t.id, seed) % current_frame_size
                        bit_pos = price_to_bit_pos_map[t.price]
                        physical_frame.aggregate(slot_id, 1 << bit_pos)
                # [物理层模拟结束]
                total_tag_response_bits += current_frame_size * bits_per_slot

                # 检查所有候选者的逻辑信道是否饱和
                is_any_saturated = False
                temp_counts = {}
                for price, bit_pos in price_to_bit_pos_map.items():
                    logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                    num_zeros = current_frame_size - sum(logical_bit_vector)
                    if num_zeros == 0:
                        is_any_saturated = True
                        break # 只要有一个饱和，就必须重试整个批次
                    elif num_zeros == current_frame_size:
                         temp_counts[price] = 0
                    else:
                        temp_counts[price] = -current_frame_size * math.log(num_zeros / current_frame_size)

                if is_any_saturated:
                    current_frame_size *= 2 # 帧长加倍
                else:
                    is_verification_complete = True
                    final_counts = temp_counts

            # 如果达到最大帧长仍然饱和，则记录为inf
            if not is_verification_complete:
                 for price in candidate_prices:
                     final_counts[price] = float('inf')


        # --- 结果汇总与时间计算 ---
        if not final_counts:
            return [], 0, 0.0, {"stage": "final", "message": "No candidates to verify"}

        max_count = -1
        for count in final_counts.values():
             if count > max_count:
                 max_count = count

        if max_count <= 0:
            return [], 0, 0.0, {"stage": "final", "message": "No counts found"}

        modes = sorted([p for p, c in final_counts.items() if math.isclose(c, max_count)])
        display_count = math.ceil(max_count)

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "termination_reason": "Completed dynamic verification",
            "coarse_candidates": sorted_coarse_counts[:CANDIDATE_POOL_SIZE],
            "final_accurate_counts": final_counts,
            "total_frames": total_frames
        }

        return modes, display_count, total_time_ms, verification_data
