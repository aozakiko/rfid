import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g3_1(IProtocol):
    """
    动态自适应过滤协议 (Dynamic Adaptive Filtering Protocol, DAFP)

    本协议是一个为解决复杂多众数问题而设计的全新子代协议。
    它严格遵循交叉与突变的设计原则，深度融合了两个父代协议的优点，
    并引入了全新的动态优化机制以保证在高冲突环境下的准确性。

    协议流程分为三个核心阶段：
    1.  **多哈希过滤阶段 (Crossover)**:
        融合了父代B的“多哈希布隆过滤”思想。此阶段使用多个哈希函数快速扫描整个价格
        范围，构建出一个“价格活动位图”。这种方法能以极高的效率和较低的伪正例率筛除
        大量不存在或低频的价格，为后续阶段生成一个高质量的候选价格集。

    2.  **批量评分排序阶段 (Crossover)**:
        融合了父代A的“并行批量估算”架构与父代B的“非空时隙评分”机制。协议不再使用
        父代A中对帧大小敏感的对数估算，而是对候选者分批处理，并采用更鲁棒的非空时隙
        数作为评分标准。这结合了父代A的并行效率与父代B的评分稳定性。

    3.  **动态验证阶段 (Mutation)**:
        引入了全新的“帧饱和度检测与动态尺寸调整”机制。在对最终候选者进行精确验证时，
        如果协议检测到由于标签数量过多导致通信帧接近饱和（高冲突），它将自动加倍帧长
        并重试。此机制确保了即使面对数量极高的众数，协议也能动态调整通信资源，避免
        因数据饱和导致误判，从而获得准确的最终评分。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_slots = 0 # 记录标签响应的总时隙数

        # --- 协议超参数 ---
        # 阶段1: 过滤
        FILTER_FRAME_SIZE = 512
        NUM_HASH_FUNCTIONS = 2
        # 阶段2: 评分
        SCORE_FRAME_SIZE = 256
        SCORE_BATCH_SIZE = 64
        FINAL_CANDIDATE_SIZE = 10
        # 阶段3: 验证 (突变)
        VERIFY_FRAME_SIZE_INITIAL = 128
        VERIFY_FRAME_SIZE_MAX = 2048
        SATURATION_THRESHOLD = 0.9 # 帧饱和度阈值

        # ==================================================================
        #  阶段一: 多哈希过滤 (源自父代B)
        # ==================================================================
        # --- 交叉部分：融合父代B的“多哈希/布隆过滤”思想，快速筛选候选价格。---
        # 此阶段直接采纳父代B高效的候选者筛选机制，作为整个协议的第一道防线。
        filter_seeds = [generate_random_seed() for _ in range(NUM_HASH_FUNCTIONS)]
        bits_sent = FILTER_FRAME_SIZE.bit_length()
        for seed in filter_seeds:
            bits_sent += seed.bit_length()
        total_bits_sent_by_reader += bits_sent
        total_frames += NUM_HASH_FUNCTIONS # 概念上每个哈希函数对应一个帧

        # [物理层模拟]
        filter_frames_states = [[0] * FILTER_FRAME_SIZE for _ in range(NUM_HASH_FUNCTIONS)]
        for t in tags:
            for i in range(NUM_HASH_FUNCTIONS):
                slot_id = simple_hash(t.price, filter_seeds[i]) % FILTER_FRAME_SIZE
                filter_frames_states[i][slot_id] = 1 # 仅标记为非空即可
        total_tag_slots += FILTER_FRAME_SIZE * NUM_HASH_FUNCTIONS
        # [物理层模拟结束]

        # 读写器本地分析，生成候选价格列表
        candidate_prices = []
        for p in range(price_min, price_max + 1):
            is_candidate = True
            for i in range(NUM_HASH_FUNCTIONS):
                slot_p = simple_hash(p, filter_seeds[i]) % FILTER_FRAME_SIZE
                if filter_frames_states[i][slot_p] == 0:
                    is_candidate = False
                    break
            if is_candidate:
                candidate_prices.append(p)
        
        if not candidate_prices:
            return [], 0, 0.0, {"message": "No candidates found in filter stage"}

        # ==================================================================
        #  阶段二: 批量评分排序 (融合父代A和B)
        # ==================================================================
        # --- 交叉部分：融合父代A的“并行批量处理”思想与父代B的“非空时隙评分”机制。---
        # 本阶段将第一阶段筛选出的高质量候选者进行分批，在每个批次内并行评分，
        # 体现了父代A的效率。评分标准采用父代B更稳定的非空时隙计数法，避免了
        # 父代A中对数估算在帧长不匹配时可能出现的较大误差。
        candidate_scores = {}
        price_batches = [candidate_prices[i:i + SCORE_BATCH_SIZE] for i in range(0, len(candidate_prices), SCORE_BATCH_SIZE)]

        for batch in price_batches:
            if not batch: continue

            total_frames += 1
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            num_prices_in_batch = len(batch)
            
            total_bits_sent_by_reader += (seed.bit_length() + SCORE_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())

            # [物理层模拟]
            physical_frame = Frame(num_slots=SCORE_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % SCORE_FRAME_SIZE
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            total_tag_slots += SCORE_FRAME_SIZE * num_prices_in_batch
            # [物理层模拟结束]

            # 读写器分析帧，为批次内每个价格计算非空时隙数作为分数
            for price, bit_pos in price_to_bit_pos_map.items():
                num_non_empty = sum(1 for slot_data in physical_frame.slots if ((slot_data >> bit_pos) & 1))
                if num_non_empty > 0:
                    candidate_scores[price] = num_non_empty
        
        sorted_scores = sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)
        final_candidate_prices = [price for price, score in sorted_scores[:FINAL_CANDIDATE_SIZE]]

        if not final_candidate_prices:
            return [], 0, 0.0, {"message": "No candidates survived scoring stage"}

        # ==================================================================
        #  阶段三: 动态验证 (突变)
        # ==================================================================
        final_scores = {}
        for price in final_candidate_prices:
            current_frame_size = VERIFY_FRAME_SIZE_INITIAL
            is_verified = False
            while not is_verified and current_frame_size <= VERIFY_FRAME_SIZE_MAX:
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + current_frame_size.bit_length() + seed.bit_length())

                # [物理层模拟]
                verification_slots = [0] * current_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % current_frame_size
                        verification_slots[slot_id] = 1
                total_tag_slots += current_frame_size * 1 # bits_per_slot is 1
                # [物理层模拟结束]

                num_non_empty = sum(verification_slots)

                # --- 突变部分：引入“帧饱和检测与动态尺寸调整”全新机制 ---
                # 此机制在两个父代协议中均未出现。它通过监控帧的填充率（饱和度），
                # 主动识别并应对高密度标签导致的通信冲突。一旦检测到饱和风险，
                # 协议将自动增加帧长（通信资源）并进行重试，直至获得一个可靠的
                # 评分。这极大地增强了协议在寻找高频众数时的鲁棒性和准确性。
                if num_non_empty / current_frame_size > SATURATION_THRESHOLD and current_frame_size < VERIFY_FRAME_SIZE_MAX:
                    current_frame_size *= 2 # 帧饱和，加倍帧长重试
                else:
                    final_scores[price] = num_non_empty # 评分有效
                    is_verified = True

            if not is_verified: # 如果达到最大帧长仍然饱和，则记录下饱和时的分数
                final_scores[price] = num_non_empty

        # --- 结果汇总与时间计算 ---
        if not final_scores:
            return [], 0, 0.0, {"message": "Final verification failed"}

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        T_reader = total_bits_sent_by_reader * 0.025
        # 标签的总比特数 = 总响应时隙数 * 每个时隙的比特数 (在此协议中为1)
        T_tag = total_tag_slots * 1 * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "num_initial_candidates": len(candidate_prices),
            "scoring_stage_results": sorted_scores[:FINAL_CANDIDATE_SIZE],
            "final_verification_scores": final_scores,
            "total_frames": total_frames,
        }

        return modes, max_score, total_time_ms, verification_data
