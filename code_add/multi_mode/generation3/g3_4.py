import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g3_4(IProtocol):
    """
    子代协议: 基于分层估算与自适应验证的多众数查询协议
    (Multi-Mode Query Protocol via Hierarchical Estimation and Adaptive Verification)

    本协议为解决具有挑战性的多众数查询问题而设计，尤其适用于多个众数值频率相近的场景。
    它通过一个创新的三阶段流程，深度融合了父代协议的优点并引入了全新的优化机制。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 阶段一: 粗粒度并行估算
        coarse_estimation_batch_size = 128
        coarse_estimation_frame_size = 512
        max_candidates_for_refinement = 40 # 筛选多少候选者进入下一阶段

        # 阶段二: 细粒度独立估算
        fine_estimation_frame_size = 256

        # 阶段三: 精确验证
        verification_bits_per_reply = 32
        max_candidates_to_verify = 20
        verification_pruning_factor = 0.9 # 用于剪枝的容忍因子

        # =================================================================================
        # --- 交叉部分：融合父代A的独立估算思想与父代B的并行估算思想 ---
        # 创建一个全新的“分层估算”框架：
        # 1. 首先采用父代B高效的并行SEM估算进行“广撒网”式的快速筛选，剔除大量无关价格。
        # 2. 然后对筛选出的少数候选者，采用父代A那种更精确、无干扰的“一对一”独立估算方法进行细化。
        # 这种“先快后准”的策略结合了双方的优点，形成了全新的、更鲁棒的估算阶段。
        # =================================================================================

        # --- 阶段一: 粗粒度并行估算 (源自父代B的SEM机制) ---
        all_prices = list(range(price_min, price_max + 1))
        price_batches = [all_prices[i:i + coarse_estimation_batch_size] for i in range(0, len(all_prices), coarse_estimation_batch_size)]
        
        coarse_estimated_counts = {}

        for batch in price_batches:
            if not batch: continue
            
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            
            # 读写器广播参数
            total_bits_sent_by_reader += (seed.bit_length() + coarse_estimation_frame_size.bit_length() + num_prices_in_batch.bit_length())

            # --- [物理层模拟] ---
            physical_frame = Frame(num_slots=coarse_estimation_frame_size, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            # 每个标签根据广播指令和自身数据独立做出响应
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % coarse_estimation_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += coarse_estimation_frame_size * num_prices_in_batch
            
            # --- 读写器分析聚合的Frame ---
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = coarse_estimation_frame_size - sum(logical_bit_vector)
                
                if num_zeros == 0:
                    coarse_estimated_counts[price] = float('inf')
                elif num_zeros == coarse_estimation_frame_size:
                    coarse_estimated_counts[price] = 0
                else:
                    coarse_estimated_counts[price] = -coarse_estimation_frame_size * math.log(num_zeros / coarse_estimation_frame_size)

        # --- 筛选候选者进入精细估算阶段 ---
        sorted_coarse_candidates = sorted(
            coarse_estimated_counts.items(), 
            key=lambda item: (item[1] == float('inf'), item[1]), 
            reverse=True
        )
        
        candidates_for_refinement = sorted_coarse_candidates[:max_candidates_for_refinement]
        
        # --- 阶段二: 细粒度独立估算 (源自父代A的逐一处理思想) ---
        refined_estimated_counts = {}
        for price, _ in candidates_for_refinement:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + fine_estimation_frame_size.bit_length() + price.bit_length())

            # --- [物理层模拟] ---
            physical_frame = Frame(num_slots=fine_estimation_frame_size, bits_per_slot=1)
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % fine_estimation_frame_size
                    physical_frame.aggregate(slot_id, 1)
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += fine_estimation_frame_size * 1

            # --- 读写器分析聚合的Frame ---
            num_zeros = physical_frame.slots.count(0)
            
            if num_zeros == 0:
                refined_estimated_counts[price] = fine_estimation_frame_size * 2.5 
            elif num_zeros == fine_estimation_frame_size:
                 refined_estimated_counts[price] = 0
            else:
                refined_estimated_counts[price] = -fine_estimation_frame_size * math.log(num_zeros / fine_estimation_frame_size)

        # --- 筛选最终候选者进行精确验证 ---
        sorted_refined_candidates = sorted(
            refined_estimated_counts.items(), 
            key=lambda item: item[1], 
            reverse=True
        )
        candidates_to_verify = sorted_refined_candidates[:max_candidates_to_verify]
        
        # --- 阶段三: 精确验证 ---
        exact_counts = {}
        current_max_verified_count = 0

        for price, refined_estimate in candidates_to_verify:
            # --- 对父代B剪枝逻辑的交叉改进 ---
            # 引入容忍因子，以应对多众数频率相近的情况，防止过早剪掉真众数
            if refined_estimate < current_max_verified_count * verification_pruning_factor:
                break
            
            identified_tag_ids_for_price = set()

            # =================================================================================
            # --- 突变部分：引入“基于估算值的自适应初始帧长”全新机制 ---
            # 父代协议在验证阶段通常使用固定的或粗略的估算值作为初始帧长。
            # 本协议利用阶段二得到的更精确的refined_estimate，直接将其作为ALOHA迭代的
            # 初始帧长。这使得初始资源分配更贴近实际标签数量，从而减少后续动态调整的
            # 次数，加速收敛，尤其对高计数的众数价格效率提升明显。
            # =================================================================================
            current_frame_size = max(16, int(round(refined_estimate)))
            
            # 启动迭代式ALOHA协议 (增加最大迭代次数保护，防止死循环)
            for _ in range(10): 
                total_frames += 1
                seed_s = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed_s.bit_length()

                # --- [物理层模拟] ---
                slot_states = defaultdict(list)
                for t in tags:
                    if t.price == price and t.id not in identified_tag_ids_for_price:
                        slot_id = simple_hash(t.id, seed_s) % current_frame_size
                        slot_states[slot_id].append(t.id)
                # --- [物理层模拟结束] ---
                
                total_tag_bits_sent += current_frame_size * verification_bits_per_reply

                num_collision_slots = 0
                newly_identified_ids = set()
                # 读写器根据收到的匿名聚合信号分析时隙状态
                for _, tag_ids in slot_states.items():
                    if len(tag_ids) == 1:
                        newly_identified_ids.add(tag_ids[0])
                    elif len(tag_ids) > 1:
                        num_collision_slots += 1

                if newly_identified_ids:
                    identified_tag_ids_for_price.update(newly_identified_ids)

                # 终止条件：没有新标签被识别且没有碰撞
                if not newly_identified_ids and num_collision_slots == 0:
                    break
                
                # 帧长自适应调整
                if num_collision_slots > 0:
                    current_frame_size = min(current_frame_size * 2, 4096)
            
            exact_counts[price] = len(identified_tag_ids_for_price)
            if exact_counts[price] > current_max_verified_count:
                current_max_verified_count = exact_counts[price]

        # --- 结果处理与时间计算 ---
        if not exact_counts:
             return [], 0, 0.0, {"status": "No candidates verified"}

        max_count = max(exact_counts.values())
        modes = sorted([p for p, c in exact_counts.items() if c == max_count])

        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        verification_data = {
            "protocol_name": "MultiModeHybridProtocol",
            "phase1_top_candidates": {p: (round(n, 2) if n != float('inf') else 'inf') for p, n in candidates_for_refinement},
            "phase2_refined_estimates": {p: round(n, 2) for p, n in sorted_refined_candidates},
            "phase3_exact_counts": exact_counts,
        }
        
        return modes, max_count, total_time_ms, verification_data
