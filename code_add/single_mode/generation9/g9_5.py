import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_5(IProtocol):
    """
    混合过滤与碰撞恢复重探协议 (Hybrid Filtering with Collision Recovery Re-probe Protocol)

    本协议是一个全新的子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，通过一个创新的多阶段框架，深度融合了两个父代协议的核心思想，
    并引入了全新的“碰撞恢复重探”机制以提升在高密度冲突区域的准确率。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        """
        执行协议的核心逻辑。
        """
        # --- 时间计算与统计变量 ---
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0
        bits_per_slot = 1 # 标签每次只回复1比特表示存在性

        # --- 协议超参数 ---
        FILTER_FRAME_SIZE_1 = 1024 # 过滤阶段哈希帧1大小
        FILTER_FRAME_SIZE_2 = 1024 # 过滤阶段哈希帧2大小
        INITIAL_SCORING_FRAME_SIZE = 128 # 评分阶段初始帧大小
        MIN_FRAME_SIZE = 32          # 帧大小动态调整下限
        MAX_FRAME_SIZE = 2048        # 帧大小动态调整上限
        COLLISION_ADJUST_HIGH_THRESHOLD = 0.4 # 用于增大下一帧的碰撞率阈值
        COLLISION_ADJUST_LOW_THRESHOLD = 0.1  # 用于减小下一帧的碰撞率阈值
        REPROBE_THRESHOLD = 0.6               # 触发对当前价格进行“碰撞恢复重探”的超高碰撞率阈值
        REPROBE_MULTIPLIER = 4.0              # 重探时帧大小的放大倍数

        # --- 交叉部分：融合父代B的“双哈希协同过滤”思想与父代A的“单轮精确评分”结构 ---

        # 阶段一：候选价格生成 (源自父代B g7_5 的核心思想)
        # 采用父代B高效的“双哈希协同过滤”策略。读写器进行两次全局价格哈希，一个价格只有在两次探测中
        # 都映射到非空时隙，才被接纳为候选价格。这能生成一个比父代A的范围剪枝更精简、更高质量的候选集。

        # --- 第一次哈希过滤 ---
        total_frames += 1
        filter_seed_1 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_1.bit_length() + FILTER_FRAME_SIZE_1.bit_length()

        # [物理层模拟]
        filtering_slots_1 = [0] * FILTER_FRAME_SIZE_1
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_1) % FILTER_FRAME_SIZE_1
            filtering_slots_1[slot_id] = 1 # 仅标记存在性
        # [物理层模拟结束]
        total_tag_bits_sent += FILTER_FRAME_SIZE_1 * bits_per_slot
        active_slot_indices_1 = {i for i, state in enumerate(filtering_slots_1) if state > 0}

        # --- 第二次哈希过滤 ---
        total_frames += 1
        filter_seed_2 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_2.bit_length() + FILTER_FRAME_SIZE_2.bit_length()

        # [物理层模拟]
        filtering_slots_2 = [0] * FILTER_FRAME_SIZE_2
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_2) % FILTER_FRAME_SIZE_2
            filtering_slots_2[slot_id] = 1 # 仅标记存在性
        # [物理层模拟结束]
        total_tag_bits_sent += FILTER_FRAME_SIZE_2 * bits_per_slot
        active_slot_indices_2 = {i for i, state in enumerate(filtering_slots_2) if state > 0}

        # --- 读写器本地生成最终候选集 ---
        candidate_prices = []
        if active_slot_indices_1 and active_slot_indices_2:
            for p in range(price_min, price_max + 1):
                hash1_in_set = (simple_hash(p, filter_seed_1) % FILTER_FRAME_SIZE_1) in active_slot_indices_1
                hash2_in_set = (simple_hash(p, filter_seed_2) % FILTER_FRAME_SIZE_2) in active_slot_indices_2
                if hash1_in_set and hash2_in_set:
                    candidate_prices.append(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = (total_tag_bits_sent) * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates after dual-hash filtering"}

        # 阶段二：精确评分 (源自父代A g2_1 的核心思想)
        # 对精炼后的候选集，采用父代A鲁棒且高效的“单轮精确评分”框架。
        # 逐一探测每个候选价格，使用“非空时隙数”作为评分标准。
        # 同时，也继承了两个父代共有的“基于碰撞率动态调整下一帧大小”的优秀策略。

        price_to_score_map = {}
        current_frame_size = INITIAL_SCORING_FRAME_SIZE
        reprobe_count = 0

        for price in sorted(candidate_prices):
            # --- 突变部分：引入“碰撞恢复重探”全新机制 ---
            # 在两个父代协议中，如果一次探测发生严重碰撞，它们只会根据这个（可能不准确的）结果去调整 *下一个* 价格的帧大小。
            # 本协议引入一项全新机制：如果当前价格的碰撞率超过一个极高的阈值(REPROBE_THRESHOLD)，
            # 协议会判定当前评分结果因饱和而不可信。它会立即使用一个更大的帧，对 *同一个价格* 发起一次“恢复性重探”。
            # 最终将采用这次更精确的重探结果作为该价格的最终得分。
            # 这项突变直接解决了父代协议在面对真正众数时可能因得分饱和而被错误超越的问题，显著提升了准确性。

            # --- 第一次探测 ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed.bit_length()

            # [物理层模拟]
            slot_states = [0] * current_frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % current_frame_size
                    slot_states[slot_id] += 1
            # [物理层模拟结束]
            total_tag_bits_sent += current_frame_size * bits_per_slot

            num_non_empty_slots = sum(1 for count in slot_states if count > 0)
            num_collisions = sum(1 for count in slot_states if count > 1)
            collision_rate = num_collisions / current_frame_size if current_frame_size > 0 else 0

            final_score = num_non_empty_slots
            final_collision_rate_for_adjustment = collision_rate

            # --- 执行突变逻辑 ---
            if collision_rate > REPROBE_THRESHOLD:
                reprobe_count += 1
                reprobe_frame_size = min(MAX_FRAME_SIZE, int(current_frame_size * REPROBE_MULTIPLIER))

                # 发起恢复性重探
                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + reprobe_frame_size.bit_length() + seed.bit_length()

                # [物理层模拟]
                slot_states_reprobe = [0] * reprobe_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % reprobe_frame_size
                        slot_states_reprobe[slot_id] += 1
                # [物理层模拟结束]
                total_tag_bits_sent += reprobe_frame_size * bits_per_slot

                # 使用重探结果更新分数和用于调整的碰撞率
                final_score = sum(1 for count in slot_states_reprobe if count > 0)
                num_collisions_reprobe = sum(1 for count in slot_states_reprobe if count > 1)
                final_collision_rate_for_adjustment = num_collisions_reprobe / reprobe_frame_size if reprobe_frame_size > 0 else 0


            price_to_score_map[price] = final_score

            # 基于最终探测结果，为下一个价格动态调整帧大小 (继承自父代A和B的共有思想)
            if final_collision_rate_for_adjustment > COLLISION_ADJUST_HIGH_THRESHOLD:
                current_frame_size = min(MAX_FRAME_SIZE, int(current_frame_size * 1.5))
            elif final_collision_rate_for_adjustment < COLLISION_ADJUST_LOW_THRESHOLD:
                current_frame_size = max(MIN_FRAME_SIZE, int(current_frame_size * 0.75))
            # 否则保持当前帧大小不变


        # --- 最终处理 (Post-processing) ---
        total_slots = total_tag_bits_sent / bits_per_slot
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Fused B's dual-hash filtering with A's single-pass scoring structure.",
            "mutation_info": "Introduced Collision Recovery Re-probe for high-conflict prices.",
            "initial_candidate_count": len(candidate_prices),
            "reprobe_activation_count": reprobe_count,
            "final_scores": price_to_score_map,
        }

        if not price_to_score_map:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = max(price_to_score_map.values())
        if max_score == 0:
             return [], 0, total_time_ms, verification_data

        modes = sorted([
            price for price, score in price_to_score_map.items()
            if score == max_score
        ])

        return modes, max_score, total_time_ms, verification_data
