import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g6_1(IProtocol):
    """
    子代协议: 融合批处理验证的自适应RFID众数查询协议
    (Adaptive RFID Mode Query Protocol with Batched Verification)

    本协议是一个全新的子代协议，旨在解决复杂的多众数查询问题。
    它通过深度融合两个父代协议的优点，并引入创新的“批处理验证”机制，
    实现了在保证高准确率的同时，对通信开销进行优化。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0 # 用于计算 T_tag

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 40      # 当候选者数量小于此值时，停止过滤
        max_refinement_rounds = 5     # 最大过滤轮次
        high_density_threshold = 0.75 # 帧密度过高阈值
        low_density_threshold = 0.25  # 帧密度过低阈值
        min_frame_size = 64

        # 阶段二: 验证相关
        verification_frame_size = 128
        # --- 突变部分：引入批处理验证机制 ---
        batch_size_threshold = 32     # 验证阶段中，单个批次处理的最大候选者数量

        # ==================================================================
        #  阶段一: 基于碰撞率的自适应迭代过滤
        # --- 交叉部分：融合父代A的自适应帧长调整思想与父代B的多哈希过滤流程 ---
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        refinement_rounds_done = 0
        current_filter_frame_size = initial_filter_frame_size

        while len(candidate_prices) > candidate_threshold and refinement_rounds_done < max_refinement_rounds:
            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + filter_seeds[0].bit_length())

            # --- [物理层模拟] ---
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            # 在过滤阶段，标签只回复1比特（存在性）
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # 读写器根据聚合信号（帧状态）筛选候选者
            new_candidates = {p for p in candidate_prices if all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % current_filter_frame_size] > 0
                for i in range(num_hash_functions)
            )}
            
            # 如果没有候选者幸存，则提前终止以防万一
            if not new_candidates:
                break
            candidate_prices = new_candidates
            refinement_rounds_done += 1

            # 读写器分析帧密度以自适应调整帧长
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / (num_hash_functions * current_filter_frame_size)

            if density > high_density_threshold:
                current_filter_frame_size *= 2
            elif density < low_density_threshold:
                new_size = current_filter_frame_size // 2
                current_filter_frame_size = max(min_frame_size, new_size)

        # ==================================================================
        #  阶段二: 并行位图验证
        # --- 突变部分：引入批处理（Batching）全新机制以优化标签响应负载 ---
        # --- 交叉部分：该验证方法的核心逻辑继承自父代B的并行位图思想 ---
        # ==================================================================
        final_scores: Dict[int, int] = {}
        
        if candidate_prices:
            candidate_list = sorted(list(candidate_prices))
            num_candidates = len(candidate_list)
            
            # 根据候选者数量决定是否分批处理
            num_batches = math.ceil(num_candidates / batch_size_threshold)

            for i in range(num_batches):
                # 提取当前批次的候选价格
                batch_candidates = candidate_list[i * batch_size_threshold : (i + 1) * batch_size_threshold]
                if not batch_candidates: continue

                total_frames += 1
                price_to_bit_pos_map = {price: j for j, price in enumerate(batch_candidates)}
                bits_per_slot = len(batch_candidates)

                seed_verify = generate_random_seed()
                # 读写器广播验证参数：帧长、种子、以及当前批次的候选价格列表
                total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                              seed_verify.bit_length() +
                                              sum(p.bit_length() for p in batch_candidates))

                # --- [物理层模拟] ---
                verification_frame_slots = [0] * verification_frame_size
                for t in tags:
                    if t.price in price_to_bit_pos_map:
                        slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                        bit_pos = price_to_bit_pos_map[t.price]
                        verification_frame_slots[slot_id] |= (1 << bit_pos)
                
                # 累加当前批次标签响应的总比特数
                total_tag_response_bits += verification_frame_size * bits_per_slot
                # --- [物理层模拟结束] ---
                
                # 读写器分析聚合信号，计算当前批次中每个价格的得分
                for price, bit_pos in price_to_bit_pos_map.items():
                    score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                    if score > 0:
                        # 累加得分（尽管在此设计中每个价格只会出现一次，但为保持通用性）
                        final_scores[price] = final_scores.get(price, 0) + score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        # 根据题目公式，重新定义T_tag的计算方式
        # T_tag (ms) = total_slots × bits_per_slot × 0.4
        # 由于本协议中 bits_per_slot 是变化的，我们将 total_slots * bits_per_slot 替换为 total_tag_response_bits
        
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "refinement_rounds_done": refinement_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "verification_batches": num_batches if candidate_prices else 0,
            "batch_size_threshold": batch_size_threshold,
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, verification_data
