import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g8_5(IProtocol):
    """
    子代协议: 混合自适应决策协议 (Hybrid Adaptive Decision Protocol, HADP)

    本协议为一个全新的子代协议，旨在高效且准确地解决具有挑战性的多众数查询问题。
    它不仅深度融合了两个父代协议的精华，还引入了一项全新的动态决策机制作为其核心突变。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0  # 用于精确计算 T_tag，应对变化的 bits_per_slot

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        candidate_threshold = 35      # 当候选者数量小于此值时，停止过滤
        max_refinement_rounds = 6     # 最大过滤轮次
        high_density_threshold = 0.8  # 帧密度过高阈值
        low_density_threshold = 0.2   # 帧密度过低阈值
        min_frame_size = 64

        # 阶段二: 验证相关 (继承自父代)
        one_by_one_verification_frame_size = 64
        batch_verification_frame_size = 128
        batch_size_threshold = 32     # 批处理验证中，单个批次的最大候选者数量

        # --- 突变部分：引入混合验证决策的全新机制 ---
        # 引入一个全新的决策阈值。协议将根据过滤后剩余的候选者数量与此阈值的比较，
        # 动态地选择执行父代A的高效“批处理验证”或父代B的稳健“独立精确验证”。
        # 这种决策能力在两个父代中均未出现。
        hybrid_verification_threshold = 8 # 若候选者少于等于8个，则采用更直接的独立验证

        # ==================================================================
        #  阶段一: 自适应多哈希迭代过滤
        # --- 交叉部分：融合父代A的自适应帧长调整思想与父代B的多哈希迭代过滤流程 ---
        # 本阶段完美结合了两个父代协议在过滤阶段的核心优势：
        # 1. 采用多哈希函数（num_hash_functions > 1）并行执行，以高效筛除大量无关价格，这是父代A和B的共同策略。
        # 2. 在每一轮迭代后，读写器会分析帧的“密度”（非空时隙占比），并动态调整下一轮的帧长（frame_size）。
        #    如果密度过高（碰撞严重），则增大帧长；如果密度过低（资源浪费），则减小帧长。
        #    这一自适应调整逻辑是两个父代协议性能的关键，本协议将其完整继承并融合。
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
            # 在过滤阶段，标签只回复1比特
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # 读写器根据聚合信号（帧状态）筛选候选者
            new_candidates = {p for p in candidate_prices if all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % current_filter_frame_size] > 0
                for i in range(num_hash_functions)
            )}

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
        #  阶段二: 混合验证决策与执行
        # ==================================================================
        final_scores: Dict[int, int] = {}
        verification_method_used = "None"

        if not candidate_prices:
            # 如果没有候选者，直接结束
            pass
        # --- 突变部分：引入基于候选者数量的混合验证决策机制 ---
        # 这是本协议的核心创新。在过滤阶段结束后，读写器会检查剩余候选者的数量。
        # 1. 若数量 > hybrid_verification_threshold，协议判定采用父代A的“批处理位图验证”更为高效。
        # 2. 若数量 <= hybrid_verification_threshold，协议判定候选者已足够少，采用父代B的“独立精确验证”开销更低。
        # 这种根据中间状态动态选择后续最优策略的能力，是两个父代协议都不具备的全新智能优化。
        elif len(candidate_prices) > hybrid_verification_threshold:
            # --- 交叉部分：执行继承自父代A的高效“批处理位图验证” ---
            verification_method_used = f"Batched Bitmap (from Parent A, {len(candidate_prices)} candidates)"
            candidate_list = sorted(list(candidate_prices))
            num_batches = math.ceil(len(candidate_list) / batch_size_threshold)

            for i in range(num_batches):
                batch_candidates = candidate_list[i * batch_size_threshold : (i + 1) * batch_size_threshold]
                if not batch_candidates: continue

                total_frames += 1
                price_to_bit_pos_map = {price: j for j, price in enumerate(batch_candidates)}
                bits_per_slot_in_batch = len(batch_candidates)
                seed_verify = generate_random_seed()

                total_bits_sent_by_reader += (batch_verification_frame_size.bit_length() +
                                              seed_verify.bit_length() +
                                              sum(p.bit_length() for p in batch_candidates))

                # --- [物理层模拟] ---
                verification_frame_slots = [0] * batch_verification_frame_size
                for t in tags:
                    if t.price in price_to_bit_pos_map:
                        slot_id = simple_hash(t.id, seed_verify) % batch_verification_frame_size
                        bit_pos = price_to_bit_pos_map[t.price]
                        verification_frame_slots[slot_id] |= (1 << bit_pos)
                total_tag_response_bits += batch_verification_frame_size * bits_per_slot_in_batch
                # --- [物理层模拟结束] ---

                for price, bit_pos in price_to_bit_pos_map.items():
                    score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                    if score > 0:
                        final_scores[price] = final_scores.get(price, 0) + score
        else:
            # --- 交叉部分：执行继承自父代B的稳健“独立精确验证” ---
            verification_method_used = f"One-by-One (from Parent B, {len(candidate_prices)} candidates)"
            for p in sorted(list(candidate_prices)):
                total_frames += 1
                seed_verify = generate_random_seed()
                total_bits_sent_by_reader += p.bit_length() + one_by_one_verification_frame_size.bit_length() + seed_verify.bit_length()

                # --- [物理层模拟] ---
                verification_slot_states = [0] * one_by_one_verification_frame_size
                for t in tags:
                    if t.price == p:
                        slot_id = simple_hash(t.id, seed_verify) % one_by_one_verification_frame_size
                        verification_slot_states[slot_id] += 1
                # 在独立验证中，每个时隙标签只回复1比特
                total_tag_response_bits += one_by_one_verification_frame_size * 1
                # --- [物理层模拟结束] ---

                num_non_empty = sum(1 for count in verification_slot_states if count > 0)
                if num_non_empty > 0:
                    final_scores[p] = num_non_empty

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        # 准备详细的调试和分析数据
        verification_data = {
            "refinement_rounds_done": refinement_rounds_done,
            "num_candidates_after_filter": len(candidate_prices),
            "hybrid_verification_threshold": hybrid_verification_threshold,
            "verification_method_used": verification_method_used,
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, verification_data

        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, verification_data
