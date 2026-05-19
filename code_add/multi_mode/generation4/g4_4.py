import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g4_4(IProtocol):
    """
    混合自适应众数查找协议 (Hybrid Adaptive Mode Protocol, HAMP)

    本协议是一个为解决复杂多众数问题而设计的全新子代协议。
    它严格遵循交叉与突变的设计原则，深度融合了父代协议A和B的优点，
    并引入了全新的动态候选者扩展机制，以提高在众数值相近场景下的准确率。

    协议流程分为三个核心阶段：
    1.  **多哈希过滤阶段 (Crossover)**:
        融合了父代B的“多哈希布隆过滤”思想。此阶段使用多个哈希函数快速扫描整个价格
        范围，以极高的效率筛除大量不存在或低频的价格，生成一个高质量的初始候选集。

    2.  **批量估算排序阶段 (Crossover)**:
        融合了父代A的“并行批量估算”架构。协议对上一阶段筛选出的候选价格，使用
        父代A中基于零时隙的对数估算方法 (`-F * log(Z/F)`) 进行定量估算。这结合了
        父代B的过滤效率与父代A的定量分析能力，能为候选者提供更精细的排序依据。

    3.  **自适应验证阶段 (Mutation)**:
        引入了全新的“自适应候选者扩展”机制。在估算排序后，协议不再是简单地选取
        固定数量的Top-K候选者，而是自动将所有计数值与最高值相近（在一个动态阈值内）
        的候选者全部纳入最终验证环节。随后，协议使用父代B中鲁棒的“动态帧调整”
        方法对这个扩展后的候选集进行最终验证，确保在高冲突和计数值相近的严苛
        条件下，依然能够准确地找出所有并列的众数。
    """

    def _run_estimation_phase(
        self,
        tags: List[Tag],
        target_prices: List[int],
        frame_size: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], int, int, int]:
        """
        一个通用的估算阶段执行函数 (源自父代A的逻辑)。
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

            reader_bits += (seed.bit_length() + frame_size.bit_length() + num_prices_in_batch.bit_length())

            physical_frame = Frame(num_slots=frame_size, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            
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
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 阶段1: 过滤
        FILTER_FRAME_SIZE = 512
        NUM_HASH_FUNCTIONS = 2
        # 阶段2: 估算
        ESTIMATE_FRAME_SIZE = 1024
        ESTIMATE_BATCH_SIZE = 64
        # 阶段3: 验证 (突变)
        VERIFY_FRAME_SIZE_INITIAL = 128
        VERIFY_FRAME_SIZE_MAX = 4096
        SATURATION_THRESHOLD = 0.9
        CANDIDATE_EXPANSION_THRESHOLD = 0.9 # 低于最大计数值90%的候选者将被纳入最终验证

        # ==================================================================
        #  阶段一: 多哈希过滤 (Crossover - 融合父代B)
        # ==================================================================
        # --- 交叉部分：融合父代B的“多哈希/布隆过滤”思想，快速筛选候选价格。---
        # 此阶段高效地筛除大量无关价格，为下一阶段的精确估算奠定基础。
        filter_seeds = [generate_random_seed() for _ in range(NUM_HASH_FUNCTIONS)]
        bits_sent = FILTER_FRAME_SIZE.bit_length()
        for seed in filter_seeds:
            bits_sent += seed.bit_length()
        total_bits_sent_by_reader += bits_sent
        total_frames += NUM_HASH_FUNCTIONS

        filter_frames_states = [[0] * FILTER_FRAME_SIZE for _ in range(NUM_HASH_FUNCTIONS)]
        for t in tags:
            for i in range(NUM_HASH_FUNCTIONS):
                slot_id = simple_hash(t.price, filter_seeds[i]) % FILTER_FRAME_SIZE
                filter_frames_states[i][slot_id] = 1
        total_tag_bits_sent += FILTER_FRAME_SIZE * 1 * NUM_HASH_FUNCTIONS

        candidate_prices = []
        for p in range(price_min, price_max + 1):
            is_candidate = all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % FILTER_FRAME_SIZE] == 1
                for i in range(NUM_HASH_FUNCTIONS)
            )
            if is_candidate:
                candidate_prices.append(p)
        
        if not candidate_prices:
            return [], 0, 0.0, {"message": "No candidates found in filter stage"}

        # ==================================================================
        #  阶段二: 批量估算排序 (Crossover - 融合父代A)
        # ==================================================================
        # --- 交叉部分：融合父代A的“并行批量估算”思想，对候选者进行定量排序。---
        # 此阶段使用父代A的零时隙估算公式，为过滤后的候选集提供精确的计数值，
        # 这比父代B的非空时隙数评分更能区分计数值相近的候选者。
        estimated_counts, frames, reader_bits, tag_bits = self._run_estimation_phase(
            tags, candidate_prices, ESTIMATE_FRAME_SIZE, ESTIMATE_BATCH_SIZE
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_tag_bits_sent += tag_bits

        sorted_estimates = sorted(
            estimated_counts.items(),
            key=lambda item: (item[1] == float('inf'), item[1]),
            reverse=True
        )

        if not sorted_estimates:
            return [], 0, 0.0, {"message": "No candidates survived estimation stage"}

        # ==================================================================
        #  阶段三: 自适应验证 (Mutation)
        # ==================================================================
        # --- 突变部分：引入“自适应候选者扩展”全新机制 ---
        # 此机制在两个父代协议中均未出现。它不再局限于固定的Top-K选择，
        # 而是动态地将在计数值上与最强者“旗鼓相当”的所有候选者都纳入最终决选圈。
        # 这极大地提升了协议在面对多个频率相近的“热门”价格时，找全所有众数的能力。
        final_candidate_prices = []
        max_estimated_count = sorted_estimates[0][1]
        
        if max_estimated_count > 0 and max_estimated_count != float('inf'):
            # 基于阈值进行动态扩展
            for price, count in sorted_estimates:
                if count >= max_estimated_count * CANDIDATE_EXPANSION_THRESHOLD:
                    final_candidate_prices.append(price)
                else:
                    # 由于列表已排序，后续的计数值只会更小，可以提前终止
                    break
        elif max_estimated_count == float('inf'):
            # 如果估算值为无穷大，则所有无穷大的都进入验证
             final_candidate_prices = [p for p, c in sorted_estimates if c == float('inf')]
        elif sorted_estimates:
             final_candidate_prices = [sorted_estimates[0][0]] # 至少选择一个
        
        if not final_candidate_prices:
             return [], 0, 0.0, {"message": "Final candidate selection failed"}


        # 使用父代B的动态帧验证机制对扩展后的候选集进行精确验证
        final_scores = {}
        for price in final_candidate_prices:
            current_frame_size = VERIFY_FRAME_SIZE_INITIAL
            is_verified = False
            num_non_empty_final = 0
            
            while not is_verified and current_frame_size <= VERIFY_FRAME_SIZE_MAX:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + current_frame_size.bit_length() + seed.bit_length())

                verification_slots = [0] * current_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % current_frame_size
                        verification_slots[slot_id] = 1
                total_tag_bits_sent += current_frame_size * 1

                num_non_empty = sum(verification_slots)
                num_non_empty_final = num_non_empty
                
                # 帧饱和检测 (源自父代B)
                if num_non_empty / current_frame_size > SATURATION_THRESHOLD and current_frame_size < VERIFY_FRAME_SIZE_MAX:
                    current_frame_size *= 2
                else:
                    final_scores[price] = num_non_empty
                    is_verified = True
            
            if not is_verified:
                final_scores[price] = num_non_empty_final

        # --- 结果汇总与时间计算 ---
        if not final_scores:
            return [], 0, 0.0, {"message": "Final verification failed"}

        max_score = max(final_scores.values()) if final_scores else 0
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "num_filter_candidates": len(candidate_prices),
            "estimation_stage_results": sorted_estimates[:len(final_candidate_prices)+2],
            "final_verification_candidates": final_candidate_prices,
            "final_verification_scores": final_scores,
            "total_frames": total_frames,
        }

        return modes, max_score, total_time_ms, verification_data
