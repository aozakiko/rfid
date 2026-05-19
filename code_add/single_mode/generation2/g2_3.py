import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g2_3(IProtocol):
    """
    启发式验证众数查询协议 (Heuristic Verification Mode Query)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循了信息隔离原则，并综合了两个父代协议的核心设计思想，同时引入了创新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 估算阶段参数 (源于父代A)
        ESTIMATION_BATCH_SIZE = 100
        ESTIMATION_FRAME_SIZE = 512
        # 验证阶段参数 (源于父代B)
        BITS_PER_SINGULATION_QUERY = 96
        BITS_PER_PRICE_REPLY = 32
        # 突变机制参数
        VERIFICATION_CONFIDENCE_MARGIN = 0.1 # 10% 的置信区间

        # --- 交叉部分：融合父代A的“并行估算”思想与父代B的“精确盘点”思想 ---

        # 阶段一：全局并行估算 (Inherited from Parent A: ModeQuerySEM)
        # 采用父代A的并行SEM估算方法，高效地为价格范围内的所有价格生成一个初步的计数值，
        # 快速筛选出少数几个最有可能的候选者。
        
        estimated_counts = {}
        all_prices = list(range(price_min, price_max + 1))
        price_batches = [all_prices[i:i + ESTIMATION_BATCH_SIZE] for i in range(0, len(all_prices), ESTIMATION_BATCH_SIZE)]

        for batch in price_batches:
            if not batch: continue
            
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            total_bits_sent_by_reader += (seed.bit_length() + ESTIMATION_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())

            # --- [物理层模拟] ---
            physical_frame = Frame(num_slots=ESTIMATION_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % ESTIMATION_FRAME_SIZE
                    reply_vector = 1 << price_to_bit_pos_map[t.price]
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += ESTIMATION_FRAME_SIZE * num_prices_in_batch

            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = ESTIMATION_FRAME_SIZE - sum(logical_bit_vector)
                
                if num_zeros == ESTIMATION_FRAME_SIZE:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    estimated_counts[price] = float('inf')
                else:
                    estimated_counts[price] = -ESTIMATION_FRAME_SIZE * math.log(num_zeros / ESTIMATION_FRAME_SIZE)

        # 阶段二：候选精确验证 (Inspired by Parent B: ModeQueryID)
        # 对估算出的顶尖候选者，借鉴父代B的精确盘点思想，进行逐一的、高成本但高准确度的验证，
        # 从而确定最终的众数。

        # --- 突变部分：引入“基于置信度的提前终止”全新机制 ---
        # 在父代协议中，要么全估算，要么全盘点。本协议引入一个创新：
        # 在验证阶段，我们动态维护一个当前已验证的“最高分”。对于下一个待验证的候选者，
        # 我们首先检查其“估算值”加上一个置信区间后，是否仍然低于当前的“最高分”。
        # 如果是，则我们有理由相信它和它之后的任何候选者都无法超越当前的胜出者，
        # 从而可以安全地“提前终止”验证流程，节省大量通信开销。
        
        sorted_candidates = sorted(estimated_counts.items(), key=lambda item: item[1] if item[1] != float('inf') else float('inf'), reverse=True)

        final_modes = []
        current_max_verified_count = -1
        
        verified_prices = {}

        for candidate_price, estimated_count in sorted_candidates:
            if estimated_count == 0:
                break # 后续估算值均为0，无需再验证

            # --- 提前终止逻辑 (Mutation) ---
            # 如果当前候选者的估算值加上一个置信边际后，仍然小于已验证的最大值，则终止
            margin = estimated_count * VERIFICATION_CONFIDENCE_MARGIN if estimated_count != float('inf') else 0
            if estimated_count != float('inf') and (estimated_count + margin) < current_max_verified_count:
                break

            # --- 执行精确盘点 ---
            # --- [物理层模拟] ---
            tags_with_this_price = [t for t in tags if t.price == candidate_price]
            exact_count = len(tags_with_this_price)
            
            # 计算这次精确盘点的成本，借鉴父代B的模型
            if exact_count > 0:
                total_frames += exact_count
                total_bits_sent_by_reader += exact_count * BITS_PER_SINGULATION_QUERY
                total_tag_bits_sent += exact_count * BITS_PER_PRICE_REPLY
            # --- [物理层模拟结束] ---

            verified_prices[candidate_price] = exact_count

            # 更新当前胜出者
            if exact_count > current_max_verified_count:
                current_max_verified_count = exact_count
                final_modes = [candidate_price]
            elif exact_count == current_max_verified_count:
                final_modes.append(candidate_price)

        # --- 最终处理 (Post-processing) ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "estimated_counts": estimated_counts,
            "prices_verified": verified_prices,
            "final_exact_counts": {p: c for p, c in verified_prices.items() if p in final_modes}
        }
        
        return sorted(final_modes), current_max_verified_count, total_time_ms, verification_data
